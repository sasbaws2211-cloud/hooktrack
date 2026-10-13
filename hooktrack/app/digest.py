"""Weekly email digest: "this week: 2 winners, 3 cards due, your matching video beat its original".

Built only from numbers the app already holds. Sent on Monday morning in the workspace's own time zone, once per
workspace per week (workspaces.digest_week is the claim), to confirmed owners/admins who have not opted out.
Nothing to report = nothing sent: an empty digest teaches people to ignore the next one.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import db, logic

log = logging.getLogger("hooktrack.digest")

SEND_HOUR = 8                  # local time, Monday
WINDOW_DAYS = 7
SKIP_ACTIONS = {"growing", "waiting"}      # "wait a few days" items are not things to do
MAX_PER_TICK = 200


def _secret() -> bytes:
    from . import crypto
    return crypto._load_secret().encode()


def unsubscribe_token(user_id: int, purpose: str = "digest-off") -> str:
    sig = hmac.new(_secret(), f"{purpose}:{user_id}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{user_id}.{sig}"


def user_from_token(token: str, purpose: str = "digest-off"):
    try:
        uid_s, sig = (token or "").split(".", 1)
        uid = int(uid_s)
    except ValueError:
        return None
    return uid if hmac.compare_digest(unsubscribe_token(uid, purpose), f"{uid}.{sig}") else None


def build(ws: int) -> dict:
    """What happened in this workspace over the last week. Empty lists mean nothing to say."""
    from . import main as m, nextactions, results

    st = logic.get_settings(ws)
    today = logic.today(ws)
    perf = logic.performance(ws)
    since = today - timedelta(days=WINDOW_DAYS)

    rows = db.query(m.POST_SELECT + "WHERE p.workspace_id=%s AND p.external_id IS NOT NULL AND p.post_date > %s "
                    "ORDER BY p.views DESC", (ws, since))
    winners = []
    for r in rows:
        pf = perf.get(r["id"]) or {}
        if pf.get("label") == "WINNER":
            winners.append({"id": r["id"], "hook": (r.get("hook") or "").strip()[:140], "handle": r.get("handle") or "",
                            "views": r.get("views") or 0, "multiple": pf.get("multiple")})
    winners.sort(key=lambda w: -(w["multiple"] or 0))

    # made videos whose results just became trustworthy (3 days old .. 10 days old): each shows up exactly once
    cards = db.query(m.POST_SELECT + "WHERE p.workspace_id=%s AND p.external_id IS NULL AND p.result_post_id IS NOT NULL",
                     (ws,))
    cards = [m._post_out(c, st) for c in cards]
    results.attach(ws, cards, perf)
    beats = []
    for c in cards:
        res = c.get("result") or {}
        if res.get("verdict") != "beat" or not res.get("settled"):
            continue
        r = db.one("SELECT post_date FROM posts WHERE id=%s", (res["post_id"],))
        if r and r["post_date"] > today - timedelta(days=WINDOW_DAYS + results.SETTLE_DAYS):
            beats.append({"id": c["id"], "hook": (c.get("hook") or "").strip()[:140], "handle": c.get("handle") or "",
                          "made": res.get("multiple"), "original": res.get("original_multiple")})

    actions = [a for a in nextactions.build(ws) if a["key"] not in SKIP_ACTIONS]
    pace = nextactions.pace(ws)
    return {"workspace_id": ws, "winners": winners[:5], "winner_count": len(winners), "beats": beats[:5],
            "beat_count": len(beats), "actions": actions, "pace": pace,
            "empty": not (winners or beats or actions)}


def _x(v) -> str:
    return f"{v:.1f}x" if isinstance(v, (int, float)) else "?"


def render(d: dict, name: str, base: str, unsubscribe: str) -> tuple[str, str]:
    n_w, n_b = d["winner_count"], d["beat_count"]
    parts = []
    if n_w:
        parts.append(f"{n_w} winner{'' if n_w == 1 else 's'}")
    if n_b:
        parts.append(f"{n_b} matching video{'' if n_b == 1 else 's'} beat the original")
    due = sum(a["count"] for a in d["actions"])
    if due:
        parts.append(f"{due} thing{'' if due == 1 else 's'} to do")
    subject = f"{name}: " + ", ".join(parts) if parts else f"{name}: your week in HookTrack"
    lines = [f"Your week in {name}", ""]
    if d["winners"]:
        lines.append("WINNERS (beat their account's normal)")
        for w in d["winners"]:
            lines.append(f"  - {_x(w['multiple'])} normal, {w['views']:,} views  @{w['handle'].lstrip('@')}: {w['hook'] or '(no hook text)'}")
        if n_w > len(d["winners"]):
            lines.append(f"  ...and {n_w - len(d['winners'])} more")
        lines.append("")
    if d["beats"]:
        lines.append("MATCHING VIDEOS THAT BEAT THEIR ORIGINAL")
        for b in d["beats"]:
            lines.append(f"  - {_x(b['made'])} vs {_x(b['original'])}  @{b['handle'].lstrip('@')}: {b['hook'] or '(no hook text)'}")
        lines.append("")
    if d["actions"]:
        lines.append("WAITING FOR YOU")
        for a in d["actions"]:
            lines.append(f"  - {a['count']} {a['text']}")
        lines.append("")
    p = d["pace"]
    if p.get("target"):
        lines.append(f"This week so far: {p['done']} of {p['target']} videos filmed.")
        lines.append("")
    lines += [f"Open HookTrack: {base}/#/dashboard", "",
              f"Don't want this email? Switch it off: {base}/#/digest-off/{unsubscribe}"]
    return subject, "\n".join(lines)


def recipients(ws: int) -> list[dict]:
    from . import auth
    rows = db.query("""SELECT u.id, u.email, u.name FROM memberships m JOIN users u ON u.id=m.user_id
                       WHERE m.workspace_id=%s AND m.role IN ('owner','admin') AND NOT u.digest_off
                         AND (u.email_verified_at IS NOT NULL OR NOT %s)""",
                    (ws, auth.verification_required()))
    return rows


def send_for(ws: int, only_user: int | None = None) -> dict:
    """Build and send one workspace's digest. Returns {sent, skipped_empty, recipients}."""
    from . import mailer
    d = build(ws)
    if d["empty"]:
        return {"sent": 0, "skipped_empty": True, "recipients": 0}
    name = (db.one("SELECT name FROM workspaces WHERE id=%s", (ws,)) or {}).get("name") or "Your workspace"
    to = [r for r in recipients(ws) if only_user in (None, r["id"])]
    sent = 0
    for r in to:
        subject, text = render(d, name, mailer.public_url(), unsubscribe_token(r["id"]))
        if mailer.send(r["email"], subject, text):
            sent += 1
    return {"sent": sent, "skipped_empty": False, "recipients": len(to)}


