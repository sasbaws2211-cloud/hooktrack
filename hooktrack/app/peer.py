"""Peer benchmarks: what a typical account of a similar size and niche gets, for accounts too new to have a normal of their own.

An account's own median needs at least MIN_BASELINE older posts. Until then a post is compared with the typical account in
its group (platform, format, niche, follower band) instead of being left unjudged. Two sources fill the table:
  * 'seed'  : a sample of public accounts collected with tools/seed_benchmarks.py
  * 'users' : aggregated from HookTrack workspaces; used only for a group with MIN_USERS or more accounts, so nobody can be
              picked out, and preferred over the seed sample once it exists.
Only anonymous aggregates (quartiles of per-account medians) are stored.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from . import db

log = logging.getLogger("hooktrack.peer")

MIN_USERS = 20           # accounts needed before the user-derived figure for a group is used
MIN_SEED = 5             # accounts needed before a seed figure is trusted
MIN_POSTS_PER_ACCOUNT = 5
MAX_POSTS_PER_ACCOUNT = 30

NICHES = [
    ("general", "General"), ("comedy", "Comedy & entertainment"), ("fitness", "Fitness & health"),
    ("beauty", "Beauty & fashion"), ("food", "Food & cooking"), ("tech", "Tech, business & finance"),
    ("education", "Education & how-to"), ("gaming", "Gaming"), ("lifestyle", "Lifestyle & travel"),
    ("music", "Music & dance"),
]
NICHE_KEYS = {k for k, _ in NICHES}
_KEYWORDS = {
    "comedy": ("comedy", "funny", "skit", "meme", "humor", "humour", "entertain", "prank", "reaction"),
    "fitness": ("fitness", "gym", "workout", "health", "yoga", "weight", "wellness", "sport", "running", "nutrition"),
    "beauty": ("beauty", "makeup", "skincare", "fashion", "style", "hair", "outfit", "nails"),
    "food": ("food", "recipe", "cook", "baking", "chef", "restaurant", "kitchen", "meal"),
    "tech": ("tech", "business", "finance", "money", "invest", "crypto", "startup", "marketing", "saas", "coding",
             "software", "entrepreneur"),
    "education": ("education", "learn", "tutorial", "how-to", "how to", "study", "teach", "language", "science", "history", "career"),
    "gaming": ("gaming", "game", "gamer", "minecraft", "fortnite", "esports", "stream"),
    "lifestyle": ("lifestyle", "travel", "vlog", "home", "family", "parenting", "daily", "diy", "pets", "dog", "cat"),
    "music": ("music", "dance", "singer", "song", "cover", "guitar", "piano", "rap"),
}
BANDS = [("under-1k", 0, 1_000), ("1k-10k", 1_000, 10_000), ("10k-100k", 10_000, 100_000), ("100k-plus", 100_000, None)]
BAND_LABELS = {"under-1k": "under 1,000 followers", "1k-10k": "1,000 to 10,000 followers",
               "10k-100k": "10,000 to 100,000 followers", "100k-plus": "over 100,000 followers", "all": "all sizes"}


def niche_bucket(text: str | None) -> str:
    t = (text or "").strip().lower()
    if t in NICHE_KEYS:
        return t
    for key, words in _KEYWORDS.items():
        if any(w in t for w in words):
            return key
    return "general"


def band_for(followers) -> str | None:
    if followers is None:
        return None
    for key, lo, hi in BANDS:
        if followers >= lo and (hi is None or followers < hi):
            return key
    return None


def canonical_platform(name: str | None) -> str:
    n = (name or "").strip().lower()
    return "youtube" if n.startswith("youtube") else "instagram" if n.startswith("insta") else "tiktok"


def quartiles(values: list) -> tuple:
    xs = sorted(values)

    def q(p):
        k = (len(xs) - 1) * p
        f, c = int(k), min(int(k) + 1, len(xs) - 1)
        return xs[f] + (xs[c] - xs[f]) * (k - f)
    return q(0.25), q(0.5), q(0.75)


_cache: dict = {"at": 0.0, "rows": {}}
_TTL = 300


def _rows() -> dict:
    if time.time() - _cache["at"] > _TTL:
        try:
            got = db.query("SELECT platform, fmt, niche, band, source, n_accounts, n_posts, p25, median, p75, updated_at "
                           "FROM peer_benchmarks")
        except Exception:
            log.exception("peer benchmarks could not be read")
            got = []
        _cache["rows"] = {(r["platform"], r["fmt"], r["niche"], r["band"], r["source"]): r for r in got}
        _cache["at"] = time.time()
    return _cache["rows"]


def forget() -> None:
    _cache["at"] = 0.0


def _pick(rows: dict, platform: str, fmt: str, niche: str, band: str):
    users = rows.get((platform, fmt, niche, band, "users"))
    if users and users["n_accounts"] >= MIN_USERS:
        return users
    seed = rows.get((platform, fmt, niche, band, "seed"))
    if seed and seed["n_accounts"] >= MIN_SEED:
        return seed
    return None


def lookup(platform: str | None, fmt: str, niche_text: str | None, followers) -> dict | None:
    """The typical account's normal for this group, or None when nothing trustworthy exists. Never raises."""
    rows = _rows()
    if not rows:
        return None
    plat, niche, band = canonical_platform(platform), niche_bucket(niche_text), band_for(followers)
    # A known follower band is only ever compared with its own band. The all-sizes group is for accounts whose size is
    # unknown: using it for a known beginner would measure them against accounts hundreds of times larger.
    chain = [(niche, band), ("general", band)] if band else [(niche, "all"), ("general", "all")]
    seen = set()
    for n, b in chain:
        if (n, b) in seen:
            continue
        seen.add((n, b))
        hit = _pick(rows, plat, fmt, n, b)
        if hit:
            label = dict(NICHES).get(n, "General").lower()
            size = BAND_LABELS.get(b, b)
            who = f"{label} accounts with {size}" if n != "general" else f"accounts with {size}"
            return {"median": int(hit["median"]), "p25": int(hit["p25"]), "p75": int(hit["p75"]),
                    "n_accounts": hit["n_accounts"], "source": hit["source"], "niche": n, "band": b, "who": who,
                    "exact": (n == niche and b == band)}
    return None


