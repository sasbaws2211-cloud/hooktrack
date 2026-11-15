"""Comments and DMs on a connected account (yours or a client's), read and replied to through Zernio (Late).

Rides on the exact same OAuth connection publishing already uses (see latehub.py): nothing new to connect, no new
social login. An account only shows up here once it is connected for publishing (latehub.accounts(ws)).

Every endpoint and field name below is confirmed against Zernio's own openapi.json (fetched and read directly with
the real account key, read-only calls only: no real comment or message was ever posted while building this):
  - GET  /v1/inbox/comments                list posts, envelope {"data": [...]}, each: id, content, permalink, likeCount
  - GET  /v1/inbox/comments/{postId}       one post's comments, envelope {"comments": [...]}, each: id, message,
                                            createdTime, from: {name, username, picture}, likeCount (top-level
                                            comments only; threaded replies sit in a nested "replies" array, not
                                            pulled in here yet)
  - POST /v1/inbox/comments/{postId}       reply: body {accountId, message, commentId} - there is no separate
                                            .../{commentId}/reply path (an earlier version of this file guessed one
                                            and it does not exist: Zernio returns a clean 404 for it)
  - GET  /v1/inbox/conversations           list, envelope {"data": [...]}, each: id, participantName, participantPicture
  - GET  /v1/inbox/conversations/{id}/messages   envelope {"messages": [...]}, each: id, message
  - POST /v1/inbox/conversations/{id}/messages   send: body {accountId, message, attachmentUrl?}

Platform limits that are real, not bugs if you hit them: Instagram DMs need a human-typed reply within 24h of the
other person's last message (or a tagged message up to 7 days); TikTok DMs need a Business account outside the
EEA/Switzerland/UK and are reply-only. None of that is enforced here; Zernio's own API returns the real error.

X, LinkedIn and Facebook are uneven in a different way — not a transient error but a structural one, confirmed
against Zernio's own platform enums for the comments and messaging endpoints: X has no comment management at all
(DMs only), LinkedIn has no DM management at all (comments only), Facebook has both. Unlike the limits above,
this is known in advance, so it is enforced here (COMMENT_PLATFORMS / DM_PLATFORMS below) rather than left to
surface as a raw Zernio error on every refresh.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import db, latehub
from .auth import Ctx, admin, current
from .sociavault import platform_get, platform_set, platform_delete

log = logging.getLogger("hooktrack.inbox")
router = APIRouter(prefix="/api")

WEBHOOK_SECRET_ENC = "late_webhook_secret_enc"


class InboxError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def _explain(platform: str, e: "InboxError") -> "InboxError":
    """Turn a raw Zernio/platform error into something a user can actually act on, for the known platform rules.
    Anything not recognised is returned unchanged, raw error text and all, rather than hidden."""
    platform = (platform or "").strip().lower()
    msg = str(e)
    if platform == "tiktok" and "40001" in msg:
        return InboxError(
            "TikTok refused this (permission error, code 40001). TikTok DMs only work for a TikTok Business "
            "account, registered outside the EEA, Switzerland and the UK, connected through the newer Business "
            "app. If that's already true for this account, this needs a Zernio support ticket.", e.status)
    if platform == "tiktok" and e.status in (400, 403) and ("start" in msg.lower() or "initiat" in msg.lower()):
        return InboxError("TikTok only allows replying to a DM someone already sent you; a business account can "
                          "never send the first message in a conversation.", e.status)
    if platform == "instagram" and e.status in (400, 403) and ("window" in msg.lower() or "24" in msg or "expired" in msg.lower()):
        return InboxError("Instagram only allows a free reply within 24 hours of their last message to you "
                          "(or a tagged message, up to 7 days). This conversation is outside that window.", e.status)
    return e


def _headers() -> dict:
    if not latehub.available():
        raise InboxError("Publishing is not set up on this server yet.")
    return {"Authorization": f"Bearer {latehub.platform_key()}", "Content-Type": "application/json",
            "User-Agent": "HookTrack-Inbox/1.0"}


def _call(method: str, path: str, **kw) -> dict:
    # extra headers (e.g. Idempotency-Key) merge with the default ones; passing headers=... again as a **kw
    # alongside the hardcoded one below used to crash every call that needed it (reply, send) with a Python
    # TypeError, since a keyword argument cannot be given a value twice
    headers = {**_headers(), **kw.pop("headers", {})}
    with httpx.Client(timeout=30.0) as c:
        r = c.request(method, f"{latehub.base_url()}{path}", headers=headers, **kw)
    if r.status_code >= 400:
        raise InboxError(f"Zernio error {r.status_code}: {r.text[:200]}", r.status_code)
    return r.json() if r.content else {}


def _own_account(ws: int, account_id: int) -> dict:
    row = db.one("SELECT id, handle, platform FROM accounts WHERE id=%s AND workspace_id=%s", (account_id, ws))
    if not row:
        raise InboxError("That account is not in this workspace", 404)
    return row


# HookTrack's account.platform (display casing, "X"/"LinkedIn"/...) -> Zernio's own slug. "x" -> "twitter" is not
# a typo: Zernio's connect/account platform value for X has always been "twitter" (confirmed against its real
# OpenAPI spec), the same slug publish.py uses.
_LATE_PLATFORM_SLUGS = {
    "tiktok": "tiktok", "instagram": "instagram", "youtube": "youtube",
    "x": "twitter", "twitter": "twitter", "linkedin": "linkedin", "facebook": "facebook",
}
# Confirmed against Zernio's own comments/messaging platform enums, not guessed. Threads/Bluesky/Reddit also
# appear in Zernio's comments enum but HookTrack neither tracks nor publishes to them, so they are left out here.
COMMENT_PLATFORMS = {"tiktok", "instagram", "youtube", "linkedin", "facebook"}
DM_PLATFORMS = {"tiktok", "instagram", "facebook", "twitter"}


def _late_platform(account: dict) -> Optional[str]:
    return _LATE_PLATFORM_SLUGS.get(str(account["platform"] or "").strip().lower())


def _late_account_id(ws: int, account: dict) -> Optional[str]:
    """This HookTrack account's id on Zernio, matched by platform + handle (same convention as publish.py:
    nothing persists the mapping, since a disconnect/reconnect must not leave a stale id behind)."""
    key = _late_platform(account)
    if not key:
        return None
    h = (account["handle"] or "").lstrip("@").lower()
    same = [a for a in latehub.accounts(ws) if a["platform"] == key]
    pick = next((a for a in same if h and a["username"].lstrip("@").lower() == h), None) or (same[0] if same else None)
    return pick["id"] if pick else None


def connected_accounts(ws: int, capability: Optional[str] = None) -> list[dict]:
    """This workspace's tracked accounts that are also connected for publishing, so the inbox has something to
    read. capability='comments'|'messages' narrows to accounts whose platform actually supports that feature on
    Zernio (X has no comment management, LinkedIn has no DM management) — pass it whenever the caller is about
    to use one specific feature, not just listing accounts in general."""
    wanted = {"comments": COMMENT_PLATFORMS, "messages": DM_PLATFORMS}.get(capability)
    rows = db.query("SELECT id, handle, platform, client_id FROM accounts WHERE workspace_id=%s", (ws,))
    out = []
    for r in rows:
        key = _late_platform(r)
        if not key or (wanted is not None and key not in wanted):
            continue
        lid = _late_account_id(ws, r)
        if lid:
            out.append({**r, "late_account_id": lid})
    return out


# ---------------------------------------------------------------- comments
def refresh_comments(ws: int, account_id: Optional[int] = None) -> int:
    """Pull recent posts-with-comments for one account (or every connected account), then each post's own comments.
    Returns how many comment rows were new or changed. Safe to call often: Zernio's own list is cached ~10 min."""
    accts = [a for a in connected_accounts(ws, "comments") if (account_id is None or a["id"] == account_id)]
    changed = 0
    for a in accts:
        try:
            data = _call("GET", "/inbox/comments", params={"accountId": a["late_account_id"], "limit": 25})
        except InboxError as e:
            log.warning("inbox comments list failed for account %s: %s", a["id"], e)
            continue
        # confirmed against Zernio's own openapi.json: the list envelope is {"data": [...]}, each post's own
        # id/caption/url fields are "id"/"content"/"permalink" (not postId/title/url)
        posts = data.get("data") if isinstance(data, dict) else data
        for p in posts or []:
            post_id = str(p.get("id") or "")
            if not post_id:
                continue
            try:
                thread = _call("GET", f"/inbox/comments/{post_id}", params={"accountId": a["late_account_id"]})
            except InboxError as e:
                log.warning("inbox comment thread failed for post %s: %s", post_id, e)
                continue
            # this endpoint's envelope is {"comments": [...]}, distinct from the list call above; each comment's
            # body is "message" (not text/body), author is nested under "from": {name, username}. Threaded replies
            # come back nested under each comment's own "replies" array (Zernio's spec leaves that array's item
            # shape undeclared, so it is read defensively, assuming it matches a top-level comment's own shape).
            def _save(c: dict, parent_id: str) -> None:
                nonlocal changed
                cid = str(c.get("id") or "")
                if not cid:
                    return
                frm = c.get("from") or {}
                row = db.one(
                    """INSERT INTO inbox_comments(workspace_id, account_id, platform, post_external_id, post_title, post_url,
                                                   comment_id, parent_comment_id, author_name, author_avatar, body, like_count, posted_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (account_id, comment_id) DO UPDATE SET like_count=EXCLUDED.like_count, fetched_at=now()
                       RETURNING (xmax = 0) AS inserted""",
                    (ws, a["id"], a["platform"], post_id, p.get("content") or "", p.get("permalink") or "",
                     cid, parent_id, frm.get("name") or frm.get("username") or "",
                     frm.get("picture") or "", c.get("message") or "", c.get("likeCount"),
                     c.get("createdTime")))
                if row and row.get("inserted"):
                    changed += 1
                for reply in c.get("replies") or []:
                    _save(reply, cid)

            items = thread.get("comments") if isinstance(thread, dict) else thread
            for c in items or []:
                _save(c, "")
    return changed


