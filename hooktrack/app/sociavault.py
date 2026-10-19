"""SociaVault public-data API client and account sync into HookTrack tables.

Docs: https://docs.sociavault.com
Auth: X-API-Key header. Most endpoints cost 1 credit.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

from . import crypto, db, leases, logic

BASE = "https://api.sociavault.com/v1/scrape"
TIMEOUT = 45.0
log = logging.getLogger("hooktrack.sociavault")
MAX_PAGES_FULL = 3   # full manual sync (14 days or less)
MAX_PAGES_HISTORY = 15   # manual sync asking for more than 14 days: it still stops at the first post older than the window
MAX_PAGES_LIGHT = 1  # scheduled: one videos page (~1 credit)

SYNC_LEASE = "sociavault-scheduled-sync"
CLAIM_STALE_MINUTES = 10      # an unfinished claim older than this is a crashed sync and may be taken over


class SociaVaultError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status



def platform_get(key: str, default=None):
    row = db.one("SELECT value FROM platform_settings WHERE key=%s", (key,))
    if row is None:
        return default
    return row["value"]


def platform_set(key: str, value) -> None:
    db.execute(
        """INSERT INTO platform_settings(key, value, updated_at) VALUES (%s, %s, now())
           ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()""",
        (key, db.Json(value)),
    )


def platform_delete(key: str) -> None:
    db.execute("DELETE FROM platform_settings WHERE key=%s", (key,))


def resolve_server_key() -> str:
    """System-wide SociaVault key: platform_settings first, then env."""
    enc = platform_get("sociavault_api_key_enc")
    if isinstance(enc, str) and enc:
        try:
            key = crypto.decrypt(enc)
            if key:
                return key.strip()
        except Exception:
            pass
    return (os.environ.get("SOCIAVAULT_API_KEY") or os.environ.get("HOOKTRACK_SOCIAVAULT_API_KEY") or "").strip()


def platform_key_status() -> dict:
    hint = platform_get("sociavault_key_hint") or ""
    has_db = bool(hint) or bool(platform_get("sociavault_api_key_enc"))
    has_env = bool((os.environ.get("SOCIAVAULT_API_KEY") or os.environ.get("HOOKTRACK_SOCIAVAULT_API_KEY") or "").strip())
    source = "platform" if has_db else ("env" if has_env else "none")
    return {
        "configured": has_db or has_env,
        "source": source,
        "key_hint": hint if has_db else ("" if not has_env else "from env"),
        "has_env_fallback": has_env,
    }


def save_platform_key(api_key: str, clear: bool = False) -> dict:
    if clear or not (api_key or "").strip():
        platform_delete("sociavault_api_key_enc")
        platform_delete("sociavault_key_hint")
        return platform_key_status()
    key = api_key.strip()
    hint = (key[:4] + "…" + key[-4:]) if len(key) > 8 else "••••"
    platform_set("sociavault_api_key_enc", crypto.encrypt(key))
    platform_set("sociavault_key_hint", hint)
    return platform_key_status()


def save_schedule_settings(
    *,
    enabled: bool | None = None,
    interval_hours: float | None = None,
    days_back: int | None = None,
    mode: str | None = None,
) -> dict:
    cur = schedule_config()
    if enabled is not None:
        platform_set("sync_enabled", bool(enabled))
    if interval_hours is not None:
        platform_set("sync_interval_hours", float(interval_hours))
    if days_back is not None:
        platform_set("sync_days_back", int(days_back))
    if mode is not None:
        m = (mode or "light").strip().lower()
        if m not in ("light", "full"):
            m = "light"
        platform_set("sync_mode", m)
    return schedule_config()


def resolve_workspace_key(workspace_id: int) -> str:
    """The customer's own SociaVault key, or '' (then the platform's credits are being spent)."""
    row = db.one("SELECT sociavault_key_enc FROM workspace_connectors WHERE workspace_id=%s", (workspace_id,))
    try:
        return crypto.decrypt(row["sociavault_key_enc"]) if row and row.get("sociavault_key_enc") else ""
    except Exception:
        return ""


def resolve_key(workspace_id: int) -> str:
    """Workspace key first, then SOCIAVAULT_API_KEY env."""
    row = db.one(
        "SELECT sociavault_key_enc FROM workspace_connectors WHERE workspace_id=%s",
        (workspace_id,),
    )
    if row and row.get("sociavault_key_enc"):
        key = crypto.decrypt(row["sociavault_key_enc"])
        if key:
            return key
    return resolve_server_key()


def key_status(workspace_id: int) -> dict:
    row = db.one(
        "SELECT key_hint, updated_at FROM workspace_connectors WHERE workspace_id=%s",
        (workspace_id,),
    ) or {}
    has_ws = bool(row.get("key_hint"))
    has_server = bool(resolve_server_key())
    return {
        "configured": has_ws or has_server,
        "source": "workspace" if has_ws else ("server" if has_server else "none"),
        "key_hint": row.get("key_hint") or "",
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


def save_key(workspace_id: int, api_key: str, clear: bool = False) -> dict:
    if clear or not api_key.strip():
        db.execute(
            """INSERT INTO workspace_connectors (workspace_id, sociavault_key_enc, key_hint)
               VALUES (%s, '', '')
               ON CONFLICT (workspace_id) DO UPDATE
               SET sociavault_key_enc='', key_hint='', updated_at=now()""",
            (workspace_id,),
        )
        return key_status(workspace_id)
    key = api_key.strip()
    hint = (key[:4] + "…" + key[-4:]) if len(key) > 8 else "••••"
    db.execute(
        """INSERT INTO workspace_connectors (workspace_id, sociavault_key_enc, key_hint)
           VALUES (%s, %s, %s)
           ON CONFLICT (workspace_id) DO UPDATE
           SET sociavault_key_enc=EXCLUDED.sociavault_key_enc,
               key_hint=EXCLUDED.key_hint, updated_at=now()""",
        (workspace_id, crypto.encrypt(key), hint),
    )
    return key_status(workspace_id)


def _get(path: str, params: dict, api_key: str) -> dict:
    if not api_key:
        raise SociaVaultError("SociaVault API key not configured")
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            r = client.get(
                f"{BASE}/{path}",
                params=params,
                headers={"X-API-Key": api_key},
            )
    except httpx.TimeoutException:
        raise SociaVaultError("SociaVault request timed out")
    except httpx.HTTPError as e:
        raise SociaVaultError(f"Network error: {e}")

    if r.status_code == 401:
        raise SociaVaultError("Invalid SociaVault API key", 401)
    if r.status_code == 403:
        raise SociaVaultError("Insufficient SociaVault credits or key blocked", 403)
    if r.status_code == 404:
        raise SociaVaultError("Account or resource not found on platform", 404)
    if r.status_code != 200:
        raise SociaVaultError(f"SociaVault HTTP {r.status_code}: {r.text[:200]}", r.status_code)

    try:
        data = r.json()
    except Exception:
        raise SociaVaultError("SociaVault returned non-JSON response")

    if isinstance(data, dict) and data.get("success") is False:
        raise SociaVaultError(str(data.get("error") or data.get("message") or data)[:300])
    return data


def _payload(data: dict) -> dict:
    """Responses sometimes nest under data, sometimes are flat."""
    if not isinstance(data, dict):
        return {}
    inner = data.get("data")
    return inner if isinstance(inner, dict) else data


# ---------- TikTok ----------

def tiktok_profile(handle: str, api_key: str) -> dict:
    return _get("tiktok/profile", {"handle": handle.lstrip("@")}, api_key)


def tiktok_videos(
    handle: str,
    api_key: str,
    max_cursor: Optional[str] = None,
    sort_by: str = "latest",
) -> dict:
    params: dict[str, Any] = {"handle": handle.lstrip("@"), "sort_by": sort_by}
    if max_cursor:
        params["max_cursor"] = max_cursor
    return _get("tiktok/videos", params, api_key)


def test_connection(api_key: str, handle: str = "tiktok") -> str:
    """Cheap smoke test: fetch a well-known public profile."""
    data = tiktok_profile(handle, api_key)
    p = _payload(data)
    user = p.get("user") or {}
    stats = p.get("stats") or {}
    name = user.get("uniqueId") or user.get("nickname") or handle
    followers = stats.get("followerCount") or stats.get("follower_count") or "?"
    return f"@{name} ({followers} followers)"


# ---------- Instagram (Reels / posts) ----------

def instagram_profile(handle: str, api_key: str) -> dict:
    return _get("instagram/profile", {"handle": handle.lstrip("@")}, api_key)


def instagram_reels(handle: str, api_key: str, max_id: Optional[str] = None) -> dict:
    params: dict[str, Any] = {"handle": handle.lstrip("@")}
    if max_id:
        params["max_id"] = max_id
    # endpoint name varies slightly across docs; try reels first
    try:
        return _get("instagram/reels", params, api_key)
    except SociaVaultError:
        return _get("instagram/posts", params, api_key)


# ---------- YouTube ----------

def youtube_channel(handle: str, api_key: str) -> dict:
    h = handle.lstrip("@")
    return _get("youtube/channel", {"handle": h}, api_key)


def youtube_channel_shorts(handle: str, api_key: str, page: Optional[str] = None) -> dict:
    params: dict[str, Any] = {"handle": handle.lstrip("@")}
    if page:
        params["continuationToken"] = page
    return _get("youtube/channel/shorts", params, api_key)


def _youtube_items(page: dict, key: str) -> list:
    """The list under `key` ("shorts" / "videos"); the API returns a dict keyed "0","1",... or a list."""
    data = _payload(page)
    items = data.get(key) or (data.get("items") if key == "videos" else None) or []
    if isinstance(items, dict):
        items = list(items.values())
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def youtube_channel_videos(handle: str, api_key: str, page: Optional[str] = None) -> dict:
    params: dict[str, Any] = {"handle": handle.lstrip("@")}
    if page:
        params["page"] = page
    return _get("youtube/channel-videos", params, api_key)


# ---------- Sync into HookTrack ----------

def _aweme_items(page: dict) -> list:
    data = _payload(page)
    raw = data.get("aweme_list") or data.get("videos") or data.get("items") or []
    if isinstance(raw, dict):
        return list(raw.values())
    return list(raw) if isinstance(raw, list) else []


def _ts_to_date(ts, tz: str = "UTC") -> date:
    """Calendar date of a unix timestamp in the workspace's time zone."""
    try:
        t = int(ts)
        if t > 1e12:  # ms
            t //= 1000
        return datetime.fromtimestamp(t, tz=ZoneInfo(tz if logic.valid_tz(tz) else "UTC")).date()
    except (TypeError, ValueError, OSError):
        return logic.today_in(tz)



