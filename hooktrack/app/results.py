"""Connect a card the user made to the video it became, so its real results come back.

The user pastes the address of the posted video on the card (`url`). When the sync has pulled that video in, the card is
linked to it (`result_post_id`) and shows its views and how it did against its own account's normal and against the
hook it was made from. Linking never moves a card between columns: that stays the user's call.
"""
from __future__ import annotations

import re
from typing import Optional

from . import db, logic

_TT = re.compile(r"tiktok\.com/(?:@[^/?#]+/)?video/(\d{6,25})")
_YT = re.compile(r"(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|shorts/|embed/|live/)|youtu\.be/)([A-Za-z0-9_-]{11})")
_IG = re.compile(r"instagram\.com/(?:[^/?#]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]{5,})")


def key_of(url: str) -> str:
    """'tiktok:<id>' / 'youtube:<id>' / 'instagram:<code>' for a post address, or ''."""
    u = (url or "").strip()
    for name, rx in (("tiktok", _TT), ("youtube", _YT), ("instagram", _IG)):
        m = rx.search(u)
        if m:
            return f"{name}:{m.group(1)}"
    return ""


def _keys_for(row: dict) -> set[str]:
    keys = {key_of(row.get("url") or "")} - {""}
    ext = (row.get("external_id") or "").strip()
    if ext:
        keys |= {f"tiktok:{ext}", f"youtube:{ext}", f"instagram:{ext}"}
    return keys


def reconcile(ws: int) -> int:
    """Link unlinked cards that carry a posted-video address to the synced post with that address."""
    cards = db.query(
        "SELECT id, url FROM posts WHERE workspace_id=%s AND external_id IS NULL AND url <> '' AND result_post_id IS NULL",
        (ws,))
    wanted = {c["id"]: key_of(c["url"]) for c in cards}
    wanted = {i: k for i, k in wanted.items() if k}
    if not wanted:
        return 0
    synced = db.query("SELECT id, url, external_id FROM posts WHERE workspace_id=%s AND external_id IS NOT NULL", (ws,))
    by_key: dict[str, int] = {}
    for s in synced:
        for k in _keys_for(s):
            by_key.setdefault(k, s["id"])
    linked = 0
    for cid, k in wanted.items():
        sid = by_key.get(k)
        if sid:
            db.execute("UPDATE posts SET result_post_id=%s WHERE id=%s AND workspace_id=%s", (sid, cid, ws))
            linked += 1
    if linked:
        from . import cache
        cache.bump(ws)
    return linked


BEAT = 1.10          # at least 10% above the original's multiple
SHORT = 0.90         # at least 10% below it
SETTLE_DAYS = 3      # the made video's own results must be this old before a verdict means anything


def verdict_for(made: Optional[float], original: Optional[float]) -> str:
    """'beat' | 'matched' | 'fell_short' by comparing the made video's multiple with the original's, or ''."""
    if made is None or not original:
        return ""
    ratio = made / original
    return "beat" if ratio >= BEAT else "fell_short" if ratio <= SHORT else "matched"


def attach(ws: int, cards: list[dict], perf: Optional[dict] = None) -> None:
    """Put `result` on each card dict: its synced post's views and multiple, and the original hook's multiple."""
    ids = [c["result_post_id"] for c in cards if c.get("result_post_id")]
    if not ids:
        return
    perf = perf if perf is not None else logic.performance(ws)
    from datetime import timedelta
    cutoff = logic.today(ws) - timedelta(days=SETTLE_DAYS)
    rows = {r["id"]: r for r in db.query(
        "SELECT id, views, post_date, url FROM posts WHERE workspace_id=%s AND id = ANY(%s)", (ws, ids))}
    origin = {c["id"]: c.get("followup_of") or c.get("cloned_from") for c in cards}
    for c in cards:
        sid = c.get("result_post_id")
        r = rows.get(sid)
        if not r:
            continue
        pf = perf.get(sid) or {}
        opf = perf.get(origin.get(c["id"])) or {}
        c["result"] = {"post_id": sid, "views": r["views"], "url": r["url"], "multiple": pf.get("multiple"),
                       "label": pf.get("label"), "original_multiple": opf.get("multiple"),
                       "verdict": verdict_for(pf.get("multiple"), opf.get("multiple")),
                       "settled": r["post_date"] <= cutoff}


