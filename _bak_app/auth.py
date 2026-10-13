"""Accounts, sessions, workspaces (tenants), members and invites."""
import base64
import hashlib
import hmac
import logging
import os
import shutil
import re
import secrets
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import db

router = APIRouter(prefix="/api")

COOKIE = "ht_session"
SESSION_DAYS = 30
INVITE_DAYS = 7
ROLES = ("owner", "admin", "member")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------- passwords & tokens ----------

def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = 2 ** 14, 8, 1
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    b = lambda x: base64.b64encode(x).decode()
    return f"scrypt${n}${r}${p}${b(salt)}${b(dk)}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got, base64.b64decode(dk))
    except Exception:
        return False


def _hash_token(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


# ---------- rate limiting (in Postgres: shared by every worker and survives restarts) ----------

def _hits(key: str, window: int) -> int:
    return db.one("SELECT COUNT(*) AS n FROM rate_limits WHERE key=%s AND at > now() - make_interval(secs => %s)",
                  (key[:300], window))["n"]


def _record(key: str):
    db.execute("INSERT INTO rate_limits(key) VALUES (%s)", (key[:300],))


def _blocked(key: str, limit: int, window: int = 600):
    """Refuse if `key` already has `limit` recorded events in the window (does not record one)."""
    if _hits(key, window) >= limit:
        raise HTTPException(429, "Too many attempts. Try again in a few minutes.")


def _throttle(key: str, limit: int, window: int = 600):
    """Count every call to `key`; refuse once it exceeds `limit` within the window."""
    _blocked(key, limit, window)
    _record(key)


def _clear(key: str):
    db.execute("DELETE FROM rate_limits WHERE key=%s", (key[:300],))


def reset_throttle():
    db.execute("DELETE FROM rate_limits")


def _client_ip(request: Request) -> str:
    """The caller's address. X-Forwarded-For is only believed when HOOKTRACK_TRUSTED_PROXIES=<n> says
    n proxies sit in front of the app; the entry n from the right is the one the first of them saw
    (anything further left is client-controlled and spoofable)."""
    direct = request.client.host if request.client else "?"
    try:
        n = int(os.environ.get("HOOKTRACK_TRUSTED_PROXIES", "0") or 0)
    except ValueError:
        n = 0
    if n > 0:
        hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
        if len(hops) >= n:
            return hops[-n]
    return direct


def cleanup_expired() -> dict:
    """Housekeeping: drop expired sessions/invites/reset tokens and old rate-limit rows."""
    return {
        "sessions": db.execute("DELETE FROM sessions WHERE expires_at < now()"),
        "invites": db.execute("DELETE FROM invites WHERE expires_at < now() - interval '30 days'"),
        "password_resets": db.execute("DELETE FROM password_resets WHERE expires_at < now()"),
        "rate_limits": db.execute("DELETE FROM rate_limits WHERE at < now() - interval '1 day'"),
    }


# ---------- sessions ----------

@dataclass
class Ctx:
    user_id: int
    email: str
    name: str
    workspace_id: Optional[int]
    role: Optional[str]


def _new_session(response: Response, request: Request, user_id: int, workspace_id: Optional[int]):
    token = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO sessions(token_hash, user_id, workspace_id, expires_at) "
        "VALUES (%s,%s,%s, now() + make_interval(days => %s))",
        (_hash_token(token), user_id, workspace_id, SESSION_DAYS))
    secure = os.environ.get("HOOKTRACK_SECURE_COOKIES") == "1" or request.url.scheme == "https"
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=secure,
                        max_age=SESSION_DAYS * 86400, path="/")