def _count_from_text(v) -> int:
    """'12,345 views' / '1.2M views' / '5.4K' -> int (0 if it isn't a count)."""
    m = re.search(r"([\d][\d,]*\.?\d*)\s*([KMB]?)", str(v or "").replace("\u00a0", " "), re.I)
    if not m:
        return 0
    try:
        n = float(m.group(1).replace(",", ""))
    except ValueError:
        return 0
    return int(n * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2).upper()])


def _n(v):
    """int, or None when the platform did not provide the number (so 0 stays distinguishable from unknown)."""
    if v is None or v == "" or v == "None":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return _count_from_text(v) or None


def _upsert_post(workspace_id: int, account_id: int, *, ext_id, post_date, date_estimated, hook, caption,
                 views, comments, likes=None, shares=None, saves=None, duration_s=None, url="") -> int:
    """Create or refresh one synced post. Metrics the platform did not send never overwrite stored ones."""
    existing = None
    if ext_id:
        existing = db.one("SELECT id FROM posts WHERE workspace_id=%s AND account_id=%s AND external_id=%s",
                          (workspace_id, account_id, ext_id))
    if existing:
        db.execute(
            """UPDATE posts SET views=%s, comments=%s, status='posted',
                 likes=COALESCE(%s, likes), shares=COALESCE(%s, shares), saves=COALESCE(%s, saves),
                 duration_s=COALESCE(%s, duration_s),
                 url=CASE WHEN %s <> '' THEN %s ELSE url END,
                 post_date=CASE WHEN date_estimated AND NOT %s THEN %s ELSE post_date END,
                 date_estimated=(date_estimated AND %s),
                 caption=CASE WHEN caption='' THEN %s ELSE caption END,
                 hook=CASE WHEN hook='' THEN %s ELSE hook END
               WHERE id=%s""",
            (views, comments, likes, shares, saves, duration_s, url or '', url or '', date_estimated, post_date,
             date_estimated, caption, hook, existing["id"]))
        return existing["id"]
    return db.one(
        """INSERT INTO posts (workspace_id, account_id, post_date, status, hook, caption, views, comments,
                              likes, shares, saves, duration_s, external_id, score_by, date_estimated, url)
           VALUES (%s,%s,%s,'posted',%s,%s,%s,%s,%s,%s,%s,%s,%s,'imported',%s,%s) RETURNING id""",
        (workspace_id, account_id, post_date, hook, caption, views, comments, likes, shares, saves, duration_s,
         ext_id or None, date_estimated, url or ""))["id"]


FOLLOWER_EVERY_HOURS = float(os.environ.get("HOOKTRACK_FOLLOWER_SNAPSHOT_HOURS", "168"))   # weekly by default


def _num(v) -> Optional[int]:
    try:
        return int(float(str(v).replace(",", ""))) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _first(o, *keys):
    for k in keys:
        v = _deep(o, k)
        if v not in (None, "", {}, []):
            return v
    return None


def video_info(platform: str, url: str, api_key: str) -> dict:
    """What one public video says about itself: caption, account, views, likes, comments, when, how long, sound.
    1 credit. TikTok and YouTube shapes were checked live; Instagram is read defensively."""
    p = canonical_platform(platform).lower()
    if p == "tiktok":
        d = _payload(_get("tiktok/video-info", {"url": url}, api_key))
        a = d.get("aweme_detail") or {}
        if not a:
            raise SociaVaultError("That video could not be read. Check the link is public.", 404)
        st, au, mu = a.get("statistics") or {}, a.get("author") or {}, a.get("music") or {}
        return {"platform": "TikTok", "handle": au.get("unique_id") or "", "caption": a.get("desc") or "",
                "views": _num(st.get("play_count")), "likes": _num(st.get("digg_count")),
                "comments": _num(st.get("comment_count")), "shares": _num(st.get("share_count")),
                "posted_ts": _num(a.get("create_time")), "duration_s": (_num((a.get("video") or {}).get("duration")) or 0) / 1000 or None,
                "sound": mu.get("title") or ""}
    if p == "youtube":
        d = _payload(_get("youtube/video", {"url": url}, api_key))
        if not d.get("title") and not d.get("id"):
            raise SociaVaultError("That video could not be read. Check the link is public.", 404)
        ch = d.get("channel") or {}
        return {"platform": "YouTube", "handle": str(ch.get("handle") or ""), "caption": d.get("title") or "",
                "views": _num(d.get("viewCountInt")), "likes": _num(d.get("likeCountInt")),
                "comments": _num(d.get("commentCountInt")), "shares": None, "posted_iso": d.get("publishDate") or "",
                "duration_s": (_num(d.get("durationMs")) or 0) / 1000 or None, "sound": ""}
    if p == "instagram":
        d = _payload(_get("instagram/post-info", {"url": url}, api_key))
        cap = _first(d, "caption", "text", "edge_media_to_caption")
        if isinstance(cap, dict):
            cap = cap.get("text") or ""
        if not (cap or _first(d, "like_count", "play_count", "video_view_count")):
            raise SociaVaultError("That post could not be read. Check the link is public.", 404)
        owner = _first(d, "owner", "user") or {}
        return {"platform": "Instagram", "handle": str((owner.get("username") if isinstance(owner, dict) else "") or ""),
                "caption": str(cap or ""), "views": _num(_first(d, "play_count", "video_view_count", "view_count")),
                "likes": _num(_first(d, "like_count", "likes")), "comments": _num(_first(d, "comment_count", "comments")),
                "shares": None, "posted_ts": _num(_first(d, "taken_at", "taken_at_timestamp")),
                "duration_s": _num(_first(d, "video_duration")), "sound": ""}
    raise SociaVaultError("Use a TikTok, Instagram or YouTube video link.", 400)