# ---- finding the video without a pasted link -------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9']{3,}")
_STOP = {"the", "and", "for", "you", "your", "this", "that", "with", "are", "was", "have", "from", "but", "not", "can",
         "will", "just", "its", "they", "what", "when", "who", "how", "all", "out", "get", "our", "has", "his", "her"}


def _tokens(*texts: str) -> set[str]:
    words = set()
    for t in texts:
        words |= {w for w in _WORD.findall((t or "").lower()) if w not in _STOP}
    return words


def candidates(ws: int, card_id: int, limit: int = 3) -> list[dict]:
    """Synced videos on the card's account that could be the video it became, best first.
    Only videos posted since the card was made, not already matched to another card, and never its own origin."""
    from datetime import timedelta
    c = db.one("SELECT * FROM posts WHERE id=%s AND workspace_id=%s", (card_id, ws))
    if not c or c.get("external_id") is not None:
        return []
    since = (c["created_at"].date() if c.get("created_at") else c["post_date"]) - timedelta(days=1)
    taken = {r["result_post_id"] for r in db.query(
        "SELECT result_post_id FROM posts WHERE workspace_id=%s AND result_post_id IS NOT NULL AND id <> %s", (ws, card_id))}
    origin = {c.get("followup_of"), c.get("cloned_from")} - {None}
    rows = db.query(
        """SELECT p.id, p.hook, p.caption, p.views, p.post_date, p.url, p.external_id, a.handle, a.platform
           FROM posts p LEFT JOIN accounts a ON a.id = p.account_id
           WHERE p.workspace_id=%s AND p.external_id IS NOT NULL AND p.account_id IS NOT DISTINCT FROM %s AND p.post_date >= %s
           ORDER BY p.post_date DESC LIMIT 60""", (ws, c["account_id"], since))
    mine = _tokens(c.get("hook"), c.get("caption"), c.get("body"))
    out = []
    for r in rows:
        if r["id"] in taken or r["id"] in origin:
            continue
        theirs = _tokens(r.get("hook"), r.get("caption"))
        sim = len(mine & theirs) / len(mine | theirs) if mine and theirs else 0.0
        days = max(0, (r["post_date"] - since).days)
        recency = max(0.0, 1.0 - days / 30)
        out.append({"post_id": r["id"], "hook": r["hook"] or "", "views": r["views"], "post_date": str(r["post_date"]),
                    "url": _address(r), "similarity": round(sim, 2), "score": round(0.7 * sim + 0.3 * recency, 3)})
    out.sort(key=lambda x: -x["score"])
    return out[:limit]


def _address(r: dict) -> str:
    from . import sociavault
    try:
        return sociavault.post_url_for(sociavault.canonical_platform(r.get("platform") or "").lower(),
                                       r.get("handle") or "", r.get("external_id") or "", r.get("url") or "") or ""
    except Exception:
        return r.get("url") or ""


def link(ws: int, card_id: int, post_id: int) -> dict:
    """Confirm a candidate: the card gets that video's address and results."""
    ok = [x for x in candidates(ws, card_id, limit=60) if x["post_id"] == post_id]
    if not ok:
        raise ValueError("That video cannot be matched to this card")
    db.execute("UPDATE posts SET result_post_id=%s, url=CASE WHEN url='' THEN %s ELSE url END "
               "WHERE id=%s AND workspace_id=%s", (post_id, ok[0]["url"], card_id, ws))
    return {"ok": True, "post_id": post_id}