def session_ctx(request: Request) -> Optional[Ctx]:
    """The logged-in user and their active workspace, or None."""
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    row = db.one(
        "SELECT u.id, u.email, u.name, s.workspace_id, s.token_hash FROM sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.token_hash = %s AND s.expires_at > now()",
        (_hash_token(token),))
    if row is None:
        return None
    ws, role = row["workspace_id"], None
    if ws is not None:
        m = db.one("SELECT role FROM memberships WHERE workspace_id=%s AND user_id=%s", (ws, row["id"]))
        role = m["role"] if m else None
    if role is None:   # membership was removed: fall back to another workspace, if any
        m = db.one("SELECT workspace_id, role FROM memberships WHERE user_id=%s ORDER BY created_at LIMIT 1", (row["id"],))
        ws, role = (m["workspace_id"], m["role"]) if m else (None, None)
        db.execute("UPDATE sessions SET workspace_id=%s WHERE token_hash=%s", (ws, row["token_hash"]))
    return Ctx(row["id"], row["email"], row["name"], ws, role)


def current(request: Request) -> Ctx:
    ctx = session_ctx(request)
    if ctx is None:
        raise HTTPException(401, "Not signed in")
    if ctx.workspace_id is None:
        raise HTTPException(403, "You don't belong to any workspace")
    return ctx


def admin(ctx: Ctx = Depends(current)) -> Ctx:
    if ctx.role not in ("owner", "admin"):
        raise HTTPException(403, "Only workspace admins can do that")
    return ctx


def _me(ctx: Ctx) -> dict:
    wss = db.query(
        "SELECT w.id, w.name, w.timezone, m.role FROM memberships m JOIN workspaces w ON w.id = m.workspace_id "
        "WHERE m.user_id = %s ORDER BY w.name", (ctx.user_id,))
    cur = next((w for w in wss if w["id"] == ctx.workspace_id), None)
    from .platform_admin import is_platform_admin_email
    return {"user": {"id": ctx.user_id, "email": ctx.email, "name": ctx.name},
            "workspace": cur, "workspaces": wss,
            "platform_admin": is_platform_admin_email(ctx.email)}


# ---------- signup / login ----------

def _clean_email(email: str) -> str:
    e = (email or "").strip().lower()
    if len(e) > 254 or not EMAIL_RE.match(e):
        raise HTTPException(400, "Enter a valid email address")
    return e


def _check_password(pw: str):
    if len(pw or "") < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if len(pw) > 200:
        raise HTTPException(400, "Password is too long")


def _clean_tz(tz) -> str:
    from .logic import valid_tz
    tz = (tz or "").strip()
    return tz if valid_tz(tz) else "UTC"


def create_workspace(user_id: int, name: str, timezone: str = "UTC") -> int:
    ws = db.one("INSERT INTO workspaces(name, timezone) VALUES (%s,%s) RETURNING id",
                (name.strip(), _clean_tz(timezone)))["id"]
    db.execute("INSERT INTO memberships(workspace_id, user_id, role) VALUES (%s,%s,'owner')", (ws, user_id))
    return ws


class SignupIn(BaseModel):
    email: str
    password: str
    name: str = Field("", max_length=100)
    workspace_name: str = Field("", max_length=100)
    timezone: str = Field("", max_length=64)   # the browser's zone; unknown/invalid -> UTC


@router.post("/auth/signup")
def signup(b: SignupIn, request: Request, response: Response):
    _throttle("signup:" + _client_ip(request), 10, 3600)
    email = _clean_email(b.email)
    _check_password(b.password)
    if db.one("SELECT 1 FROM users WHERE email=%s", (email,)):
        raise HTTPException(409, "An account with that email already exists. Log in instead.")
    first_user = db.one("SELECT COUNT(*) AS n FROM users")["n"] == 0
    uid = db.one("INSERT INTO users(email, name, password_hash) VALUES (%s,%s,%s) RETURNING id",
                 (email, b.name.strip(), hash_password(b.password)))["id"]
    ws = create_workspace(uid, b.workspace_name.strip() or f"{b.name.strip() or email.split('@')[0]}'s workspace", b.timezone)
    if first_user:   # data created before multi-tenancy belongs to the first workspace
        for t in ("phones", "accounts", "daily_views", "posts"):
            db.execute(f"UPDATE {t} SET workspace_id = %s WHERE workspace_id IS NULL", (ws,))
        db.execute("SELECT ht_tighten_workspace_ids()")   # now that nothing is orphaned, require it going forward
    _new_session(response, request, uid, ws)
    return _me(Ctx(uid, email, b.name.strip(), ws, "owner"))


