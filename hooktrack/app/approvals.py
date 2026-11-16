"""Client approval: before a post goes out to a client's own connected account, the client can be asked to sign
off on it first (on by default per client; an agency can turn it off for one they trust).

One open ("pending") request per post. The token is a report_links-style credential: a long random string is
the whole password for a no-login public page, and nothing short of that token gets in. Approving actually
schedules the post (via publish.publish_post with _approved=True, the one way past the gate in publish.py);
rejecting just records the client's note so the agency can fix it and send a new request.

If the schedule time has already passed by the time the client approves, or dispatch fails for any other
reason, publish_post's own queue/failure bookkeeping takes over: the attempt shows up wherever a normal failed
publish would (Scheduled > "Didn't go out"), so this module does not need its own failure-surfacing UI.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import auth, db, mailer, publish
from .auth import Ctx, current

router = APIRouter(prefix="/api")
pages = APIRouter()

NO_INDEX = {"X-Robots-Tag": "noindex, nofollow, noarchive", "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer"}


def _link_for(token: str) -> str:
    base = mailer.public_url()
    return f"{base}/a/{token}" if base else f"/a/{token}"


def _out(row: dict) -> dict:
    o = dict(row)
    for k in ("created_at", "decided_at", "viewed_at", "schedule_at"):
        if o.get(k) and hasattr(o[k], "isoformat"):
            o[k] = o[k].isoformat()
    o["platforms"] = [p for p in (row.get("platforms") or "").split(",") if p]
    o["link"] = _link_for(row["token"])
    return o


# ---------- workspace side ----------

class ApprovalIn(BaseModel):
    platforms: list[str] = Field(default_factory=list)
    schedule_at: Optional[str] = None
    schedule_local: Optional[str] = None


def create(ws: int, post_id: int, user_id: int, *, platforms: list[str],
           schedule_at: Optional[str] = None, schedule_local: Optional[str] = None) -> dict:
    post = db.one("SELECT id, account_id FROM posts WHERE id=%s AND workspace_id=%s", (post_id, ws))
    if not post:
        raise HTTPException(404, "Post not found")
    if not post.get("account_id"):
        raise HTTPException(400, "This post has no account yet")
    account = db.one("SELECT * FROM accounts WHERE id=%s AND workspace_id=%s", (post["account_id"], ws))
    if not account or not account.get("client_id"):
        raise HTTPException(400, "This account is not assigned to a client")
    client = db.one("SELECT * FROM clients WHERE id=%s AND workspace_id=%s", (account["client_id"], ws))
    if not client:
        raise HTTPException(404, "Client not found")
    platforms = [p.strip() for p in (platforms or []) if p and p.strip()]
    if not platforms:
        raise HTTPException(400, "Choose at least one platform")
    try:
        if schedule_local and not schedule_at:
            schedule_at = publish.local_to_utc_iso(ws, schedule_local)
        when = publish._parse_schedule(schedule_at)
    except publish.PublishError as e:
        raise HTTPException(400, str(e))
    if not when or when <= datetime.now(timezone.utc):
        raise HTTPException(400, "Pick a time in the future: a client needs a moment to review before it goes out")

    # supersede any still-open request for this post; the new one is the only live link from here on
    db.execute("UPDATE post_approvals SET status='cancelled' WHERE post_id=%s AND status='pending'", (post_id,))
    row = db.one(
        """INSERT INTO post_approvals(workspace_id, post_id, token, platforms, schedule_at, requested_by)
           VALUES (%s,%s,%s,%s,%s,%s) RETURNING *""",
        (ws, post_id, secrets.token_urlsafe(24), ",".join(platforms), when, user_id))
    out = _out(row)
    if client.get("contact_email") and mailer.configured():
        mailer.send(client["contact_email"], f"{client['name']}: a post is ready for your approval",
                     f"Take a look and approve it or ask for changes:\n\n{out['link']}\n\n"
                     f"This link is private to you — don't forward it.")
    return out


def list_all(ws: int, limit: int = 200) -> list[dict]:
    rows = db.query(
        """SELECT pa.*, p.hook, p.caption, a.handle, c.name AS client_name
           FROM post_approvals pa
           JOIN posts p ON p.id = pa.post_id
           LEFT JOIN accounts a ON a.id = p.account_id
           LEFT JOIN clients c ON c.id = a.client_id
           WHERE pa.workspace_id=%s ORDER BY pa.created_at DESC LIMIT %s""", (ws, limit))
    return [_out(r) for r in rows]


def cancel(ws: int, aid: int) -> dict:
    row = db.one(
        "UPDATE post_approvals SET status='cancelled' WHERE id=%s AND workspace_id=%s AND status='pending' RETURNING *",
        (aid, ws))
    if not row:
        raise HTTPException(404, "Open approval request not found")
    return _out(row)


def annotate(ws: int, posts: list[dict]) -> None:
    """Attaches each post's most recent approval request (pending, or the last decision) as post['approval']."""
    ids = [p["id"] for p in posts if p.get("id")]
    if not ids:
        return
    rows = db.query(
        """SELECT DISTINCT ON (post_id) * FROM post_approvals
           WHERE workspace_id=%s AND post_id = ANY(%s) ORDER BY post_id, created_at DESC""",
        (ws, ids))
    by = {r["post_id"]: _out(r) for r in rows}
    for p in posts:
        p["approval"] = by.get(p["id"])


@router.post("/posts/{pid}/approval")
def approval_create(pid: int, b: ApprovalIn, ctx: Ctx = Depends(current)):
    return create(ctx.workspace_id, pid, ctx.user_id, platforms=b.platforms,
                  schedule_at=b.schedule_at, schedule_local=b.schedule_local)


