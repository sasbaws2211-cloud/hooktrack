"""What's working in your niche: recent YouTube Shorts that did far better than their own channel's normal.

Why "far better than its own normal" and not "most viewed": a big channel's every video has big numbers, which teaches a small
creator nothing. A small channel whose video suddenly did 4x its usual is evidence that something about the video worked, and
the same thing is within reach of a small creator. HookTrack already judges every video that way, so the feed uses the same idea.

How it is built, and why it is cheap:
  * One shared feed per niche, rebuilt every couple of days from YouTube's free official API. Its cost grows with the number of
    niches, not with the number of users.
  * It only runs while plenty of the free daily YouTube allowance is left (RESERVE_UNITS), so it can never starve users' syncs.
  * Only numbers, the video's own title and a link are stored. Nothing is copied or re-hosted; the thumbnail is YouTube's own
    public image address. Rows are deleted after RETENTION_DAYS (YouTube limits how long its data may be kept).
Nothing here predicts anything: it reports what already happened to similar creators.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query

from . import db, leases, logic, peer, youtube_api
from .auth import Ctx, current
from .sociavault import platform_get, platform_set

log = logging.getLogger("hooktrack.discover")
router = APIRouter(prefix="/api/discover", tags=["discover"])

OUTLIER_MULTIPLE = 3.0          # a video counts when it got at least this many times its channel's normal
WINDOW_DAYS = 14                # only videos published within this long
MIN_AGE_DAYS = logic.MATURE_DAYS
MIN_VIEWS = 1000                # smaller numbers are noise
MIN_OTHERS = 8                  # videos behind a channel's "normal"
MIN_MEDIAN = 100
MAX_SUBSCRIBERS = 2_000_000     # past this a creator is no longer "similar" to a beginner
CHANNEL_CAP = 40                # channels judged per refresh
RETENTION_DAYS = 14
REFRESH_EVERY_H = 48
GAP_BETWEEN_RUNS_MIN = 30
RESERVE_UNITS = 3000            # always leave this much of the free daily allowance for users' syncs
EST_UNITS_PER_NICHE = 450
NICHE_TERMS = {
    "comedy": ["comedy skit", "funny video"], "fitness": ["workout routine", "gym tips"],
    "beauty": ["makeup tutorial", "skincare routine"], "food": ["easy recipe", "cooking at home"],
    "tech": ["business tips", "tech review"], "education": ["how to learn", "tutorial"],
    "gaming": ["gameplay", "gaming highlights"], "lifestyle": ["day in my life", "travel vlog"],
    "music": ["cover song", "dance"],
}
# A search term pulls in neighbours (a "workout" search returns makeup routines). A video is kept only if its title or description
# actually mentions its niche, which is crude but cheap and removes most of the off-topic results.
NICHE_WORDS = {
    "comedy": ("comedy", "funny", "skit", "prank", "joke", "meme", "humor", "humour", "parody", "roast"),
    "fitness": ("workout", "gym", "fitness", "exercise", "abs", "cardio", "muscle", "weight", "yoga", "stretch", "trainer",
                "calisthenics", "squat", "pushup", "push-up", "protein", "bodybuilding", "pilates", "mobility", "fat loss"),
    "beauty": ("makeup", "skincare", "beauty", "hair", "nails", "foundation", "lipstick", "serum", "mascara", "grwm", "fashion", "outfit"),
    "food": ("recipe", "cook", "cooking", "food", "meal", "dinner", "lunch", "breakfast", "bake", "baking", "snack", "chef", "kitchen"),
    "tech": ("tech", "business", "startup", "money", "invest", "ai ", "software", "app ", "phone", "gadget", "review", "marketing",
             "entrepreneur", "side hustle", "crypto"),
    "education": ("learn", "tutorial", "how to", "study", "explained", "science", "history", "lesson", "tips", "facts", "language", "english"),
    "gaming": ("game", "gaming", "gameplay", "minecraft", "roblox", "fortnite", "valorant", "gta", "playstation", "xbox", "stream"),
    "lifestyle": ("vlog", "day in my life", "routine", "travel", "home", "family", "apartment", "morning", "weekend", "trip", "diy"),
    "music": ("music", "song", "cover", "dance", "guitar", "piano", "sing", "singing", "beat", "dj", "choreo", "remix"),
}
MAX_PER_CHANNEL = 2             # one channel posting near-copies must not fill the feed
RANK_CAP = 30                   # beyond this, a ratio mostly measures luck (or a tiny "normal"), so it stops adding to the rank
NICHE_LABELS = dict(peer.NICHES)
STATE_KEY = "discover_state"
LEASE = "discover-refresh"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------- reading a hook ----------------

def on_topic(niche: str, title: str, description: str = "") -> bool:
    text = f" {title} {description[:600]} ".lower()
    return any(w in text for w in NICHE_WORDS.get(niche, ()))


def hook_pattern(title: str) -> str:
    """A plain label for the shape of a title, as a hint for what to try. Derived only from the title's own words."""
    t = (title or "").strip()
    low = t.lower()
    if "?" in t or re.match(r"^(why|what|how|who|when|can|do|does|is|are|did)\b", low):
        return "Opens with a question"
    if re.search(r"\b\d+\b", t):
        return "Uses a number"
    if re.match(r"^(how to|learn|try|make|stop|never|don't|dont|you need|you should)\b", low):
        return "A direct how-to or instruction"
    if re.search(r"\b(secret|nobody|no one|never|worst|best|mistake|truth|hack)\b", low):
        return "Makes a bold claim"
    return "Tells a short story or shows a result"


