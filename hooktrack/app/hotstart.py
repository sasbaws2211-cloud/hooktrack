"""Hot-start alert: a young video has a very strong first day, so tell the owner while a matching video can still ride it.

The engine already labels such posts HOT_START (first-day views >= the winner threshold x the account's usual first
day). This turns that label into an email and a dashboard item. Each post is announced once (posts.hot_alerted_at),
only while it is still young, and one email per workspace carries all of that run's hot posts.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from . import db, logic

log = logging.getLogger("hooktrack.hotstart")

MAX_AGE_DAYS = 3        # after this the "act while it is hot" message is no longer true
MAX_LISTED = 3


def hot_posts(ws: int, unannounced_only: bool = False) -> list[dict]:
    """Young hooks pulled in by the sync that are HOT_START right now, strongest first."""
    perf = logic.performance(ws)
    cutoff = logic.today(ws) - timedelta(days=MAX_AGE_DAYS)
    rows = db.query(
        """SELECT p.id, p.hook, p.views, p.post_date, p.hot_alerted_at, a.handle FROM posts p
           LEFT JOIN accounts a ON a.id = p.account_id
           WHERE p.workspace_id=%s AND p.external_id IS NOT NULL AND p.post_date >= %s""", (ws, cutoff))
    out = []
    for r in rows:
        pf = perf.get(r["id"]) or {}
        if pf.get("label") != "HOT_START" or pf.get("early_multiple") is None:
            continue
        if unannounced_only and r["hot_alerted_at"] is not None:
            continue
        out.append({"id": r["id"], "hook": (r["hook"] or "").strip()[:140], "handle": r["handle"] or "",
                    "views": r["views"] or 0, "early_multiple": pf["early_multiple"], "post_date": r["post_date"]})
    out.sort(key=lambda h: -h["early_multiple"])
    return out


def _x(v) -> str:
    return f"{v:.1f}x"


def render(hot: list[dict], name: str, base: str, unsubscribe: str) -> tuple[str, str]:
    top = hot[0]
    subject = (f"{name}: a video is breaking out ({_x(top['early_multiple'])} its usual first day)" if len(hot) == 1
               else f"{name}: {len(hot)} videos are breaking out")
    lines = ["A video you posted is starting much faster than usual. The first day is the best time to make a "
             "matching one, while the idea is still getting pushed.", ""]
    for h in hot[:MAX_LISTED]:
        lines.append(f"  - {_x(h['early_multiple'])} its usual first day, {h['views']:,} views so far  "
                     f"@{h['handle'].lstrip('@')}: {h['hook'] or '(no hook text)'}")
    if len(hot) > MAX_LISTED:
        lines.append(f"  ...and {len(hot) - MAX_LISTED} more")
    lines += ["", f"Make a matching video: {base}/#/hooks", "",
              "It is a fast start, not a verdict: it can still fade, and HookTrack will tell you when it is settled.", "",
              f"Don't want these? Switch them off: {base}/#/hot-off/{unsubscribe}"]
    return subject, "\n".join(lines)


def notify(ws: int) -> dict:
    """Announce this workspace's new hot posts (once each). Never raises: it runs after a sync."""
    try:
        from . import auth, billing, digest, mailer
        hot = hot_posts(ws, unannounced_only=True)
        if not hot:
            return {"announced": 0, "sent": 0}
        # claim first, so two workers finishing syncs at the same moment cannot both email
        claimed = db.query("UPDATE posts SET hot_alerted_at=now() WHERE workspace_id=%s AND id = ANY(%s) "
                           "AND hot_alerted_at IS NULL RETURNING id", (ws, [h["id"] for h in hot]))
        ids = {r["id"] for r in claimed}
        hot = [h for h in hot if h["id"] in ids]
        if not hot or not mailer.configured() or billing.access_blocked(ws):
            return {"announced": len(hot), "sent": 0}
        name = (db.one("SELECT name FROM workspaces WHERE id=%s", (ws,)) or {}).get("name") or "Your workspace"
        sent = 0
        for r in db.query("""SELECT u.id, u.email FROM memberships m JOIN users u ON u.id=m.user_id
                             WHERE m.workspace_id=%s AND m.role IN ('owner','admin') AND NOT u.hot_off
                               AND (u.email_verified_at IS NOT NULL OR NOT %s)""", (ws, auth.verification_required())):
            subject, text = render(hot, name, mailer.public_url(), digest.unsubscribe_token(r["id"], "hot-off"))
            sent += 1 if mailer.send(r["email"], subject, text) else 0
        return {"announced": len(hot), "sent": sent}
    except Exception:
        log.exception("hot-start alert failed for workspace %s", ws)
        return {"announced": 0, "sent": 0}