@router.get("/approvals")
def approvals_list(ctx: Ctx = Depends(current)):
    return {"approvals": list_all(ctx.workspace_id)}


@router.delete("/approvals/{aid}")
def approval_cancel(aid: int, ctx: Ctx = Depends(current)):
    return cancel(ctx.workspace_id, aid)


# ---------- the public side (no login: the token is the credential) ----------

def public_get(token: str) -> dict:
    row = db.one("SELECT * FROM post_approvals WHERE token=%s", (token[:100],))
    if not row:
        raise HTTPException(404, "This approval link is no longer active.")
    from . import billing
    if billing.access_blocked(row["workspace_id"]):
        raise HTTPException(404, "This approval link is no longer active.")
    post = db.one("SELECT hook, caption, body, video_url, video_file FROM posts WHERE id=%s", (row["post_id"],))
    account = db.one(
        "SELECT handle, platform FROM accounts WHERE id=(SELECT account_id FROM posts WHERE id=%s)",
        (row["post_id"],))
    ws_name = (db.one("SELECT name FROM workspaces WHERE id=%s", (row["workspace_id"],)) or {}).get("name") or ""
    video_url = (post or {}).get("video_url") or ""
    from . import media
    if post and post.get("video_file"):
        try:
            video_url = media.signed_url(row["workspace_id"], row["post_id"], ttl=7 * 24 * 3600)
        except ValueError:
            pass
    images = []
    try:
        images = media.signed_image_urls(row["workspace_id"], row["post_id"], ttl=7 * 24 * 3600)
    except ValueError:
        pass
    if row["status"] == "pending" and not row.get("viewed_at"):
        db.execute("UPDATE post_approvals SET viewed_at=now() WHERE id=%s", (row["id"],))
    out = _out(row)
    out.update({
        "prepared_by": ws_name,
        "handle": (account or {}).get("handle") or "",
        "hook": (post or {}).get("hook") or "",
        "caption": (post or {}).get("caption") or "",
        "body": (post or {}).get("body") or "",
        "video_url": video_url,
        "images": images,
    })
    out.pop("token", None)
    return out


class PublicDecisionIn(BaseModel):
    action: str
    note: str = Field("", max_length=1000)


def decide(token: str, action: str, note: str) -> dict:
    row = db.one("SELECT * FROM post_approvals WHERE token=%s", (token[:100],))
    if not row:
        raise HTTPException(404, "This approval link is no longer active.")
    if row["status"] != "pending":
        return {"status": row["status"], "already_decided": True}
    if action not in ("approve", "reject"):
        raise HTTPException(400, "Unknown action")
    note = (note or "").strip()[:1000]
    if action == "reject" and not note:
        raise HTTPException(400, "Say what needs to change")
    publish_error = ""
    if action == "approve":
        try:
            publish.publish_post(row["workspace_id"], row["post_id"], platforms=row["platforms"].split(","),
                                  schedule_at=row["schedule_at"].isoformat(), _approved=True)
        except publish.PublishError as e:
            # the client's decision still stands either way; publish_post can fail before it ever creates a
            # publish_jobs row (addon off, account disconnected, no video...), so that is not somewhere this
            # failure would otherwise show up — recorded here instead, for the agency to see and fix.
            publish_error = str(e)[:500]
    status = "approved" if action == "approve" else "rejected"
    db.execute("UPDATE post_approvals SET status=%s, decided_at=now(), client_note=%s, publish_error=%s WHERE id=%s",
               (status, note, publish_error, row["id"]))
    _notify_decision(row, status, note, publish_error)
    return {"ok": True, "status": status}


def _notify_decision(row: dict, status: str, note: str, publish_error: str) -> None:
    """Tells whoever sent the request what the client decided — otherwise the only way to find out is to keep
    checking the Clients page, and a decision that just sits there unnoticed defeats the point of asking."""
    if not row.get("requested_by") or not mailer.configured():
        return
    user = db.one("SELECT email FROM users WHERE id=%s", (row["requested_by"],))
    if not user or not user.get("email"):
        return
    post = db.one("SELECT hook, caption FROM posts WHERE id=%s", (row["post_id"],))
    what = (post or {}).get("hook") or (post or {}).get("caption") or "Your post"
    link = f"{mailer.public_url()}/app#/clients" if mailer.public_url() else "/app#/clients"
    if status == "approved" and publish_error:
        subject, body = f'"{what[:60]}" was approved, but didn\'t go out', (
            f"The client approved it, but scheduling failed:\n\n{publish_error}\n\nOpen Clients to see it and fix it:\n{link}")
    elif status == "approved":
        subject, body = f'"{what[:60]}" was approved', f"It will go out as scheduled, no action needed.\n\n{link}"
    else:
        subject, body = f'"{what[:60]}": changes requested', f'The client asked for changes:\n\n"{note}"\n\nOpen Clients to review and resend:\n{link}'
    mailer.send(user["email"], subject, body)


@router.get("/public/approval/{token}")
def public_approval(token: str, request: Request):
    auth._throttle("approval-ip:" + auth._client_ip(request), 240, 3600)
    return JSONResponse(public_get(token), headers=NO_INDEX)


@router.post("/public/approval/{token}/decide")
def public_approval_decide(token: str, b: PublicDecisionIn, request: Request):
    auth._throttle("approval-decide-ip:" + auth._client_ip(request), 60, 3600)
    return decide(token, b.action, b.note)


@pages.get("/a/{token}")
def approval_page(token: str):
    from .main import STATIC
    return FileResponse(STATIC / "approval.html", headers=NO_INDEX)