def _deep(o, key, depth=0, maxd=5):
    """First value stored under `key` anywhere in a nested dict/list (the API nests profiles inconsistently)."""
    if isinstance(o, dict):
        if key in o:
            return o[key]
        if depth < maxd:
            for v in o.values():
                r = _deep(v, key, depth + 1, maxd)
                if r is not None:
                    return r
    elif isinstance(o, list) and depth < maxd:
        for v in o[:5]:
            r = _deep(v, key, depth + 1, maxd)
            if r is not None:
                return r
    return None


def _profile_stats(platform: str, raw: dict) -> dict:
    """Audience numbers from a profile/channel response, per platform. Missing -> None."""
    if platform == "tiktok":
        v2 = _deep(raw, "statsV2")                     # exact counts; "stats" is rounded to 3 digits
        src = v2 if isinstance(v2, dict) else (_deep(raw, "stats") or raw)
        return {"followers": _n(src.get("followerCount")), "following": _n(src.get("followingCount")),
                "total_likes": _n(src.get("heartCount") if src.get("heartCount") is not None else src.get("heart")),
                "video_count": _n(src.get("videoCount"))}
    if platform == "instagram":
        def count(key):
            v = _deep(raw, key)
            return _n(v.get("count")) if isinstance(v, dict) else _n(v)
        return {"followers": count("edge_followed_by") or _n(_deep(raw, "follower_count")),
                "following": count("edge_follow") or _n(_deep(raw, "following_count")),
                "total_likes": None,
                "video_count": count("edge_owner_to_timeline_media") or _n(_deep(raw, "media_count"))}
    return {"followers": _n(_deep(raw, "subscriberCount")), "following": None, "total_likes": None,
            "video_count": _n(_deep(raw, "videoCount"))}


def _followers_due(account_id: int) -> bool:
    """Light syncs only look at the profile (1 extra credit) about once a week; full syncs always do."""
    row = db.one("SELECT max(taken_at) AS t FROM account_snapshots WHERE account_id=%s", (account_id,))
    if not row or row["t"] is None:
        return True
    return (datetime.now(timezone.utc) - row["t"]).total_seconds() >= FOLLOWER_EVERY_HOURS * 3600


def _store_followers(workspace_id: int, account_id: int, platform: str, raw: dict) -> None:
    st = _profile_stats(platform, raw)
    if st["followers"] is None:
        return
    db.execute(
        """INSERT INTO account_snapshots (account_id, workspace_id, day, followers, following, total_likes, video_count, taken_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s, now())
           ON CONFLICT (account_id, day) DO UPDATE SET followers=EXCLUDED.followers, following=EXCLUDED.following,
             total_likes=EXCLUDED.total_likes, video_count=EXCLUDED.video_count, taken_at=now()""",
        (account_id, workspace_id, logic.today(workspace_id), st["followers"], st["following"], st["total_likes"],
         st["video_count"]))


def _record_post(workspace_id: int, account_id: int, post_id: int, views: int, today: date) -> None:
    """Remember today's lifetime view count for a post, and mark its views as really recorded."""
    db.execute(
        """INSERT INTO post_view_snapshots (post_id, workspace_id, day, views) VALUES (%s,%s,%s,%s)
           ON CONFLICT (post_id, day) DO UPDATE SET views = EXCLUDED.views""",
        (post_id, workspace_id, today, views),
    )
    db.execute("UPDATE posts SET views_updated_at = now() WHERE id=%s AND workspace_id=%s",
               (post_id, workspace_id))


def _apply_daily_gain(workspace_id: int, account_id: int, today: date) -> dict:
    """Write the views this account GAINED today (sum over posts of today's snapshot minus the
    previous one) into the daily grid. Posts first seen today that were posted before today have
    no baseline and contribute nothing rather than their whole lifetime count. Cells the user
    entered by hand or via AI screenshots are never overwritten."""
    row = db.one(
        """SELECT COALESCE(SUM(GREATEST(cur.views - COALESCE(prev.views,
                    CASE WHEN p.post_date >= %(today)s AND NOT p.date_estimated THEN 0 ELSE cur.views END), 0)), 0)::bigint AS gain,
                  COUNT(*) AS posts
           FROM post_view_snapshots cur
           JOIN posts p ON p.id = cur.post_id
           LEFT JOIN LATERAL (SELECT s.views FROM post_view_snapshots s
                              WHERE s.post_id = cur.post_id AND s.day < %(today)s
                              ORDER BY s.day DESC LIMIT 1) prev ON TRUE
           WHERE cur.day = %(today)s AND cur.workspace_id = %(ws)s AND p.account_id = %(acc)s
             AND (prev.views IS NOT NULL OR (p.post_date >= %(today)s AND NOT p.date_estimated))""",
        {"today": today, "ws": workspace_id, "acc": account_id},
    )
    if not row or not row["posts"]:
        return {"written": 0, "kept": 0}
    n = db.execute(
        """INSERT INTO daily_views (account_id, workspace_id, day, views, source, updated_at)
           VALUES (%s,%s,%s,%s,'sociavault',now())
           ON CONFLICT (account_id, day) DO UPDATE
           SET views = EXCLUDED.views, source = 'sociavault', updated_at = now()
           WHERE daily_views.source IN ('sociavault', 'estimated')""",
        (account_id, workspace_id, today, row["gain"]),
    )
    return {"written": 1, "kept": 0} if n else {"written": 0, "kept": 1}