# ---------------- collecting ----------------

def refresh_niche(niche: str, max_units: int = 600) -> dict:
    """Rebuild one niche's feed. Returns counts. Raises nothing for 'nothing found'; YouTube errors propagate."""
    terms = NICHE_TERMS[niche]
    start_units = youtube_api.used_today()
    since = (_now() - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cand: dict = {}
    for term in terms:
        for r in youtube_api.search_recent_shorts(term, since):
            cand.setdefault(r["video_id"], r["channel_id"])
    vids = {v["id"]: v for v in youtube_api.videos(list(cand))} if cand else {}
    cutoff = _now() - timedelta(days=MIN_AGE_DAYS)
    keep = {}
    for vid, v in vids.items():
        when = _parse(v["published"])
        if (when and when <= cutoff and youtube_api.is_short_form(v["duration_s"]) and v["views"] >= MIN_VIEWS
                and v["channel_id"] and on_topic(niche, v["title"], v.get("description", ""))):
            keep[vid] = v
    by_channel: dict = {}
    for v in keep.values():
        by_channel.setdefault(v["channel_id"], []).append(v)
    # judge the channels whose best candidate has the most views first, up to the cap
    order = sorted(by_channel, key=lambda c: -max(x["views"] for x in by_channel[c]))[:CHANNEL_CAP]
    chans = {c["id"]: c for c in youtube_api.channels(order)}
    found = checked = 0
    for cid in order:
        if youtube_api.used_today() - start_units > max_units:
            log.info("discover %s: stopped at the unit budget after %s channels", niche, checked)
            break
        ch = chans.get(cid)
        if not ch or ch["subscribers"] is None or ch["subscribers"] > MAX_SUBSCRIBERS or not ch["uploads_playlist"]:
            continue
        checked += 1
        cand_ids = {x["id"] for x in by_channel[cid]}
        recent = youtube_api.videos(youtube_api.recent_video_ids(ch["uploads_playlist"], 50))
        others = []
        for v in recent:
            when = _parse(v["published"])
            if v["id"] in cand_ids or not when or when > cutoff or not youtube_api.is_short_form(v["duration_s"]):
                continue
            others.append(v["views"])
        if len(others) < MIN_OTHERS:
            continue
        median = logic._median(others)
        if not median or median < MIN_MEDIAN:
            continue
        band = peer.band_for(ch["subscribers"]) or "under-1k"
        wins = sorted(((v["views"] / median, v) for v in by_channel[cid]), key=lambda x: -x[0])
        for multiple, v in [w for w in wins if w[0] >= OUTLIER_MULTIPLE][:MAX_PER_CHANNEL]:
            db.execute(
                """INSERT INTO discover_items(platform, video_id, niche, band, channel_id, channel_title, title, duration_s,
                                              published_at, views, channel_median, multiple, subscribers, collected_at)
                   VALUES ('youtube',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
                   ON CONFLICT (platform, video_id) DO UPDATE SET niche=EXCLUDED.niche, band=EXCLUDED.band, views=EXCLUDED.views,
                     channel_median=EXCLUDED.channel_median, multiple=EXCLUDED.multiple, subscribers=EXCLUDED.subscribers,
                     title=EXCLUDED.title, collected_at=now()""",
                (v["id"], niche, band, cid, v["channel_title"][:120], v["title"][:200], v["duration_s"], _parse(v["published"]),
                 v["views"], round(median), round(multiple, 2), ch["subscribers"]))
            found += 1
    purge()
    return {"niche": niche, "candidates": len(cand), "channels_checked": checked, "outliers": found,
            "units": youtube_api.used_today() - start_units}


def purge() -> int:
    return db.execute("DELETE FROM discover_items WHERE collected_at < now() - make_interval(days => %s) "
                      "OR published_at < now() - make_interval(days => %s)", (RETENTION_DAYS, WINDOW_DAYS * 2))


def refresh_due(force_niche: Optional[str] = None) -> Optional[dict]:
    """Called from the scheduler loop every few minutes. Rebuilds at most one niche per call, only when one is due, only when
    the free daily YouTube allowance has plenty left, and only on one worker at a time."""
    if not youtube_api.available():
        return None
    state = platform_get(STATE_KEY) or {}
    now = _now()
    if not force_niche:
        last_run = _parse(state.get("_last_run", ""))
        if last_run and now - last_run < timedelta(minutes=GAP_BETWEEN_RUNS_MIN):
            return None
        due = []
        for n in NICHE_TERMS:
            t = _parse(state.get(n, ""))
            if not t or now - t > timedelta(hours=REFRESH_EVERY_H):
                due.append((t or datetime.min.replace(tzinfo=timezone.utc), n))
        if not due:
            return None
        niche = sorted(due)[0][1]
    else:
        niche = force_niche
    if youtube_api.budget() - youtube_api.used_today() < RESERVE_UNITS + EST_UNITS_PER_NICHE:
        return None
    with leases.lease(LEASE, ttl=900) as got:
        if not got:
            return None
        try:
            res = refresh_niche(niche)
        except youtube_api.YouTubeError as e:
            log.warning("discover refresh for %s stopped: %s", niche, e)
            state["_last_run"] = now.isoformat()
            platform_set(STATE_KEY, state)
            return None
        state[niche] = now.isoformat()
        state["_last_run"] = now.isoformat()
        platform_set(STATE_KEY, state)
        log.info("discover refreshed: %s", res)
        return res


# ---------------- the feed ----------------

def _mult_label(m: float) -> str:
    """A ratio past 100x says 'a fluke on a tiny normal', not a number anyone can aim for, so it is shown as 100x+."""
    return "100x+" if m >= 100 else f"{round(m, 1):g}x"


def _item(r: dict) -> dict:
    days = max(0, (_now() - r["published_at"]).days)
    return {
        "video_id": r["video_id"], "title": r["title"], "channel": r["channel_title"],
        "url": f"https://www.youtube.com/shorts/{r['video_id']}",
        "thumb": f"https://i.ytimg.com/vi/{r['video_id']}/mqdefault.jpg",
        "views": r["views"], "multiple": round(r["multiple"], 1), "multiple_label": _mult_label(r["multiple"]),
        "channel_median": r["channel_median"],
        "subscribers": r["subscribers"], "band": r["band"], "band_label": peer.BAND_LABELS.get(r["band"], r["band"]),
        "niche": r["niche"], "days_ago": days, "pattern": hook_pattern(r["title"]),
        "note": f"{_mult_label(r['multiple'])} what this channel usually gets ({r['channel_median']:,} views)",
    }


def _suggest(ws: int) -> dict:
    """The niche and size band that fit this workspace's own accounts (YouTube accounts first), or empty strings."""
    row = db.one(
        """SELECT a.niche, (SELECT s.followers FROM account_snapshots s WHERE s.account_id = a.id AND s.followers IS NOT NULL
                           ORDER BY s.day DESC LIMIT 1) AS followers
           FROM accounts a WHERE a.workspace_id = %s AND a.handle NOT LIKE '@demo\\_%%'
           ORDER BY (a.platform ILIKE 'youtube%%') DESC, a.created_at LIMIT 1""", (ws,))
    if not row:
        return {"niche": "", "band": ""}
    n = peer.niche_bucket(row["niche"])
    return {"niche": n if n in NICHE_TERMS else "", "band": peer.band_for(row["followers"]) or ""}


@router.get("")
def feed(niche: str = Query("", max_length=40), band: str = Query("", max_length=20), limit: int = Query(24, ge=1, le=60),
         mine: bool = False, ctx: Ctx = Depends(current)):
    applied = {"niche": "", "band": ""}
    if mine and not niche and not band:
        sug = _suggest(ctx.workspace_id)
        # use the suggestion only if it leaves something to show
        if db.one("SELECT 1 FROM discover_items WHERE (%s = '' OR niche = %s) AND (%s = '' OR band = %s) LIMIT 1",
                  (sug["niche"], sug["niche"], sug["band"], sug["band"])) and (sug["niche"] or sug["band"]):
            niche, band = sug["niche"], sug["band"]
        elif sug["niche"] and db.one("SELECT 1 FROM discover_items WHERE niche = %s LIMIT 1", (sug["niche"],)):
            niche = sug["niche"]
    niche = niche if niche in NICHE_TERMS else ""
    band = band if band in {b[0] for b in peer.BANDS} else ""
    applied = {"niche": niche, "band": band}
    where, args = ["TRUE"], []
    if niche:
        where.append("niche=%s"); args.append(niche)
    if band:
        where.append("band=%s"); args.append(band)
    rows = db.query(f"SELECT * FROM discover_items WHERE {' AND '.join(where)} ORDER BY LEAST(multiple, %s) DESC, subscribers ASC NULLS LAST, views DESC LIMIT %s",
                    (*args[:len(args)], RANK_CAP, limit))
    counts = {r["niche"]: r["n"] for r in db.query("SELECT niche, COUNT(*) AS n FROM discover_items GROUP BY niche")}
    state = platform_get(STATE_KEY) or {}
    return {
        "items": [_item(r) for r in rows],
        "niches": [{"key": k, "label": NICHE_LABELS.get(k, k.title()), "count": counts.get(k, 0), "refreshed": state.get(k)}
                   for k in NICHE_TERMS],
        "bands": [{"key": k, "label": peer.BAND_LABELS[k]} for k, _, _ in peer.BANDS],
        "available": youtube_api.available(), "applied": applied,
        "rule": f"Videos from the last {WINDOW_DAYS} days that got at least {OUTLIER_MULTIPLE:g}x what their own channel usually gets.",
        "caution": "These worked for similar creators. That does not mean they will work for you: try your own version and see.",
    }