def list_comments(ws: int, account_id: Optional[int] = None, unreplied_only: bool = False) -> list[dict]:
    """Threads grouped together: a reply sits directly under its own parent comment, not wherever its own timestamp
    would otherwise place it. Threads themselves are ordered by their most recent activity (a reply counts as
    activity on its parent's thread), so an old comment that just got a new reply rises back to the top."""
    where, params = ["ic.workspace_id=%s"], [ws]
    if account_id:
        where.append("ic.account_id=%s"); params.append(account_id)
    if unreplied_only:
        where.append("ic.replied_at IS NULL AND ic.hidden_at IS NULL")
    rows = db.query(
        f"""SELECT * FROM (
              SELECT ic.*, a.handle,
                     COALESCE(NULLIF(ic.parent_comment_id, ''), ic.comment_id) AS thread_key,
                     MAX(ic.posted_at) OVER (PARTITION BY ic.account_id, COALESCE(NULLIF(ic.parent_comment_id, ''), ic.comment_id)) AS thread_last_activity
              FROM inbox_comments ic JOIN accounts a ON a.id = ic.account_id
              WHERE {' AND '.join(where)}
            ) t
            ORDER BY t.thread_last_activity DESC NULLS LAST,
                     t.thread_key,
                     (t.parent_comment_id = '' OR t.parent_comment_id IS NULL) DESC,
                     t.posted_at ASC
            LIMIT 200""", params)
    return rows