def sync_tiktok_account(workspace_id: int, account_id: int, days_back: int = 14,
                        mode: str = "full") -> dict:
    """mode=light: skip profile, 1 video page (~1 credit). full: profile + up to 3 pages."""
    acc = db.one(
        "SELECT * FROM accounts WHERE id=%s AND workspace_id=%s",
        (account_id, workspace_id),
    )
    if not acc:
        raise SociaVaultError("Account not found")
    handle = (acc["handle"] or "").lstrip("@")
    if not handle:
        raise SociaVaultError("Account has no handle")
    key = resolve_key(workspace_id)
    if not key:
        raise SociaVaultError("SociaVault API key not configured")

    light = (mode or "full").lower() == "light"
    credits = 0
    external_id = None
    if not light or _followers_due(account_id):
        profile = tiktok_profile(handle, key)
        credits += 1
        p = _payload(profile)
        user = p.get("user") or {}
        external_id = str(user.get("id") or user.get("uid") or "") or None
        _store_followers(workspace_id, account_id, "tiktok", profile)

    cutoff = datetime.now(timezone.utc).timestamp() - days_back * 86400
    cursor = None
    upserted = 0
    tz = logic.workspace_tz(workspace_id)
    today = logic.today_in(tz)
    pages = MAX_PAGES_LIGHT if light else (MAX_PAGES_HISTORY if days_back > 14 else MAX_PAGES_FULL)

    for _ in range(pages):
        page = tiktok_videos(handle, key, max_cursor=cursor)
        credits += 1
        data = _payload(page)
        items = _aweme_items(page)
        stop_early = False

        for item in items:
            create_ts = item.get("create_time") or item.get("createTime") or 0
            try:
                create_ts = int(create_ts)
            except (TypeError, ValueError):
                create_ts = 0
            if create_ts and create_ts < cutoff:
                if not item.get("is_top"):      # pinned posts sit first and are old: they say nothing about the rest
                    stop_early = True
                continue

            stats = item.get("statistics") or item.get("stats") or {}
            views = int(stats.get("play_count") or stats.get("playCount") or 0)
            comments = int(stats.get("comment_count") or stats.get("commentCount") or 0)
            desc = (item.get("desc") or item.get("title") or item.get("caption") or "").strip()
            aweme_id = str(item.get("aweme_id") or item.get("id") or item.get("video_id") or "")
            date_estimated = not create_ts
            post_date = _ts_to_date(create_ts, tz) if create_ts else today
            hook = desc[:200] if desc else ""

            likes = _n(stats.get("digg_count") if stats.get("digg_count") is not None else stats.get("diggCount"))
            shares = _n(stats.get("share_count"))
            saves = _n(stats.get("collect_count"))
            vd = (item.get("video") or {}).get("duration")
            duration_s = (vd / 1000.0) if isinstance(vd, (int, float)) and vd > 0 else None
            post_id = _upsert_post(
                workspace_id, account_id, ext_id=aweme_id, post_date=post_date,
                date_estimated=date_estimated, hook=hook, caption=desc[:2200], views=views, comments=comments,
                likes=likes, shares=shares, saves=saves, duration_s=duration_s,
                    url=f"https://www.tiktok.com/@{handle}/video/{aweme_id}" if aweme_id else "",)
            upserted += 1
            _record_post(workspace_id, account_id, post_id, views, today)
            music = item.get("music") or item.get("music_info") or {}
            if isinstance(music, dict):
                title = (music.get("title") or music.get("music_name") or "").strip()
                author = (music.get("author") or music.get("owner_nickname") or "").strip()
                sound = (f"{title} · {author}" if title and author else title)[:200]
                if sound:
                    db.execute("UPDATE posts SET sound=%s WHERE id=%s AND workspace_id=%s AND sound=''",
                               (sound, post_id, workspace_id))

        has_more = data.get("has_more") in (1, True, "1", "true")
        cursor = data.get("max_cursor") or data.get("cursor")
        if not has_more or not cursor or stop_early:
            break

    daily = _apply_daily_gain(workspace_id, account_id, today)

    db.execute(
        """UPDATE accounts SET external_id=COALESCE(%s, external_id),
           last_synced_at=now() WHERE id=%s AND workspace_id=%s""",
        (external_id, account_id, workspace_id),
    )

    return {
        "platform": "TikTok",
        "handle": handle,
        "mode": "light" if light else "full",
        "upserted": upserted,
        "days": daily["written"],
        "manual_kept": daily["kept"],
        "credits_est": credits,
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
    }


def sync_instagram_account(workspace_id: int, account_id: int, days_back: int = 14,
                           mode: str = "full") -> dict:
    """Best-effort Instagram Reels/posts sync. Field names vary; we map defensively."""
    acc = db.one(
        "SELECT * FROM accounts WHERE id=%s AND workspace_id=%s",
        (account_id, workspace_id),
    )
    if not acc:
        raise SociaVaultError("Account not found")
    handle = (acc["handle"] or "").lstrip("@")
    key = resolve_key(workspace_id)
    if not key:
        raise SociaVaultError("SociaVault API key not configured")

    light = (mode or "full").lower() == "light"
    credits = 0
    external_id = None
    if not light or _followers_due(account_id):
        try:
            profile = instagram_profile(handle, key)
            credits += 1
            p = _payload(profile)
            user = p.get("user") or p.get("data") or p
            external_id = str(user.get("id") or user.get("pk") or _deep(profile, "id") or "") or None
            _store_followers(workspace_id, account_id, "instagram", profile)
        except SociaVaultError:
            external_id = None

    cutoff = datetime.now(timezone.utc).timestamp() - days_back * 86400
    upserted = 0
    tz = logic.workspace_tz(workspace_id)
    today = logic.today_in(tz)
    page = instagram_reels(handle, key)
    credits += 1
    data = _payload(page)
    items = data.get("items") or data.get("reels") or data.get("posts") or data.get("data") or []
    if isinstance(items, dict):
        items = list(items.values())

    for item in items if isinstance(items, list) else []:
        # common IG shapes
        media = item.get("media") or item
        ts = (
            media.get("taken_at")
            or media.get("taken_at_timestamp")
            or media.get("timestamp")
            or media.get("create_time")
            or 0
        )
        try:
            ts = int(ts)
        except (TypeError, ValueError):
            ts = 0
        if ts and ts < cutoff:
            continue
        views = int(
            media.get("video_play_count")
            or media.get("play_count")
            or media.get("view_count")
            or media.get("ig_play_count")
            or 0
        )
        comments = int(media.get("comment_count") or media.get("comments_count") or 0)
        caption_obj = media.get("caption")
        if isinstance(caption_obj, dict):
            desc = (caption_obj.get("text") or "").strip()
        else:
            desc = (caption_obj or media.get("text") or "").strip() if caption_obj else ""
        ext = str(media.get("id") or media.get("pk") or media.get("code") or "")
        date_estimated = not ts
        post_date = _ts_to_date(ts, tz) if ts else today
        hook = desc[:200]

        likes = _n(media.get("like_count"))
        shares = _n(media.get("reshare_count"))
        saves = None
        vd = media.get("video_duration")
        duration_s = float(vd) if isinstance(vd, (int, float)) and vd > 0 else None
        post_id = _upsert_post(
            workspace_id, account_id, ext_id=ext, post_date=post_date,
            date_estimated=date_estimated, hook=hook, caption=desc[:2200], views=views, comments=comments,
            likes=likes, shares=shares, saves=saves, duration_s=duration_s,
                url=(f"https://www.instagram.com/reel/{media.get('code')}/" if media.get("code") else ""),)
        upserted += 1
        _record_post(workspace_id, account_id, post_id, views, today)

    daily = _apply_daily_gain(workspace_id, account_id, today)

    db.execute(
        """UPDATE accounts SET external_id=COALESCE(%s, external_id),
           last_synced_at=now() WHERE id=%s AND workspace_id=%s""",
        (external_id, account_id, workspace_id),
    )
    return {
        "platform": "Instagram",
        "handle": handle,
        "mode": "light" if light else "full",
        "upserted": upserted,
        "days": daily["written"],
        "manual_kept": daily["kept"],
        "credits_est": credits,
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
    }


