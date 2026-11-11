"""YouTube through Google's official YouTube Data API v3, which is free: 10,000 units a day per Google Cloud project.

Every call HookTrack makes costs 1 unit (channels.list, playlistItems.list, videos.list), so one channel sync is about 2 to 3
units and the free allowance covers thousands of syncs a day. The API reads only PUBLIC data with a plain API key: views,
likes, comments, duration, title and publish date. Watch time and impressions need the channel owner's own login and are
not used. The API has no "this is a Short" flag, so short-form is decided by length (3 minutes or less).

The key is stored encrypted (admin console) or comes from YOUTUBE_API_KEY. It is never logged or sent to a browser.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import httpx

from . import crypto, db
from .sociavault import SociaVaultError, platform_delete, platform_get, platform_set

log = logging.getLogger("hooktrack.youtube")
BASE = "https://www.googleapis.com/youtube/v3"
TIMEOUT = 20.0
GOOGLE_DAILY_LIMIT = 10000            # Google's free allowance per project per day
DEFAULT_BUDGET = 9000                 # we stop here, leaving a margin for retries and tests
SHORT_FORM_MAX_S = 180                # YouTube Shorts can be up to 3 minutes
MIN_SYNC_GAP_S = 900                  # the same channel is not re-fetched more often than every 15 minutes
KEY_SETTING = "youtube_api_key_enc"
BUDGET_SETTING = "youtube_daily_budget"
_CHANNEL_ID = re.compile(r"^UC[\w-]{22}$")


class YouTubeError(SociaVaultError):
    """A YouTube API problem, reported through the same error type the sync code already handles."""
    provider = "youtube"


# ---------------- key and quota ----------------

def key() -> str:
    enc = platform_get(KEY_SETTING)
    if isinstance(enc, str) and enc:
        k = crypto.decrypt(enc)
        if k:
            return k
    return (os.environ.get("YOUTUBE_API_KEY") or "").strip()


def available() -> bool:
    return bool(key())


def budget() -> int:
    try:
        return max(100, min(GOOGLE_DAILY_LIMIT, int(platform_get(BUDGET_SETTING, DEFAULT_BUDGET))))
    except (TypeError, ValueError):
        return DEFAULT_BUDGET


def _day():
    """Google's quota day: it resets at midnight Pacific time."""
    return datetime.now(ZoneInfo("America/Los_Angeles")).date()


def used_today() -> int:
    r = db.one("SELECT units FROM youtube_quota WHERE day=%s", (_day(),))
    return int(r["units"]) if r else 0


def status() -> dict:
    enc = platform_get(KEY_SETTING)
    from_db = isinstance(enc, str) and bool(enc) and bool(crypto.decrypt(enc))
    k = key()
    return {"configured": bool(k), "source": "platform" if from_db else ("environment" if k else ""),
            "key_hint": ("…" + k[-4:]) if k else "", "used_today": used_today(), "budget": budget(),
            "google_limit": GOOGLE_DAILY_LIMIT}


def save_key(api_key: str, clear: bool = False) -> dict:
    if clear:
        platform_delete(KEY_SETTING)
    elif api_key and api_key.strip():
        k = api_key.strip()
        if not re.fullmatch(r"[A-Za-z0-9_\-]{20,80}", k):
            raise YouTubeError("That does not look like a Google API key.", 400)
        platform_set(KEY_SETTING, crypto.encrypt(k))
    return status()


def _spend(n: int = 1) -> None:
    """Count n units against today's budget, atomically; refuse when the budget would be passed."""
    row = db.one(
        """INSERT INTO youtube_quota(day, units) VALUES (%s, %s)
           ON CONFLICT (day) DO UPDATE SET units = youtube_quota.units + EXCLUDED.units
             WHERE youtube_quota.units + EXCLUDED.units <= %s
           RETURNING units""", (_day(), n, budget()))
    if row is None:
        raise YouTubeError("YouTube data is paused for today because the free daily allowance is used up. "
                           "It resumes by itself tomorrow; your existing data is unchanged.", 503)


def _mark_exhausted() -> None:
    db.execute("""INSERT INTO youtube_quota(day, units) VALUES (%s, %s)
                  ON CONFLICT (day) DO UPDATE SET units = GREATEST(youtube_quota.units, EXCLUDED.units)""",
               (_day(), GOOGLE_DAILY_LIMIT))