def reply_to_comment(ws: int, comment_row_id: int, body: str) -> dict:
    row = db.one("SELECT ic.*, a.handle, a.platform AS acct_platform FROM inbox_comments ic "
                 "JOIN accounts a ON a.id = ic.account_id WHERE ic.id=%s AND ic.workspace_id=%s", (comment_row_id, ws))
    if not row:
        raise InboxError("Comment not found", 404)
    a = _own_account(ws, row["account_id"])
    lid = _late_account_id(ws, a)
    if not lid:
        raise InboxError("This account is not connected for publishing, so it has no inbox.", 409)
    body = (body or "").strip()
    if not body:
        raise InboxError("Write a reply first")
    # confirmed against Zernio's openapi.json: posting a reply is POST .../comments/{postId} (not a {commentId}/reply
    # sub-path) with the target comment named in the body's "commentId"
    key = str(uuid.uuid4())
    try:
        _call("POST", f"/inbox/comments/{row['post_external_id']}",
              json={"accountId": lid, "message": body, "commentId": row["comment_id"]},
              headers={**_headers(), "Idempotency-Key": key})
    except InboxError as e:
        raise _explain(a["platform"], e)
    db.execute("UPDATE inbox_comments SET replied_at=now(), reply_body=%s WHERE id=%s", (body, comment_row_id))
    return {"ok": True}