def sync_youtube_account(workspace_id: int, account_id: int, days_back: int = 14,
                         mode: str = "full") -> dict:
    acc = db.one(
        "SELECT * FROM accounts WHERE id=%s AND workspace_id=%s",
        (account_id, workspace_id),
    )
    if not acc:
        raise SociaVaultError("Account not found")
    handle = (acc["handle"] or "").lstrip("@")
    key = resolve_key(workspace_id)
    if not key:
        raise SociaVaultError("SociaVault API key not configured")

    light = (mode or "full").lower() == "light"
    credits = 0
    external_id = None
    if not light or _followers_due(account_id):
        try:
            ch = youtube_channel(handle, key)
            credits += 1
            _store_followers(workspace_id, account_id, "youtube", ch)
            p = _payload(ch)
            channel = p.get("channel") or p.get("items") or p
            if isinstance(channel, list) and channel:
                channel = channel[0]
            external_id = str(
                (channel.get("id") if isinstance(channel, dict) else None)
                or (channel.get("channelId") if isinstance(channel, dict) else None)
                or ""
            ) or None
        except SociaVaultError:
            external_id = None

    cutoff = datetime.now(timezone.utc).timestamp() - days_back * 86400
    # Short-form is the point of this app, and the "videos" endpoint returns long-form only. Pull Shorts first;
    # light mode stops there unless the channel has none (then it falls back to the long-form page).
    items = _youtube_items(youtube_channel_shorts(handle, key), "shorts")
    credits += 1
    if not light or not items:
        seen = {str(i.get("id")) for i in items}
        longform = _youtube_items(youtube_channel_videos(handle, key), "videos")
        credits += 1
        items += [i for i in longform if str(i.get("id")) not in seen]

    upserted = 0
    tz = logic.workspace_tz(workspace_id)
    today = logic.today_in(tz)
    for item in items if isinstance(items, list) else []:
        snippet = item.get("snippet") or item
        stats = item.get("statistics") or item.get("stats") or item
        published = (
            snippet.get("publishedAt")
            or snippet.get("publish_date")
            or item.get("publishDate")          # what the real API sends: ISO-8601 with an offset
            or item.get("publishedAt")
            or item.get("create_time")
        )
        ts = 0
        if isinstance(published, str) and published:
            try:
                ts = int(datetime.fromisoformat(published.replace("Z", "+00:00")).timestamp())
            except ValueError:
                ts = 0
        elif published:
            try:
                ts = int(published)
            except (TypeError, ValueError):
                ts = 0
        if ts and ts < cutoff:
            continue
        raw_views = next((v for v in (stats.get("viewCountInt"), stats.get("viewCount"), stats.get("view_count"),
                                      stats.get("views")) if v not in (None, "")), None)
        views = _count_from_text(raw_views)
        if not views:
            views = _count_from_text(item.get("viewCountText") or stats.get("viewCountText"))
        comments = _n(stats.get("commentCountInt") if stats.get("commentCountInt") is not None
                      else stats.get("commentCount") or stats.get("comment_count")) or 0
        title = (snippet.get("title") or item.get("title") or "").strip()
        desc = (snippet.get("description") or item.get("description") or title).strip()
        ext = str(item.get("id") or item.get("videoId") or item.get("video_id") or "")
        if isinstance(ext, dict):
            ext = str(ext.get("videoId") or "")
        date_estimated = not ts
        post_date = _ts_to_date(ts, tz) if ts else today
        hook = title[:200] or desc[:200]

        likes = _n(stats.get("likeCountInt") if stats.get("likeCountInt") is not None else stats.get("likeCount"))
        shares = saves = None
        dms = item.get("durationMs") or snippet.get("durationMs")
        ls = item.get("lengthSeconds")
        duration_s = (float(dms) / 1000.0) if str(dms or "").isdigit() and int(dms) > 0 else (
            float(ls) if str(ls or "").isdigit() and int(ls) > 0 else None)
        post_id = _upsert_post(
            workspace_id, account_id, ext_id=ext, post_date=post_date,
            date_estimated=date_estimated, hook=hook, caption=desc[:2200], views=views, comments=comments,
            likes=likes, shares=shares, saves=saves, duration_s=duration_s,
                url=str(item.get("url") or (f"https://www.youtube.com/watch?v={ext}" if ext else "")),)
        upserted += 1
        _record_post(workspace_id, account_id, post_id, views, today)

    daily = _apply_daily_gain(workspace_id, account_id, today)

    db.execute(
        """UPDATE accounts SET external_id=COALESCE(%s, external_id),
           last_synced_at=now() WHERE id=%s AND workspace_id=%s""",
        (external_id, account_id, workspace_id),
    )
    return {
        "platform": "YouTube",
        "handle": handle,
        "mode": "light" if light else "full",
        "upserted": upserted,
        "days": daily["written"],
        "manual_kept": daily["kept"],
        "credits_est": credits,
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
    }


class SyncSkipped(SociaVaultError):
    """Another sync of this account is running, or it was synced too recently: nothing was done."""


def _claim_account(account_id: int, min_age_s: float = 0) -> bool:
    """Atomically mark the account as being synced. False if someone else is mid-sync, or (when
    min_age_s > 0) it was synced more recently than that, so two workers can't both spend credits."""
    row = db.one(
        """UPDATE accounts SET sync_claimed_at = now()
           WHERE id = %s
             AND (sync_claimed_at IS NULL OR sync_claimed_at < now() - make_interval(mins => %s))
             AND (%s <= 0 OR last_synced_at IS NULL OR last_synced_at < now() - make_interval(secs => %s))
           RETURNING id""",
        (account_id, CLAIM_STALE_MINUTES, min_age_s, min_age_s))
    return row is not None


def _release_account(account_id: int) -> None:
    db.execute("UPDATE accounts SET sync_claimed_at = NULL WHERE id = %s", (account_id,))


def credit_gate(workspace_id: int, need: int = 1) -> None:
    """Refuse a call that would pass the plan's monthly credits (a SociaVaultError so every caller reports it)."""
    from fastapi import HTTPException
    from . import auth, billing, vaultwatch
    try:
        auth.require_verified_workspace(workspace_id)     # a free trial must not spend our credits before the email is real
    except HTTPException as e:
        raise SociaVaultError(str(e.detail), 403)
    if vaultwatch.exhausted() and not resolve_workspace_key(workspace_id):
        raise SociaVaultError("Data syncing is temporarily unavailable. We have been told and are on it; "
                              "your existing data is unchanged.", 503)
    try:
        billing.check_credits(workspace_id, need)
    except HTTPException as e:
        raise SociaVaultError(str(e.detail), 402)


def record_credits(workspace_id: int, kind: str, credits: int) -> None:
    """Add to the workspace's credit ledger. Never raises: a failed note must not fail the work it describes."""
    try:
        n = int(credits or 0)
        if n > 0:
            db.execute("INSERT INTO credit_usage (workspace_id, kind, credits) VALUES (%s,%s,%s)",
                       (workspace_id, kind, n))
    except Exception:
        pass


def credit_summary(workspace_id: int) -> dict:
    """This calendar month's spend by kind, and when the workspace's accounts were last synced."""
    rows = db.query(
        """SELECT kind, SUM(credits)::int AS n FROM credit_usage
           WHERE workspace_id=%s AND day >= date_trunc('month', CURRENT_DATE)::date GROUP BY kind""", (workspace_id,))
    by_kind = {r["kind"]: r["n"] for r in rows}
    last = db.one("SELECT MAX(last_synced_at) AS t FROM accounts WHERE workspace_id=%s", (workspace_id,))
    cfg = schedule_config()
    return {"month": sum(by_kind.values()), "by_kind": by_kind,
            "last_synced_at": last["t"].isoformat() if last and last["t"] else None,
            "auto_sync": bool(cfg["enabled"]), "interval_hours": cfg["interval_hours"]}


def _note_failure(workspace_id: int, account_id: int, e: SociaVaultError) -> None:
    """Remember a provider-side failure for the operator's health view. A 404 is the customer's account being
    private or renamed, not an outage, so it does not count. Never raises."""
    try:
        from . import vaultwatch
        if e.status == 404 or isinstance(e, SyncSkipped):
            return
        vaultwatch.record_sync_failure(account_id, str(e))
        if e.status == 403 and not resolve_workspace_key(workspace_id):
            vaultwatch.check(max_age_s=60)       # out of credits? find out now and tell the operator
    except Exception:
        pass