# ---------------- HTTP ----------------

def _http() -> httpx.Client:             # a seam: the tests replace this with a fake transport
    return httpx.Client(timeout=TIMEOUT)


def _get(path: str, params: dict) -> dict:
    k = key()
    if not k:
        raise YouTubeError("The YouTube API key is not set up on this server yet.", 503)
    _spend(1)
    try:
        with _http() as c:
            r = c.get(f"{BASE}/{path}", params={**params, "key": k})
    except httpx.TimeoutException:
        raise YouTubeError("YouTube did not answer in time. Try again in a moment.", 504)
    except httpx.HTTPError:
        raise YouTubeError("Could not reach YouTube. Try again in a moment.", 502)
    if r.status_code == 200:
        try:
            return r.json()
        except ValueError:
            raise YouTubeError("YouTube sent an unreadable answer.", 502)
    reason = ""
    try:
        reason = (r.json().get("error", {}).get("errors") or [{}])[0].get("reason", "")
    except Exception:
        pass
    if r.status_code == 403 and reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
        _mark_exhausted()
        raise YouTubeError("YouTube data is paused for today because the free daily allowance is used up. "
                           "It resumes by itself tomorrow; your existing data is unchanged.", 503)
    if r.status_code in (400, 403) and reason in ("keyInvalid", "badRequest", "accessNotConfigured", "forbidden", "ipRefererBlocked"):
        log.error("YouTube API key problem: %s", reason)
        raise YouTubeError("The YouTube connection is not set up correctly on this server. We have been told.", 503)
    if r.status_code == 404:
        raise YouTubeError("Not found on YouTube.", 404)
    log.warning("YouTube API HTTP %s (%s) for %s", r.status_code, reason, path)
    raise YouTubeError(f"YouTube returned an error ({r.status_code}). Try again later.", 502)


# ---------------- reading ----------------