# ---------------------------------------------------------------- DMs (phase 2)
def refresh_conversations(ws: int, account_id: Optional[int] = None) -> int:
    accts = [a for a in connected_accounts(ws, "messages") if (account_id is None or a["id"] == account_id)]
    changed = 0
    for a in accts:
        try:
            data = _call("GET", "/inbox/conversations", params={"accountId": a["late_account_id"], "limit": 25})
        except InboxError as e:
            log.warning("inbox conversations list failed for account %s: %s", a["id"], e)
            continue
        # confirmed against Zernio's openapi.json: this list's envelope is {"data": [...]} too (same shape as the
        # comments list), id/participant fields are "id"/"participantName"/"participantPicture"; no confirmed
        # last-message preview/timestamp field on this list, so those two are left to fill in once a thread opens
        convos = data.get("data") if isinstance(data, dict) else data
        for c in convos or []:
            conv_id = str(c.get("id") or "")
            if not conv_id:
                continue
            row = db.one(
                """INSERT INTO inbox_conversations(workspace_id, account_id, platform, conversation_id, participant_name,
                                                    participant_avatar, last_message_at, last_message_preview, unread)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (account_id, conversation_id) DO UPDATE SET last_message_at=EXCLUDED.last_message_at,
                     last_message_preview=EXCLUDED.last_message_preview, unread=EXCLUDED.unread, fetched_at=now()
                   RETURNING id, (xmax = 0) AS inserted""",
                (ws, a["id"], a["platform"], conv_id, c.get("participantName") or "",
                 c.get("participantPicture") or "", c.get("lastMessageAt"), c.get("lastMessagePreview") or "",
                 bool(c.get("unread"))))
            if row and row.get("inserted"):
                changed += 1
    return changed


def list_conversations(ws: int, account_id: Optional[int] = None) -> list[dict]:
    where, params = ["ic.workspace_id=%s"], [ws]
    if account_id:
        where.append("ic.account_id=%s"); params.append(account_id)
    return db.query(f"SELECT ic.*, a.handle FROM inbox_conversations ic JOIN accounts a ON a.id = ic.account_id "
                    f"WHERE {' AND '.join(where)} ORDER BY ic.last_message_at DESC NULLS LAST LIMIT 200", params)


def list_messages(ws: int, conversation_row_id: int) -> list[dict]:
    conv = db.one("SELECT * FROM inbox_conversations WHERE id=%s AND workspace_id=%s", (conversation_row_id, ws))
    if not conv:
        raise InboxError("Conversation not found", 404)
    a = _own_account(ws, conv["account_id"])
    lid = _late_account_id(ws, a)
    if lid:
        try:
            data = _call("GET", f"/inbox/conversations/{conv['conversation_id']}/messages",
                        params={"accountId": lid, "limit": 100})
            # confirmed envelope: {"messages": [...]}; each message's id is "id" (the platform's own message id) and
            # its body is "message" (nullable when contentNotRetained); direction/timestamp field names were not
            # confirmed against the spec, so those two keep a defensive fallback
            items = data.get("messages") if isinstance(data, dict) else data
            for m in items or []:
                mid = str(m.get("id") or "")
                if not mid:
                    continue
                db.execute(
                    """INSERT INTO inbox_messages(conversation_id, workspace_id, message_id, direction, body, attachment_url, sent_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (conversation_id, message_id) DO NOTHING""",
                    (conversation_row_id, ws, mid, "out" if m.get("fromMe") or m.get("direction") == "out" else "in",
                     m.get("message") or "", m.get("attachmentUrl") or "", m.get("sentAt") or m.get("createdTime")))
        except InboxError as e:
            log.warning("inbox messages fetch failed for conversation %s: %s", conversation_row_id, e)
    return db.query("SELECT * FROM inbox_messages WHERE conversation_id=%s ORDER BY sent_at", (conversation_row_id,))