def sync_account(workspace_id: int, account_id: int, days_back: int = 14,
                 mode: str = "full", min_age_s: float = 0) -> dict:
    acc = db.one(
        "SELECT platform FROM accounts WHERE id=%s AND workspace_id=%s",
        (account_id, workspace_id),
    )
    if not acc:
        raise SociaVaultError("Account not found")
    platform = (acc["platform"] or "TikTok").strip().lower()
    if platform in ("tiktok", "tt"):
        fn = sync_tiktok_account
    elif platform in ("instagram", "ig", "reels"):
        fn = sync_instagram_account
    elif platform in ("youtube", "yt", "youtube shorts", "shorts"):
        fn = sync_youtube_account
    else:
        raise SociaVaultError(
            f"SociaVault sync not supported for platform '{acc['platform']}'. "
            "Use TikTok, Instagram, or YouTube."
        )
    credit_gate(workspace_id, 1)
    if not _claim_account(account_id, min_age_s):
        raise SyncSkipped("This account is already being synced, or was synced a moment ago")
    try:
        out = fn(workspace_id, account_id, days_back, mode=mode)
        record_credits(workspace_id, "sync", out.get("credits_est"))
    except SociaVaultError as e:
        _note_failure(workspace_id, account_id, e)
        raise
    except Exception as e:
        _note_failure(workspace_id, account_id, SociaVaultError(f"{type(e).__name__}: {e}"))
        raise
    finally:
        _release_account(account_id)
    try:
        from . import activation, vaultwatch
        vaultwatch.record_sync_ok(account_id)
        activation.mark(workspace_id, "first_sync")
    except Exception:
        pass
    try:                                         # cards whose posted-video link is now in the synced data
        from . import results
        results.reconcile(workspace_id)
    except Exception:
        pass
    try:                                         # remember today's calls, to check them against what happens later
        logic.log_decisions(workspace_id)
    except Exception:
        pass
    try:                                         # score every judged hook from its measured results
        logic.apply_auto_scores(workspace_id)
    except Exception:
        log.exception("auto hook scoring failed")
    try:                                         # a very strong first day: tell the owner while it is hot
        from . import hotstart
        hotstart.notify(workspace_id)
    except Exception:
        pass
    try:                                         # empty days of the last week get a labelled estimate
        from . import estimate
        estimate.fill(workspace_id, account_id)
    except Exception:
        pass
    return out


def sync_all(workspace_id: int, days_back: int = 14, mode: str = "full") -> dict:
    rows = db.query(
        """SELECT id, handle, platform FROM accounts
           WHERE workspace_id=%s AND sync_enabled AND status='active'
           ORDER BY handle""",
        (workspace_id,),
    )
    results = []
    for r in rows:
        try:
            results.append({"id": r["id"], "handle": r["handle"], "ok": True,
                            **sync_account(workspace_id, r["id"], days_back, mode=mode)})
        except SociaVaultError as e:
            results.append({"id": r["id"], "handle": r["handle"], "ok": False, "error": str(e),
                            **({"skipped": True} if isinstance(e, SyncSkipped) else {})})
        except Exception as e:
            results.append({"id": r["id"], "handle": r["handle"], "ok": False, "error": str(e)[:200]})
    return {"synced": sum(1 for x in results if x.get("ok")), "total": len(results), "results": results}


def schedule_config() -> dict:
    """Schedule settings: platform_settings override env defaults.

    Env fallbacks: HOOKTRACK_SYNC_INTERVAL_HOURS (default 24; 0 disables),
    HOOKTRACK_SYNC_DAYS_BACK, HOOKTRACK_SYNC_MODE.
    """
    raw = os.environ.get("HOOKTRACK_SYNC_INTERVAL_HOURS", "24").strip()
    try:
        hours = float(raw)
    except ValueError:
        hours = 24.0
    enabled = hours > 0
    days_back = max(1, min(30, int(os.environ.get("HOOKTRACK_SYNC_DAYS_BACK", "7"))))
    mode = (os.environ.get("HOOKTRACK_SYNC_MODE") or "light").strip().lower()

    pe = platform_get("sync_enabled")
    if pe is not None:
        enabled = bool(pe)
    ph = platform_get("sync_interval_hours")
    if ph is not None:
        try:
            hours = float(ph)
        except (TypeError, ValueError):
            pass
    pd = platform_get("sync_days_back")
    if pd is not None:
        try:
            days_back = max(1, min(30, int(pd)))
        except (TypeError, ValueError):
            pass
    pm = platform_get("sync_mode")
    if isinstance(pm, str) and pm.strip():
        mode = pm.strip().lower()
    if mode not in ("light", "full"):
        mode = "light"
    if not enabled:
        hours = 0
    elif hours <= 0:
        hours = 24.0
    return {
        "enabled": enabled,
        "interval_hours": hours if enabled else 0,
        "days_back": days_back,
        "mode": mode,
    }



def _last_run() -> dict:
    return platform_get("sync_last_run") or {"started_at": None, "finished_at": None, "ok": None, "summary": None}


def schedule_status() -> dict:
    """Config + the last run, read from the database so every worker reports the same thing."""
    return {**schedule_config(), **_last_run(), "running": leases.is_held(SYNC_LEASE)}


def scheduled_run_due() -> bool:
    """True when the schedule is on and the last finished run is older than the interval (any worker's)."""
    cfg = schedule_config()
    if not cfg["enabled"]:
        return False
    fin = _last_run().get("finished_at")
    if not fin:
        return True
    try:
        last = datetime.fromisoformat(fin)
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last).total_seconds() >= cfg["interval_hours"] * 3600 * 0.98


def run_scheduled_sync(force: bool = False) -> dict:
    """One pass across ALL workspaces: every active account with Auto-sync on.

    Safe with many workers: a lease means only one pass runs at a time, and each account is claimed
    atomically (with its freshness check) before any credits are spent. Default light mode (~1
    SociaVault credit per account); accounts synced within ~85% of the interval are skipped unless force.
    """
    with leases.lease(SYNC_LEASE, ttl=600) as got:
        if not got:
            return {**schedule_status(), "ok": False, "error": "A scheduled sync is already running"}
        return _run_scheduled_pass(force)