_DUR = re.compile(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def parse_duration(iso: str) -> Optional[float]:
    """'PT1M5S' -> 65.0. None when there is no duration (live streams and premieres can lack one)."""
    m = _DUR.match(iso or "")
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    total = d * 86400 + h * 3600 + mi * 60 + s
    return float(total) if total > 0 else None             # "P0D" is what a live stream that has not ended reports


def _int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _channel_dict(item: dict) -> dict:
    sn, st, cd = item.get("snippet") or {}, item.get("statistics") or {}, item.get("contentDetails") or {}
    hidden = bool(st.get("hiddenSubscriberCount"))
    return {"id": item.get("id") or "", "title": sn.get("title") or "", "handle": (sn.get("customUrl") or "").lstrip("@"),
            "description": sn.get("description") or "", "subscribers": None if hidden else _int(st.get("subscriberCount")),
            "video_count": _int(st.get("videoCount")),
            "uploads_playlist": ((cd.get("relatedPlaylists") or {}).get("uploads")) or ""}


def channel(handle: str = "", channel_id: str = "") -> dict:
    """One public channel by @handle or channel id (UC...). Raises YouTubeError(404) when it does not exist."""
    h = (handle or "").lstrip("@").strip()
    parts = "snippet,statistics,contentDetails"
    if channel_id and _CHANNEL_ID.match(channel_id):
        data = _get("channels", {"part": parts, "id": channel_id})
    elif _CHANNEL_ID.match(h):
        data = _get("channels", {"part": parts, "id": h})
    else:
        if not h:
            raise YouTubeError("Enter a channel handle like @name.", 400)
        data = _get("channels", {"part": parts, "forHandle": "@" + h})
        if not data.get("items"):                   # an old-style username
            data = _get("channels", {"part": parts, "forUsername": h})
    items = data.get("items") or []
    if not items:
        raise YouTubeError("No YouTube channel with that name was found.", 404)
    return _channel_dict(items[0])


def channels(ids: list) -> list:
    """Many channels by id, 50 per call (1 unit per 50): for judging a list of candidates cheaply."""
    out = []
    ids = [i for i in ids if _CHANNEL_ID.match(i or "")]
    for i in range(0, len(ids), 50):
        data = _get("channels", {"part": "snippet,statistics,contentDetails", "id": ",".join(ids[i:i + 50]), "maxResults": 50})
        out += [_channel_dict(it) for it in data.get("items") or []]
    return out


def search_recent_shorts(query: str, published_after_iso: str, order: str = "viewCount", limit: int = 50) -> list:
    """Short videos (under 4 minutes) on a topic published since a date, best-performing first. search.list costs 100 units
    (not 1 like the other calls), so it is counted as such. Returns [{video_id, channel_id}]."""
    _spend(99)                                   # _get counts the other 1
    data = _get("search", {"part": "snippet", "type": "video", "videoDuration": "short", "q": query, "order": order,
                           "publishedAfter": published_after_iso, "maxResults": max(1, min(50, limit))})
    out = []
    for it in data.get("items") or []:
        vid = (it.get("id") or {}).get("videoId")
        cid = (it.get("snippet") or {}).get("channelId")
        if vid and cid:
            out.append({"video_id": vid, "channel_id": cid})
    return out


def uploads_playlist_for(channel_id: str) -> str:
    """The uploads playlist id is the channel id with UC changed to UU, so no extra call is needed once the id is known."""
    return "UU" + channel_id[2:] if _CHANNEL_ID.match(channel_id or "") else ""


def recent_video_ids(playlist_id: str, limit: int = 50) -> list:
    data = _get("playlistItems", {"part": "contentDetails", "playlistId": playlist_id, "maxResults": max(1, min(50, limit))})
    out = []
    for it in data.get("items") or []:
        vid = (it.get("contentDetails") or {}).get("videoId")
        if vid:
            out.append(vid)
    return out


def videos(ids: list) -> list:
    """Details for up to many video ids, 50 per call. Likes can be hidden by the owner and comments can be off: those are None."""
    out = []
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        data = _get("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(chunk), "maxResults": 50})
        for it in data.get("items") or []:
            sn, st, cd = it.get("snippet") or {}, it.get("statistics") or {}, it.get("contentDetails") or {}
            out.append({"id": it.get("id") or "", "title": (sn.get("title") or "").strip(), "description": (sn.get("description") or "").strip(),
                        "published": sn.get("publishedAt") or "", "channel_id": sn.get("channelId") or "",
                        "channel_title": sn.get("channelTitle") or "", "views": _int(st.get("viewCount")) or 0,
                        "likes": _int(st.get("likeCount")), "comments": _int(st.get("commentCount")),
                        "duration_s": parse_duration(cd.get("duration") or "")})
    return out


def is_short_form(duration_s: Optional[float]) -> bool:
    return duration_s is not None and 0 < duration_s <= SHORT_FORM_MAX_S


_VIDEO_ID = re.compile(r"(?:youtu\.be/|youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/|live/))([\w-]{11})")


def video_id_from_url(url: str) -> str:
    m = _VIDEO_ID.search(url or "")
    return m.group(1) if m else ""


def video_info(url: str) -> dict:
    """The shape the inspiration lookup uses, from one public video link (2 units: the video and its channel's handle)."""
    vid = video_id_from_url(url)
    if not vid:
        raise YouTubeError("That does not look like a YouTube video link.", 400)
    found = videos([vid])
    if not found:
        raise YouTubeError("That video could not be read. Check the link is public.", 404)
    v = found[0]
    handle = ""
    if v["channel_id"]:
        try:
            handle = channel(channel_id=v["channel_id"])["handle"]
        except YouTubeError:
            handle = ""
    return {"platform": "YouTube", "handle": handle or v["channel_title"], "caption": v["title"], "views": v["views"],
            "likes": v["likes"], "comments": v["comments"], "shares": None, "posted_iso": v["published"],
            "duration_s": v["duration_s"], "sound": ""}


def lookup(handle: str) -> dict:
    """For 'add an account': is this a real channel, and who is it? Never raises for 'not found'."""
    h = (handle or "").lstrip("@")
    try:
        ch = channel(handle=h)
    except YouTubeError as e:
        if e.status == 404:
            return {"found": False, "platform": "YouTube", "handle": "@" + h,
                    "message": f"No YouTube channel named @{h} was found. Check the spelling."}
        raise
    return {"found": True, "platform": "YouTube", "handle": "@" + (ch["handle"] or h), "display_name": ch["title"], "verified": False,
            "external_id": ch["id"], "followers": ch["subscribers"], "video_count": ch["video_count"],
            "bio": (ch["description"] or "")[:300], "credits_est": 0}