def send_message(ws: int, conversation_row_id: int, body: str) -> dict:
    conv = db.one("SELECT * FROM inbox_conversations WHERE id=%s AND workspace_id=%s", (conversation_row_id, ws))
    if not conv:
        raise InboxError("Conversation not found", 404)
    a = _own_account(ws, conv["account_id"])
    lid = _late_account_id(ws, a)
    if not lid:
        raise InboxError("This account is not connected for publishing, so it has no inbox.", 409)
    body = (body or "").strip()
    if not body:
        raise InboxError("Write a message first")
    # confirmed against Zernio's openapi.json: the body field is "message", not "text"
    key = str(uuid.uuid4())
    try:
        result = _call("POST", f"/inbox/conversations/{conv['conversation_id']}/messages",
                       json={"accountId": lid, "message": body}, headers={**_headers(), "Idempotency-Key": key})
    except InboxError as e:
        raise _explain(a["platform"], e)
    mid = str((result or {}).get("id") or (result or {}).get("messageId") or key)
    db.execute("INSERT INTO inbox_messages(conversation_id, workspace_id, message_id, direction, body, sent_at) "
               "VALUES (%s,%s,%s,'out',%s, now()) ON CONFLICT (conversation_id, message_id) DO NOTHING",
               (conversation_row_id, ws, mid, body))
    return {"ok": True}


# ---------------------------------------------------------------- webhook (optional: real-time instead of polling)
def webhook_secret() -> str:
    from . import crypto
    enc = platform_get(WEBHOOK_SECRET_ENC)
    if isinstance(enc, str) and enc:
        try:
            return (crypto.decrypt(enc) or "").strip()
        except Exception:
            return ""
    return ""


def save_webhook_secret(secret: str, clear: bool = False) -> None:
    from . import crypto
    if clear or not (secret or "").strip():
        platform_delete(WEBHOOK_SECRET_ENC)
        return
    platform_set(WEBHOOK_SECRET_ENC, crypto.encrypt(secret.strip()))


def verify_webhook(raw: bytes, signature: str) -> bool:
    secret = webhook_secret()
    if not secret or not signature:
        return False
    want = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, signature.strip().lower())


def handle_webhook(event: dict) -> None:
    """Applies one webhook event (comment.received or message.received). Unknown event types are ignored, not errors,
    since Zernio can add new ones. Dedup on event id is done by the caller (see inbox_webhook_events)."""
    kind = str(event.get("type") or event.get("event") or "")
    data = event.get("data") or {}
    if kind == "comment.received":
        # a single-comment version of refresh_comments' inner loop: upsert what the webhook actually sent
        acct = db.one("SELECT a.id, a.workspace_id FROM accounts a WHERE a.platform ILIKE %s AND a.handle ILIKE %s",
                      (data.get("platform", ""), "%" + (data.get("username") or "") + "%"))
        if not acct:
            return
        cid = str(data.get("commentId") or data.get("id") or "")
        if not cid:
            return
        db.execute(
            """INSERT INTO inbox_comments(workspace_id, account_id, platform, post_external_id, post_title, post_url,
                                          comment_id, author_name, body, posted_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (account_id, comment_id) DO NOTHING""",
            (acct["workspace_id"], acct["id"], data.get("platform", ""), str(data.get("postId") or ""),
             data.get("postTitle") or "", data.get("postUrl") or "", cid, data.get("authorName") or "",
             data.get("text") or "", data.get("createdAt")))
    elif kind == "message.received":
        conv_id = str(data.get("conversationId") or "")
        if not conv_id:
            return
        conv = db.one("SELECT id FROM inbox_conversations WHERE conversation_id=%s", (conv_id,))
        if not conv:
            return     # a brand-new conversation: picked up on the next refresh_conversations instead
        mid = str(data.get("messageId") or data.get("id") or "")
        if mid:
            db.execute("INSERT INTO inbox_messages(conversation_id, workspace_id, message_id, direction, body, sent_at) "
                       "SELECT %s, workspace_id, %s, 'in', %s, now() FROM inbox_conversations WHERE id=%s "
                       "ON CONFLICT (conversation_id, message_id) DO NOTHING", (conv["id"], mid, data.get("text") or "", conv["id"]))
            db.execute("UPDATE inbox_conversations SET unread=TRUE, last_message_at=now(), last_message_preview=%s WHERE id=%s",
                      ((data.get("text") or "")[:200], conv["id"]))