def _run_scheduled_pass(force: bool) -> dict:
    cfg = schedule_config()
    mode = cfg["mode"] if cfg["mode"] in ("light", "full") else "light"
    days_back = cfg["days_back"]
    interval_hours = cfg["interval_hours"] or 24.0
    started = datetime.now(timezone.utc).isoformat()
    platform_set("sync_last_run", {**_last_run(), "started_at": started})

    server_key = bool(resolve_server_key())
    rows = db.query(
        """SELECT a.id, a.handle, a.platform, a.workspace_id, a.last_synced_at,
                  c.sociavault_key_enc
           FROM accounts a
           LEFT JOIN workspace_connectors c ON c.workspace_id = a.workspace_id
           WHERE a.sync_enabled AND a.status = 'active'
           ORDER BY a.workspace_id, a.handle"""
    )

    min_age = 0 if force else interval_hours * 3600 * 0.85
    results = []
    skipped = 0
    credits_est = 0

    from . import auth, billing, vaultwatch
    held = ""
    try:
        vaultwatch.check(force=False, max_age_s=300)
        st = vaultwatch.state()["status"]
        if st in ("paused", "exhausted") and not force:     # "Run sync now" is the operator's call and always goes
            held = st
    except Exception:
        log.exception("balance check before sync failed")
    if held:
        rows = []
    expired: dict = {}
    n_synced = 0
    for r in rows:
        w = r["workspace_id"]
        if w not in expired:
            try:
                expired[w] = billing.access_blocked(w) or not auth.workspace_verified_ok(w)
            except Exception:
                expired[w] = False
        if expired[w]:                      # trial over / email not confirmed: do not spend provider credits on it
            skipped += 1
            continue
        n_synced += 1
        if n_synced % 20 == 0 and not force:               # the balance can run out in the middle of a big pass
            try:
                if vaultwatch.check(max_age_s=0)["status"] in ("paused", "exhausted"):
                    held = vaultwatch.state()["status"]
                    break
            except Exception:
                pass
        has_ws_key = bool(r.get("sociavault_key_enc"))
        if not has_ws_key and not server_key:
            results.append({
                "id": r["id"], "workspace_id": r["workspace_id"], "handle": r["handle"],
                "ok": False, "error": "No SociaVault key for this workspace",
            })
            continue
        try:
            out = sync_account(r["workspace_id"], r["id"], days_back=days_back, mode=mode, min_age_s=min_age)
            credits_est += int(out.get("credits_est") or 0)
            results.append({
                "id": r["id"], "workspace_id": r["workspace_id"], "handle": r["handle"],
                "ok": True,
                **{k: out[k] for k in ("platform", "mode", "upserted", "credits_est") if k in out},
            })
        except SyncSkipped:
            skipped += 1
        except SociaVaultError as e:
            results.append({
                "id": r["id"], "workspace_id": r["workspace_id"], "handle": r["handle"],
                "ok": False, "error": str(e),
            })
        except Exception as e:
            results.append({
                "id": r["id"], "workspace_id": r["workspace_id"], "handle": r["handle"],
                "ok": False, "error": str(e)[:200],
            })

    summary = {
        "ok": True,
        "mode": mode,
        "days_back": days_back,
        "candidates": len(rows),
        "synced": sum(1 for x in results if x.get("ok")),
        "failed": sum(1 for x in results if x.get("ok") is False),
        "skipped_fresh": skipped,
        "credits_est": credits_est,
        "held": held,
        "results": results,
    }
    platform_set("sync_last_run", {
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "ok": True,
        "summary": {k: summary[k] for k in summary if k != "results"},
    })
    try:
        vaultwatch.check(max_age_s=0)            # what did that pass cost us?
        vaultwatch._alert_failures()
    except Exception:
        log.exception("balance check after sync failed")
    return summary



# ---------- handles: accept what people actually paste, and confirm it is the account they meant ----------

_IG_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def ig_shortcode(media_id) -> str:
    """Instagram's URL shortcode is the numeric media id written in base 64."""
    try:
        n = int(str(media_id).split("_")[0])
    except ValueError:
        return ""
    out = ""
    while n:
        n, r = divmod(n, 64)
        out = _IG_ALPHABET[r] + out
    return out


PLATFORM_ALIASES = {
    "tiktok": "TikTok", "tik tok": "TikTok", "tt": "TikTok",
    "instagram": "Instagram", "ig": "Instagram", "insta": "Instagram", "reels": "Instagram", "instagram reels": "Instagram",
    "youtube": "YouTube", "yt": "YouTube", "you tube": "YouTube", "shorts": "YouTube", "youtube shorts": "YouTube",
}


def canonical_platform(name: str) -> str:
    n = " ".join((name or "").lower().split())
    return PLATFORM_ALIASES.get(n, (name or "").strip() or "TikTok")


_URL_RE = re.compile(r"^(?:https?://)?(?:www\.|m\.|vm\.|vt\.)?(tiktok\.com|instagram\.com|youtube\.com|youtu\.be)/(.+)$", re.I)


def normalize_handle(text: str, platform_hint: str = "") -> tuple[str, str]:
    """('@handle', 'Platform') from a handle, an @handle, or a profile/video URL on any of the three platforms."""
    t = (text or "").strip()
    platform = canonical_platform(platform_hint) if platform_hint else ""
    m = _URL_RE.match(t)
    if m:
        host, path = m.group(1).lower(), m.group(2).split("?")[0].split("#")[0].strip("/")
        parts = [p for p in path.split("/") if p]
        if host == "tiktok.com":
            platform = "TikTok"
            t = parts[0] if parts else ""
        elif host == "instagram.com":
            platform = "Instagram"
            skip = {"p", "reel", "reels", "stories", "explore", "tv"}
            t = parts[0] if parts and parts[0].lower() not in skip else ""
        else:
            platform = "YouTube"
            if parts and parts[0].startswith("@"):
                t = parts[0]
            elif len(parts) > 1 and parts[0] in ("c", "user", "channel"):
                t = parts[1]
            else:
                t = ""
    t = t.strip().lstrip("@").strip()
    t = re.sub(r"[^\w.\-]", "", t)          # no spaces/punctuation: a platform handle never has them
    return ("@" + t if t else ""), (platform or "TikTok")


def lookup_profile(workspace_id: int, platform: str, handle: str) -> dict:
    """Does this handle exist on that platform, and who is it? Costs 1 credit. Never raises for 'not found'."""
    platform = canonical_platform(platform)
    h = handle.lstrip("@")
    key = resolve_key(workspace_id)
    if not key:
        raise SociaVaultError("SociaVault API key not configured")
    credit_gate(workspace_id, 1)
    bio = ""
    try:
        if platform == "TikTok":
            raw = tiktok_profile(h, key)
            user = _deep(raw, "user") or {}
            name, verified, ident = user.get("nickname") or user.get("uniqueId"), bool(user.get("verified")), user.get("id")
            real, bio = user.get("uniqueId"), user.get("signature") or ""
        elif platform == "Instagram":
            raw = instagram_profile(h, key)
            user = _deep(raw, "user") or {}
            name, verified, ident = user.get("full_name") or user.get("username"), bool(user.get("is_verified")), user.get("id")
            real = user.get("username")
            bio = user.get("biography") or user.get("category") or user.get("category_name") or ""
        elif platform == "YouTube":
            raw = youtube_channel(h, key)
            name, verified, ident = _deep(raw, "name"), bool(_deep(raw, "isVerified")), _deep(raw, "channelId")
            real = (_deep(raw, "handle") or "").lstrip("@")
            bio = _deep(raw, "description") or ""
        else:
            raise SociaVaultError(f"SociaVault cannot look up '{platform}' accounts. Use TikTok, Instagram or YouTube.")
    except SociaVaultError as e:
        if e.status == 404:
            return {"found": False, "platform": platform, "handle": "@" + h,
                    "message": f"No {platform} account named @{h} was found. Check the spelling."}
        raise
    st = _profile_stats(platform.lower(), raw)
    if not (name or ident or st["followers"] is not None):
        return {"found": False, "platform": platform, "handle": "@" + h,
                "message": f"No {platform} account named @{h} was found. Check the spelling."}
    return {"found": True, "platform": platform, "handle": "@" + (real or h), "display_name": name or "",
            "verified": verified, "external_id": str(ident or ""), "followers": st["followers"],
            "video_count": st["video_count"], "bio": str(bio or "")[:300], "credits_est": 1}


# ---------- transcripts: what is actually said in the video ----------

