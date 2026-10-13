"""Accounts, sessions, workspaces (tenants), members and invites."""
import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
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


# ---------- tiny in-memory throttle for login/signup ----------

_attempts: dict = {}


def _throttle(key: str, limit: int, window: int = 600):
    now = time.time()
    hits = [t for t in _attempts.get(key, []) if now - t < window]
    if len(hits) >= limit:
        raise HTTPException(429, "Too many attempts. Try again in a few minutes.")
    hits.append(now)
    _attempts[key] = hits


def reset_throttle():
    _attempts.clear()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


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
        "SELECT w.id, w.name, m.role FROM memberships m JOIN workspaces w ON w.id = m.workspace_id "
        "WHERE m.user_id = %s ORDER BY w.name", (ctx.user_id,))
    cur = next((w for w in wss if w["id"] == ctx.workspace_id), None)
    return {"user": {"id": ctx.user_id, "email": ctx.email, "name": ctx.name},
            "workspace": cur, "workspaces": wss}


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


def create_workspace(user_id: int, name: str) -> int:
    ws = db.one("INSERT INTO workspaces(name) VALUES (%s) RETURNING id", (name.strip(),))["id"]
    db.execute("INSERT INTO memberships(workspace_id, user_id, role) VALUES (%s,%s,'owner')", (ws, user_id))
    return ws


class SignupIn(BaseModel):
    email: str
    password: str
    name: str = Field("", max_length=100)
    workspace_name: str = Field("", max_length=100)


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
    ws = create_workspace(uid, b.workspace_name.strip() or f"{b.name.strip() or email.split('@')[0]}'s workspace")
    if first_user:   # data created before multi-tenancy belongs to the first workspace
        for t in ("phones", "accounts", "daily_views", "posts"):
            db.execute(f"UPDATE {t} SET workspace_id = %s WHERE workspace_id IS NULL", (ws,))
    _new_session(response, request, uid, ws)
    return _me(Ctx(uid, email, b.name.strip(), ws, "owner"))


class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/auth/login")
def login(b: LoginIn, request: Request, response: Response):
    email = (b.email or "").strip().lower()
    _throttle("login-ip:" + _client_ip(request), 40)
    _throttle(f"login:{_client_ip(request)}:{email}", 8)
    u = db.one("SELECT * FROM users WHERE email=%s", (email,))
    # verify against a dummy hash when the user doesn't exist, to keep timing similar
    ok = verify_password(b.password, u["password_hash"] if u else hash_password("x"))
    if not u or not ok:
        raise HTTPException(401, "Wrong email or password")
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


@router.post("/workspaces")
def workspace_create(b: WorkspaceIn, request: Request):
    ctx = session_ctx(request)
    if ctx is None:
        raise HTTPException(401, "Not signed in")
    ws = create_workspace(ctx.user_id, b.name)
    db.execute("UPDATE sessions SET workspace_id=%s WHERE token_hash=%s", (ws, _hash_token(request.cookies[COOKIE])))
    return _me(session_ctx(request))


@router.patch("/workspace")
def workspace_rename(b: WorkspaceIn, ctx: Ctx = Depends(admin)):
    db.execute("UPDATE workspaces SET name=%s WHERE id=%s", (b.name.strip(), ctx.workspace_id))
    return _me(ctx)


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