def store(source: str, platform: str, fmt: str, niche: str, band: str, per_account_medians: list, n_posts: int = 0) -> bool:
    """Save one group's aggregate. per_account_medians has one number per account (that account's median views per post)."""
    vals = [v for v in per_account_medians if v is not None and v > 0]
    if not vals:
        return False
    p25, med, p75 = quartiles(vals)
    db.execute(
        """INSERT INTO peer_benchmarks(platform, fmt, niche, band, source, n_accounts, n_posts, p25, median, p75, updated_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
           ON CONFLICT (platform, fmt, niche, band, source) DO UPDATE SET n_accounts=EXCLUDED.n_accounts,
             n_posts=EXCLUDED.n_posts, p25=EXCLUDED.p25, median=EXCLUDED.median, p75=EXCLUDED.p75, updated_at=now()""",
        (platform, fmt, niche, band, source, len(vals), n_posts, round(p25), round(med), round(p75)))
    forget()
    return True


def store_samples(source: str, samples: list) -> int:
    """samples: [{platform, fmt, niche, followers, median_views, n_posts}], one per account. Writes each account into its own
    niche and into 'general', for its follower band and for 'all sizes'. Returns how many aggregates were written."""
    groups: dict = {}
    for s in samples:
        niche = niche_bucket(s.get("niche"))
        band = band_for(s.get("followers"))
        for n in {niche, "general"}:
            for b in ({band} - {None}) | {"all"}:
                g = groups.setdefault((s["platform"], s["fmt"], n, b), [[], 0])
                g[0].append(s["median_views"])
                g[1] += s.get("n_posts") or 0
    wrote = 0
    for (plat, fmt, n, b), (meds, nposts) in groups.items():
        wrote += 1 if store(source, plat, fmt, n, b, meds, nposts) else 0
    return wrote