# ---------------------------------------------------------------- routes
class ReplyIn(BaseModel):
    body: str = Field(min_length=1, max_length=3000)


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=3000)


@router.get("/inbox/comments")
def api_list_comments(account_id: Optional[int] = None, unreplied_only: bool = False, refresh: bool = False,
                      ctx: Ctx = Depends(current)):
    try:
        if refresh:
            refresh_comments(ctx.workspace_id, account_id)
        return list_comments(ctx.workspace_id, account_id, unreplied_only)
    except InboxError as e:
        raise HTTPException(e.status or 400, str(e))


@router.post("/inbox/comments/{comment_row_id}/reply")
def api_reply_comment(comment_row_id: int, b: ReplyIn, ctx: Ctx = Depends(admin)):
    try:
        return reply_to_comment(ctx.workspace_id, comment_row_id, b.body)
    except InboxError as e:
        raise HTTPException(e.status or 400, str(e))


@router.get("/inbox/conversations")
def api_list_conversations(account_id: Optional[int] = None, refresh: bool = False, ctx: Ctx = Depends(current)):
    try:
        if refresh:
            refresh_conversations(ctx.workspace_id, account_id)
        return list_conversations(ctx.workspace_id, account_id)
    except InboxError as e:
        raise HTTPException(e.status or 400, str(e))


@router.get("/inbox/conversations/{conversation_row_id}/messages")
def api_list_messages(conversation_row_id: int, ctx: Ctx = Depends(current)):
    try:
        return list_messages(ctx.workspace_id, conversation_row_id)
    except InboxError as e:
        raise HTTPException(e.status or 400, str(e))


@router.post("/inbox/conversations/{conversation_row_id}/messages")
def api_send_message(conversation_row_id: int, b: MessageIn, ctx: Ctx = Depends(admin)):
    try:
        return send_message(ctx.workspace_id, conversation_row_id, b.body)
    except InboxError as e:
        raise HTTPException(e.status or 400, str(e))


@router.get("/inbox/accounts")
def api_inbox_accounts(capability: Optional[str] = None, ctx: Ctx = Depends(current)):
    """Which of this workspace's accounts actually have an inbox (connected for publishing). Pass
    capability=comments or capability=messages to narrow to accounts whose platform actually supports that
    feature (an X account has no comments to show; a LinkedIn account has no messages)."""
    if capability not in (None, "comments", "messages"):
        raise HTTPException(400, "capability must be 'comments' or 'messages'")
    return [{"id": a["id"], "handle": a["handle"], "platform": a["platform"], "client_id": a["client_id"]}
            for a in connected_accounts(ctx.workspace_id, capability)]


@router.post("/webhook/zernio")
async def zernio_webhook(request: Request):
    """Real-time delivery for comment.received / message.received, instead of polling. Optional: if no webhook
    secret is configured (Settings, or ask the admin to set one up with Zernio first), this 404s harmlessly."""
    if not webhook_secret():
        raise HTTPException(404, "Not configured")
    raw = await request.body()
    if not verify_webhook(raw, request.headers.get("x-zernio-signature", "")):
        raise HTTPException(401, "Bad signature")
    try:
        event = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "Bad body")
    event_id = str(event.get("id") or "")
    if event_id:
        n = db.execute("INSERT INTO inbox_webhook_events(event_id) VALUES (%s) ON CONFLICT DO NOTHING", (event_id,))
        if not n:
            return {"ok": True}      # already seen this one: Zernio retries are expected, not an error
    try:
        handle_webhook(event)
    except Exception:
        log.exception("inbox webhook event failed: %s", event.get("type"))
    return {"ok": True}