class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/auth/login")
def login(b: LoginIn, request: Request, response: Response):
    email = (b.email or "").strip().lower()
    ip = _client_ip(request)
    keys = ("login-ip:" + ip, f"login:{ip}:{email}", "login-email:" + email)     # per address, per address+email, per email
    for key, limit in zip(keys, (40, 8, 30)):
        _blocked(key, limit)
    u = db.one("SELECT * FROM users WHERE email=%s", (email,))
    # verify against a dummy hash when the user doesn't exist, to keep timing similar
    ok = verify_password(b.password, u["password_hash"] if u else hash_password("x"))
    if not u or not ok:
        for key in keys:
            _record(key)               # only failures count, so normal sign-ins never lock anyone out
        raise HTTPException(401, "Wrong email or password")
    _clear(keys[1])
    m = db.one("SELECT workspace_id, role FROM memberships WHERE user_id=%s ORDER BY created_at LIMIT 1", (u["id"],))
    ws = m["workspace_id"] if m else None
    _new_session(response, request, u["id"], ws)
    return _me(Ctx(u["id"], u["email"], u["name"], ws, m["role"] if m else None))


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        db.execute("DELETE FROM sessions WHERE token_hash=%s", (_hash_token(token),))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(request: Request):
    ctx = session_ctx(request)
    if ctx is None:
        raise HTTPException(401, "Not signed in")
    return _me(ctx)


RESET_HOURS = 1


class ForgotIn(BaseModel):
    email: str


@router.post("/auth/forgot")
def forgot_password(b: ForgotIn, request: Request, background: BackgroundTasks):
    """Always answers the same way, so it cannot be used to discover which emails have accounts."""
    from . import mailer
    ip = _client_ip(request)
    answer = {"ok": True, "email_configured": mailer.configured()}
    _throttle("forgot-ip:" + ip, 10, 3600)
    email = (b.email or "").strip().lower()[:254]
    if not EMAIL_RE.match(email):
        return answer
    try:
        _throttle("forgot-email:" + email, 3, 3600)
    except HTTPException:
        return answer                   # silently stop mailing someone who is being spammed
    u = db.one("SELECT id, email FROM users WHERE email=%s", (email,))
    if not u:
        return answer
    token = secrets.token_urlsafe(32)
    db.execute("DELETE FROM password_resets WHERE user_id=%s", (u["id"],))
    db.execute("INSERT INTO password_resets(token_hash, user_id, expires_at) "
               "VALUES (%s,%s, now() + make_interval(hours => %s))", (_hash_token(token), u["id"], RESET_HOURS))
    path = f"/#/reset/{token}"
    if mailer.configured():
        background.add_task(mailer.send, u["email"], "Reset your HookTrack password",
                            f"Someone asked to reset the password for {u['email']}.\n\n"
                            f"Open this link within {RESET_HOURS} hour to choose a new one:\n"
                            f"{mailer.public_url()}{path}\n\nIf that wasn't you, ignore this email.")
    else:
        # No email set up: the operator can read the link from the server log and pass it on.
        logging.getLogger("hooktrack.auth").warning(
            "password reset requested for %s (email not configured). Link, valid %sh: %s%s",
            u["email"], RESET_HOURS, mailer.public_url() or "http://<host>", path)
    return answer


def _reset_row(token: str):
    row = db.one("SELECT r.*, u.email FROM password_resets r JOIN users u ON u.id = r.user_id "
                 "WHERE r.token_hash=%s AND r.expires_at > now()", (_hash_token(token),))
    if row is None:
        raise HTTPException(404, "This reset link is invalid or has expired")
    return row