def fetch_transcript(platform: str, url: str, api_key: str):
    """The spoken words of one public video (WebVTT text or a list of segments). About 1 credit.
    Endpoints (all take `url`; shapes seen live):
      tiktok/transcript         data.transcript = null | WebVTT text
      instagram/transcript      data.transcripts = {"0": {id, shortcode, text}}   (AI generated, plain text, untimed)
      youtube/video/transcript  data.transcript  = {"0": {text, startMs, endMs, startTimeText}, ...}"""
    path = {"tiktok": "tiktok/transcript", "instagram": "instagram/transcript",
            "youtube": "youtube/video/transcript"}.get(platform)
    if not path:
        raise SociaVaultError(f"Transcripts are not available for {platform}")
    data = _get(path, {"url": url}, api_key)
    inner = _payload(data)
    for k in ("transcript", "transcripts", "captions", "segments", "subtitles", "text"):
        if inner.get(k):
            return inner[k]
    for k in ("transcript", "transcripts"):
        deep = _deep(data, k)
        if deep:
            return deep
    return ""                                     # a video with no speech or no captions: an honest empty result


# ---------- comments: what people said under a post ----------

def tiktok_comments(url: str, api_key: str, cursor=None) -> dict:
    params: dict[str, Any] = {"url": url}
    if cursor not in (None, ""):
        params["cursor"] = cursor
    return _get("tiktok/comments", params, api_key)


def instagram_comments(url: str, api_key: str, cursor=None) -> dict:
    params: dict[str, Any] = {"url": url}
    if cursor:
        params["cursor"] = cursor
    return _get("instagram/comments", params, api_key)


def youtube_comments(url: str, api_key: str, token=None) -> dict:
    params: dict[str, Any] = {"url": url, "order": "top"}
    if token:
        params["continuationToken"] = token
    return _get("youtube/video/comments", params, api_key)


def _listify(v) -> list:
    if isinstance(v, dict):
        return [x for x in v.values() if isinstance(x, dict)]
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _norm_comments(platform: str, page: dict, handle: str) -> tuple[list, Any]:
    """([{id, text, likes, replies, at, creator}], next_cursor) from one page, whatever the platform calls things."""
    d = _payload(page)
    items = _listify(d.get("comments"))
    out = []
    for c in items:
        if platform == "tiktok":
            user = c.get("user") or {}
            at = c.get("create_time")
            out.append({"id": str(c.get("cid") or ""), "text": c.get("text") or "", "likes": _n(c.get("digg_count")),
                        "replies": _n(c.get("reply_comment_total")),
                        "at": datetime.fromtimestamp(int(at), tz=timezone.utc) if str(at or "").isdigit() else None,
                        "creator": str(user.get("unique_id") or "").lower() == handle})
        elif platform == "instagram":
            user = c.get("user") or {}
            out.append({"id": str(c.get("id") or ""), "text": c.get("text") or "", "likes": _n(c.get("like_count")),
                        "replies": _n(c.get("child_comment_count")), "at": c.get("created_at"),
                        "creator": str(user.get("username") or "").lower() == handle})
        else:
            eng = c.get("engagement") or {}
            author = c.get("author") or {}
            if c.get("replyLevel"):
                continue                                  # replies belong to a thread, not to the video's reception
            out.append({"id": str(c.get("id") or ""), "text": c.get("content") or "", "likes": _n(eng.get("likes")),
                        "replies": _n(eng.get("replies")), "at": c.get("publishedTime"),
                        "creator": bool(author.get("isCreator"))})
    if platform == "tiktok":
        more = d.get("has_more") in (1, True, "1", "true")
        cursor = d.get("cursor") if more else None
    elif platform == "instagram":
        cursor = d.get("cursor") if items else None
    else:
        cursor = d.get("continuationToken") if items else None
    return [c for c in out if c["id"]], cursor


def post_url_for(platform: str, handle: str, external_id: str, stored: str = "") -> str:
    """The public URL of a post: what was stored at sync time, else rebuilt from the platform's id scheme."""
    if stored:
        return stored
    p = platform.lower()
    if not external_id:
        return ""
    if p.startswith("tiktok"):
        return f"https://www.tiktok.com/@{handle.lstrip('@')}/video/{external_id}"
    if p.startswith("youtube"):
        return f"https://www.youtube.com/watch?v={external_id}"
    if p.startswith("instagram"):
        code = ig_shortcode(external_id)
        return f"https://www.instagram.com/reel/{code}/" if code else ""
    return ""


COMMENT_PAGE_CAP = 5


def fetch_post_comments(workspace_id: int, post_id: int, pages: int = 2) -> dict:
    """Read a post's top comments (1 credit per ~15-20), classify them, and roll the result up onto the post."""
    from . import intent
    credit_gate(workspace_id, max(1, int(pages)))
    row = db.one(
        """SELECT p.id, p.external_id, p.url, a.handle, a.platform FROM posts p
           JOIN accounts a ON a.id = p.account_id WHERE p.id=%s AND p.workspace_id=%s""",
        (post_id, workspace_id))
    if not row:
        raise SociaVaultError("Post not found", 404)
    platform = canonical_platform(row["platform"]).lower()
    if platform not in ("tiktok", "instagram", "youtube"):
        raise SociaVaultError(f"Reading comments is supported for TikTok, Instagram and YouTube, not '{row['platform']}'.")
    url = post_url_for(platform, row["handle"], row["external_id"] or "", row["url"] or "")
    if not url:
        raise SociaVaultError("This post has no public URL to read comments from (it was not synced from a platform).")
    key = resolve_key(workspace_id)
    if not key:
        raise SociaVaultError("SociaVault API key not configured")
    fetch = {"tiktok": tiktok_comments, "instagram": instagram_comments, "youtube": youtube_comments}[platform]
    handle = row["handle"].lstrip("@").lower()
    credits, got, cursor = 0, [], None
    for _ in range(max(1, min(int(pages), COMMENT_PAGE_CAP))):
        page = fetch(url, key, cursor)
        credits += 1
        items, cursor = _norm_comments(platform, page, handle)
        got += items
        if not cursor or not items:
            break
    for c in got:
        if c["creator"]:
            continue                                   # the creator replying to their own audience is not demand
        verdict = intent.classify(c["text"])
        db.execute(
            """INSERT INTO post_comments (post_id, workspace_id, comment_id, body, likes, replies, posted_at, kind, score, matched)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (post_id, comment_id) DO UPDATE SET body=EXCLUDED.body, likes=EXCLUDED.likes,
                 replies=EXCLUDED.replies, kind=EXCLUDED.kind, score=EXCLUDED.score, matched=EXCLUDED.matched, fetched_at=now()""",
            (post_id, workspace_id, c["id"], c["text"][:1000], c["likes"], c["replies"], c["at"],
             verdict["kind"], verdict["score"], verdict["matched"]))
    record_credits(workspace_id, "comments", credits)
    return {**refresh_comment_summary(workspace_id, post_id), "read_now": len([c for c in got if not c["creator"]]),
            "credits_est": credits, "url": url}


def refresh_comment_summary(workspace_id: int, post_id: int) -> dict:
    """Recompute the per-post roll-up from the stored comments."""
    r = db.one(
        """SELECT count(*) AS fetched,
                  count(*) FILTER (WHERE kind = 'buy')  AS buy,
                  count(*) FILTER (WHERE kind = 'ask')  AS ask,
                  count(*) FILTER (WHERE kind = 'more') AS more
           FROM post_comments WHERE post_id=%s AND workspace_id=%s""", (post_id, workspace_id))
    db.execute(
        """UPDATE posts SET comments_fetched=%s, buy_comments=%s, ask_comments=%s, more_comments=%s,
               comments_fetched_at=now() WHERE id=%s AND workspace_id=%s""",
        (r["fetched"], r["buy"], r["ask"], r["more"], post_id, workspace_id))
    return {"fetched": r["fetched"], "buy": r["buy"], "ask": r["ask"], "more": r["more"], "intent": r["buy"] + r["ask"]}