def _handle_hash(platform: str, handle: str) -> str:
    import hashlib
    return hashlib.sha256(f"{platform}:{handle.lstrip('@').strip().lower()}".encode()).hexdigest()


def staged_hashes(platform: str) -> set:
    return {r["handle_hash"] for r in db.query("SELECT handle_hash FROM peer_seed_samples WHERE platform=%s", (platform,))}


def stage_sample(platform: str, handle: str, fmt: str, niche: str, followers: int, median_views: float, n_posts: int) -> bool:
    """Remember one measured seed account (by handle hash, never the handle). False if it was already staged."""
    row = db.one(
        """INSERT INTO peer_seed_samples(platform, handle_hash, fmt, niche, followers, median_views, n_posts)
           VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (platform, handle_hash) DO NOTHING RETURNING handle_hash""",
        (platform, _handle_hash(platform, handle), fmt, niche, int(followers), round(median_views), n_posts))
    return row is not None


def staged_samples(platform: str) -> list:
    return [{"platform": r["platform"], "fmt": r["fmt"], "niche": r["niche"], "followers": r["followers"],
             "median_views": r["median_views"], "n_posts": r["n_posts"]}
            for r in db.query("SELECT platform, fmt, niche, followers, median_views, n_posts FROM peer_seed_samples WHERE platform=%s",
                              (platform,))]


def staged_counts(platform: str) -> dict:
    out = {b[0]: 0 for b in BANDS}
    for s in staged_samples(platform):
        b = band_for(s["followers"])
        if b:
            out[b] += 1
    return out


def publish_staged(platform: str) -> int:
    """Turn the staged accounts into the published seed benchmark for that platform. Returns groups written."""
    samples = staged_samples(platform)
    return store_samples("seed", samples) if samples else 0


def refresh_from_users() -> int:
    """Rebuild the user-derived groups from the workspaces on this platform. Each account counts once, only accounts with
    enough older posts count, and a group is used only when it has MIN_USERS accounts (see _pick). Sample accounts are excluded."""
    from . import logic
    rows = db.query(
        """SELECT a.id, a.platform, a.niche, p.views, p.duration_s, p.post_date,
                  (SELECT s.followers FROM account_snapshots s WHERE s.account_id = a.id AND s.followers IS NOT NULL
                   ORDER BY s.day DESC LIMIT 1) AS followers
           FROM accounts a JOIN posts p ON p.account_id = a.id
           WHERE a.workspace_id IS NOT NULL AND a.handle NOT LIKE '@demo\\_%%'
             AND p.status IN ('posted', 'analysed') AND p.views_updated_at IS NOT NULL
             AND p.post_date < current_date - %s
           ORDER BY a.id, p.post_date DESC""", (logic.MATURE_DAYS,))
    per: dict = {}
    for r in rows:
        plat = canonical_platform(r["platform"])
        fmt = "short" if plat == "youtube" and r["duration_s"] is not None and r["duration_s"] <= logic.SHORTS_MAX_SECONDS else "main"
        e = per.setdefault((r["id"], fmt), {"platform": plat, "fmt": fmt, "niche": r["niche"],
                                             "followers": r["followers"], "views": []})
        if len(e["views"]) < MAX_POSTS_PER_ACCOUNT:
            e["views"].append(r["views"] or 0)
    samples = []
    for e in per.values():
        if len(e["views"]) >= MIN_POSTS_PER_ACCOUNT:
            samples.append({"platform": e["platform"], "fmt": e["fmt"], "niche": e["niche"], "followers": e["followers"],
                            "median_views": logic._median(e["views"]), "n_posts": len(e["views"])})
    return store_samples("users", samples) if samples else 0


def refresh_users_if_due() -> int:
    """Once a day, from the scheduler loop. One query, no external calls."""
    from . import sociavault
    today = datetime.now(timezone.utc).date().isoformat()
    if sociavault.platform_get("peer_users_refreshed") == today:
        return 0
    sociavault.platform_set("peer_users_refreshed", today)
    return refresh_from_users()