@router.get("/auth/reset/{token}")
def reset_preview(token: str, request: Request):
    _throttle("reset-ip:" + _client_ip(request), 60, 3600)
    return {"email": _reset_row(token)["email"]}


class ResetIn(BaseModel):
    token: str
    password: str


@router.post("/auth/reset")
def reset_password(b: ResetIn, request: Request):
    _throttle("reset-ip:" + _client_ip(request), 60, 3600)
    row = _reset_row(b.token)
    _check_password(b.password)
    db.execute("UPDATE users SET password_hash=%s WHERE id=%s", (hash_password(b.password), row["user_id"]))
    db.execute("DELETE FROM password_resets WHERE user_id=%s", (row["user_id"],))      # single use
    db.execute("DELETE FROM sessions WHERE user_id=%s", (row["user_id"],))             # sign out everywhere
    db.execute("DELETE FROM rate_limits WHERE key LIKE %s", (f"login:%:{row['email']}",))
    return {"ok": True}


class SwitchIn(BaseModel):
    workspace_id: int


@router.post("/auth/switch")
def switch(b: SwitchIn, request: Request):
    ctx = session_ctx(request)
    if ctx is None:
        raise HTTPException(401, "Not signed in")
    if not db.one("SELECT 1 FROM memberships WHERE workspace_id=%s AND user_id=%s", (b.workspace_id, ctx.user_id)):
        raise HTTPException(403, "Not a member of that workspace")
    db.execute("UPDATE sessions SET workspace_id=%s WHERE token_hash=%s",
               (b.workspace_id, _hash_token(request.cookies[COOKIE])))
    return _me(session_ctx(request))


class PasswordIn(BaseModel):
    current: str
    new: str


@router.post("/auth/password")
def change_password(b: PasswordIn, request: Request, ctx: Ctx = Depends(current)):
    _check_password(b.new)
    u = db.one("SELECT password_hash FROM users WHERE id=%s", (ctx.user_id,))
    if not verify_password(b.current, u["password_hash"]):
        raise HTTPException(400, "Current password is wrong")
    db.execute("UPDATE users SET password_hash=%s WHERE id=%s", (hash_password(b.new), ctx.user_id))
    db.execute("DELETE FROM sessions WHERE user_id=%s AND token_hash<>%s",
               (ctx.user_id, _hash_token(request.cookies[COOKIE])))
    return {"ok": True}


# ---------- workspaces ----------

class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    timezone: str = Field("", max_length=64)


class WorkspacePatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    timezone: Optional[str] = Field(None, max_length=64)


@router.post("/workspaces")
def workspace_create(b: WorkspaceIn, request: Request):
    ctx = session_ctx(request)
    if ctx is None:
        raise HTTPException(401, "Not signed in")
    ws = create_workspace(ctx.user_id, b.name, b.timezone)
    db.execute("UPDATE sessions SET workspace_id=%s WHERE token_hash=%s", (ws, _hash_token(request.cookies[COOKIE])))
    return _me(session_ctx(request))


@router.patch("/workspace")
def workspace_rename(b: WorkspacePatch, ctx: Ctx = Depends(admin)):
    """Rename the workspace and/or change its time zone (an IANA name such as Africa/Accra)."""
    if b.name is not None:
        db.execute("UPDATE workspaces SET name=%s WHERE id=%s", (b.name.strip(), ctx.workspace_id))
    if b.timezone is not None:
        from .logic import valid_tz
        if not valid_tz(b.timezone.strip()):
            raise HTTPException(400, "Unknown time zone. Use a name like Africa/Accra or America/New_York.")
        db.execute("UPDATE workspaces SET timezone=%s WHERE id=%s", (b.timezone.strip(), ctx.workspace_id))
    return _me(ctx)


UPLOAD_ROOT = __import__("pathlib").Path(__file__).parent.parent / "uploads"