def _local_now(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz if logic.valid_tz(tz) else "UTC"))


def run_due(force: bool = False) -> dict:
    """Hourly: send the digest to every workspace whose local Monday morning has come and which has not had this
    week's. force ignores the day/hour (the operator's 'send now') but still never sends twice in one call."""
    from . import billing, mailer
    if not mailer.configured():
        return {"workspaces": 0, "sent": 0, "skipped_empty": 0, "email_configured": False}
    rows = db.query("SELECT id, timezone, digest_week FROM workspaces ORDER BY id")
    out = {"workspaces": 0, "sent": 0, "skipped_empty": 0, "email_configured": True}
    for w in rows:
        if out["workspaces"] >= MAX_PER_TICK:
            break
        now = _local_now(w["timezone"] or "UTC")
        this_monday = logic.monday(now.date())
        if not force and not (now.weekday() == 0 and now.hour >= SEND_HOUR):
            continue
        if not force and w["digest_week"] == this_monday:
            continue
        # the claim is atomic, so with several workers each workspace is handled by exactly one of them
        if not force:
            got = db.one("UPDATE workspaces SET digest_week=%s WHERE id=%s AND digest_week IS DISTINCT FROM %s RETURNING id",
                         (this_monday, w["id"], this_monday))
            if got is None:
                continue
        try:
            if billing.access_blocked(w["id"]):
                continue
            r = send_for(w["id"])
        except Exception:
            log.exception("digest for workspace %s failed", w["id"])
            continue
        out["workspaces"] += 1
        out["sent"] += r["sent"]
        out["skipped_empty"] += 1 if r["skipped_empty"] else 0
    return out