def purge_workspace_files(ws: int):
    shutil.rmtree(UPLOAD_ROOT / str(ws), ignore_errors=True)


class WorkspaceDeleteIn(BaseModel):
    confirm_name: str
    password: str


@router.delete("/workspace")
def workspace_delete_own(b: WorkspaceDeleteIn, request: Request, ctx: Ctx = Depends(current)):
    """Owner-only, irreversible. Needs the workspace's exact name and the owner's password."""
    if ctx.role != "owner":
        raise HTTPException(403, "Only the workspace owner can delete it")
    _throttle(f"ws-delete:{ctx.user_id}", 5, 3600)
    w = db.one("SELECT name FROM workspaces WHERE id=%s", (ctx.workspace_id,))
    if b.confirm_name.strip() != w["name"]:
        raise HTTPException(400, "Type the workspace name exactly to confirm")
    u = db.one("SELECT password_hash FROM users WHERE id=%s", (ctx.user_id,))
    if not verify_password(b.password, u["password_hash"]):
        raise HTTPException(400, "Password is wrong")
    db.execute("UPDATE sessions SET workspace_id=NULL WHERE workspace_id=%s", (ctx.workspace_id,))
    db.execute("DELETE FROM workspaces WHERE id=%s", (ctx.workspace_id,))
    purge_workspace_files(ctx.workspace_id)
    return {"ok": True, "deleted": w["name"]}


@router.get("/workspace/members")
def members(ctx: Ctx = Depends(current)):
    return db.query(
        "SELECT u.id, u.email, u.name, m.role, m.created_at FROM memberships m JOIN users u ON u.id = m.user_id "
        "WHERE m.workspace_id = %s ORDER BY m.created_at", (ctx.workspace_id,))


def _owner_count(ws: int) -> int:
    return db.one("SELECT COUNT(*) AS n FROM memberships WHERE workspace_id=%s AND role='owner'", (ws,))["n"]


class RoleIn(BaseModel):
    role: str


@router.patch("/workspace/members/{user_id}")
def member_role(user_id: int, b: RoleIn, ctx: Ctx = Depends(admin)):
    if b.role not in ROLES:
        raise HTTPException(400, "Invalid role")
    target = db.one("SELECT role FROM memberships WHERE workspace_id=%s AND user_id=%s", (ctx.workspace_id, user_id))
    if target is None:
        raise HTTPException(404, "Member not found")
    if ctx.role != "owner" and "owner" in (b.role, target["role"]):
        raise HTTPException(403, "Only an owner can change owner roles")
    if target["role"] == "owner" and b.role != "owner" and _owner_count(ctx.workspace_id) <= 1:
        raise HTTPException(400, "A workspace needs at least one owner")
    db.execute("UPDATE memberships SET role=%s WHERE workspace_id=%s AND user_id=%s", (b.role, ctx.workspace_id, user_id))
    return {"ok": True}


@router.delete("/workspace/members/{user_id}")
def member_remove(user_id: int, ctx: Ctx = Depends(current)):
    leaving = user_id == ctx.user_id
    if not leaving and ctx.role not in ("owner", "admin"):
        raise HTTPException(403, "Only workspace admins can remove members")
    target = db.one("SELECT role FROM memberships WHERE workspace_id=%s AND user_id=%s", (ctx.workspace_id, user_id))
    if target is None:
        raise HTTPException(404, "Member not found")
    if target["role"] == "owner":
        if ctx.role != "owner":
            raise HTTPException(403, "Only an owner can remove an owner")
        if _owner_count(ctx.workspace_id) <= 1:
            raise HTTPException(400, "A workspace needs at least one owner")
    db.execute("DELETE FROM memberships WHERE workspace_id=%s AND user_id=%s", (ctx.workspace_id, user_id))
    db.execute("UPDATE sessions SET workspace_id=NULL WHERE user_id=%s AND workspace_id=%s", (user_id, ctx.workspace_id))
    return {"ok": True}


# ---------- invites ----------

class InviteIn(BaseModel):
    email: str
    role: str = "member"


@router.post("/workspace/invites")
def invite_create(b: InviteIn, ctx: Ctx = Depends(admin)):
    email = _clean_email(b.email)
    if b.role not in ("admin", "member"):
        raise HTTPException(400, "Invite as admin or member")
    if db.one("SELECT 1 FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=%s AND u.email=%s",
              (ctx.workspace_id, email)):
        raise HTTPException(409, "That person is already a member")
    db.execute("DELETE FROM invites WHERE workspace_id=%s AND email=%s", (ctx.workspace_id, email))
    token = secrets.token_urlsafe(24)
    db.execute("INSERT INTO invites(token_hash, workspace_id, email, role, invited_by, expires_at) "
               "VALUES (%s,%s,%s,%s,%s, now() + make_interval(days => %s))",
               (_hash_token(token), ctx.workspace_id, email, b.role, ctx.user_id, INVITE_DAYS))
    return {"email": email, "role": b.role, "path": f"/#/invite/{token}", "expires_days": INVITE_DAYS}


@router.get("/workspace/invites")
def invites(ctx: Ctx = Depends(admin)):
    return db.query("SELECT token_hash AS id, email, role, expires_at FROM invites "
                    "WHERE workspace_id=%s AND expires_at > now() ORDER BY created_at DESC", (ctx.workspace_id,))


@router.delete("/workspace/invites/{invite_id}")
def invite_revoke(invite_id: str, ctx: Ctx = Depends(admin)):
    if not db.execute("DELETE FROM invites WHERE token_hash=%s AND workspace_id=%s", (invite_id, ctx.workspace_id)):
        raise HTTPException(404, "Invite not found")
    return {"ok": True}


def _invite(token: str):
    row = db.one(
        "SELECT i.*, w.name AS workspace_name FROM invites i JOIN workspaces w ON w.id = i.workspace_id "
        "WHERE i.token_hash=%s AND i.expires_at > now()", (_hash_token(token),))
    if row is None:
        raise HTTPException(404, "This invite is invalid or has expired")
    return row


@router.get("/invites/{token}")
def invite_preview(token: str):
    i = _invite(token)
    return {"workspace_name": i["workspace_name"], "email": i["email"], "role": i["role"],
            "account_exists": bool(db.one("SELECT 1 FROM users WHERE email=%s", (i["email"],)))}


class AcceptIn(BaseModel):
    password: str = ""
    name: str = Field("", max_length=100)


@router.post("/invites/{token}/accept")
def invite_accept(token: str, b: AcceptIn, request: Request, response: Response):
    _throttle("invite:" + _client_ip(request), 20)
    i = _invite(token)
    user = db.one("SELECT * FROM users WHERE email=%s", (i["email"],))
    if user:
        ctx = session_ctx(request)
        if ctx is None or ctx.user_id != user["id"]:
            raise HTTPException(409, f"Log in as {i['email']} first, then open this invite again")
    else:
        _check_password(b.password)
        user = db.one("INSERT INTO users(email, name, password_hash) VALUES (%s,%s,%s) RETURNING *",
                      (i["email"], b.name.strip(), hash_password(b.password)))
    db.execute("INSERT INTO memberships(workspace_id, user_id, role) VALUES (%s,%s,%s) "
               "ON CONFLICT (workspace_id, user_id) DO NOTHING", (i["workspace_id"], user["id"], i["role"]))
    db.execute("DELETE FROM invites WHERE token_hash=%s", (i["token_hash"],))
    _new_session(response, request, user["id"], i["workspace_id"])
    role = db.one("SELECT role FROM memberships WHERE workspace_id=%s AND user_id=%s", (i["workspace_id"], user["id"]))["role"]
    return _me(Ctx(user["id"], user["email"], user["name"], i["workspace_id"], role))
