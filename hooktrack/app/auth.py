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

from . import db, emailguard, legal

router = APIRouter(prefix="/api")
log = logging.getLogger("hooktrack.auth")

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
        "email_verifications": db.execute("DELETE FROM email_verifications WHERE expires_at < now()"),
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
    ver = db.one("SELECT email_verified_at, digest_off, hot_off FROM users WHERE id=%s", (ctx.user_id,)) or {}
    return {"user": {"id": ctx.user_id, "email": ctx.email, "name": ctx.name,
                     "email_verified": ver.get("email_verified_at") is not None,
                     "digest": not ver.get("digest_off"), "hot_alerts": not ver.get("hot_off")},
            "verification_required": verification_required(),
            "workspace": cur, "workspaces": wss}


# ---------- email verification ----------

VERIFY_HOURS = 48
SETTING_REQUIRE_VERIFY = "require_email_verification"
SETTING_SIGNUP_IP_DAILY = "signup_ip_daily_limit"
DEFAULT_SIGNUP_IP_DAILY = 5


def _psetting(key: str, default):
    row = db.one("SELECT value FROM platform_settings WHERE key=%s", (key,))
    return default if not row or "value" not in row else row["value"]


def verification_required() -> bool:
    """On by default, but only enforceable when this server can actually send the email: otherwise nobody
    could ever confirm, so it stays off (the admin console says so)."""
    from . import mailer
    return mailer.configured() and bool(_psetting(SETTING_REQUIRE_VERIFY, True))


def workspace_verified_ok(ws: int) -> bool:
    """True when verification is not being enforced, or the workspace's owner has confirmed their email."""
    if not verification_required():
        return True
    return db.one("""SELECT 1 FROM memberships m JOIN users u ON u.id = m.user_id
                     WHERE m.workspace_id=%s AND m.role='owner' AND u.email_verified_at IS NOT NULL LIMIT 1""",
                  (ws,)) is not None


VERIFY_MESSAGE = ("Confirm your email address to start syncing. We sent you a link when you signed up: "
                  "use Resend on the banner at the top if you can't find it.")


def require_verified_workspace(ws: int) -> None:
    if not workspace_verified_ok(ws):
        raise HTTPException(403, VERIFY_MESSAGE)


def send_verification(user_id: int, email: str) -> bool:
    """Create a fresh link (older ones stop working) and email it. False when email isn't set up."""
    from . import mailer
    if not mailer.configured():
        return False
    token = secrets.token_urlsafe(32)
    db.execute("DELETE FROM email_verifications WHERE user_id=%s", (user_id,))
    db.execute("INSERT INTO email_verifications(token_hash, user_id, expires_at) "
               "VALUES (%s,%s, now() + make_interval(hours => %s))", (_hash_token(token), user_id, VERIFY_HOURS))
    return mailer.send(email, "Confirm your HookTrack email",
                       f"Welcome to HookTrack. Confirm this email address to start syncing your accounts:\n\n"
                       f"{mailer.public_url()}/#/verify/{token}\n\nThe link works for {VERIFY_HOURS} hours. "
                       f"If you didn't sign up, ignore this email.")


def _mark_verified(user_id: int) -> None:
    db.execute("UPDATE users SET email_verified_at = now() WHERE id=%s AND email_verified_at IS NULL", (user_id,))
    db.execute("DELETE FROM email_verifications WHERE user_id=%s", (user_id,))


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
    u = db.one("SELECT email FROM users WHERE id=%s", (user_id,))
    if u:
        from . import billing
        billing.claim_trial(u["email"], ws)        # one free trial per person, not per workspace
    return ws


class SignupIn(BaseModel):
    email: str
    password: str
    name: str = Field("", max_length=100)
    workspace_name: str = Field("", max_length=100)
    timezone: str = Field("", max_length=64)   # the browser's zone; unknown/invalid -> UTC


@router.post("/auth/signup")
def signup(b: SignupIn, request: Request, response: Response, background: BackgroundTasks):
    ip = _client_ip(request)
    _throttle("signup:" + ip, 10, 3600)
    email = _clean_email(b.email)
    emailguard.require_permanent(email)
    _check_password(b.password)
    if db.one("SELECT 1 FROM users WHERE email=%s", (email,)):
        raise HTTPException(409, "An account with that email already exists. Log in instead.")
    daily = int(_psetting(SETTING_SIGNUP_IP_DAILY, DEFAULT_SIGNUP_IP_DAILY) or 0)
    if daily > 0 and _hits("signup-day:" + ip, 86400) >= daily:    # only completed sign-ups count toward this
        raise HTTPException(429, "Too many accounts have been created from your network today. "
                                 "Try again tomorrow, or contact support if you need more.")
    first_user = db.one("SELECT COUNT(*) AS n FROM users")["n"] == 0
    uid = db.one("INSERT INTO users(email, name, password_hash, terms_accepted_at, terms_version) "
                 "VALUES (%s,%s,%s, now(), %s) RETURNING id",
                 (email, b.name.strip(), hash_password(b.password), legal.TERMS_VERSION))["id"]
    ws = create_workspace(uid, b.workspace_name.strip() or f"{b.name.strip() or email.split('@')[0]}'s workspace", b.timezone)
    if first_user:   # data created before multi-tenancy belongs to the first workspace
        for t in ("phones", "accounts", "daily_views", "posts"):
            db.execute(f"UPDATE {t} SET workspace_id = %s WHERE workspace_id IS NULL", (ws,))
        db.execute("SELECT ht_tighten_workspace_ids()")   # now that nothing is orphaned, require it going forward
    _record("signup-day:" + ip)
    if verification_required():
        background.add_task(send_verification, uid, email)
    _new_session(response, request, uid, ws)
    return _me(Ctx(uid, email, b.name.strip(), ws, "owner"))


class VerifyIn(BaseModel):
    token: str


@router.post("/auth/verify")
def verify_email(b: VerifyIn, request: Request):
    """Works signed out too (the link is opened from a mail app). The token is the proof."""
    _throttle("verify-ip:" + _client_ip(request), 60, 3600)
    row = db.one("SELECT v.user_id, u.email FROM email_verifications v JOIN users u ON u.id=v.user_id "
                 "WHERE v.token_hash=%s AND v.expires_at > now()", (_hash_token((b.token or "").strip()),))
    if row is None:
        raise HTTPException(404, "This confirmation link is invalid or has expired. Sign in and press Resend.")
    _mark_verified(row["user_id"])
    return {"ok": True, "email": row["email"]}


@router.post("/auth/resend-verification")
def resend_verification(background: BackgroundTasks, ctx: Ctx = Depends(current)):
    _throttle(f"resend-verify:{ctx.user_id}", 3, 3600)
    if db.one("SELECT 1 FROM users WHERE id=%s AND email_verified_at IS NOT NULL", (ctx.user_id,)):
        return {"ok": True, "already_verified": True}
    from . import mailer
    if not mailer.configured():
        raise HTTPException(503, "Email isn't set up on this server yet, so a confirmation link can't be sent.")
    background.add_task(send_verification, ctx.user_id, ctx.email)
    return {"ok": True}


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


class DigestPrefIn(BaseModel):
    enabled: bool


@router.put("/auth/digest")
def digest_pref(b: DigestPrefIn, ctx: Ctx = Depends(current)):
    db.execute("UPDATE users SET digest_off=%s WHERE id=%s", (not b.enabled, ctx.user_id))
    return {"digest": b.enabled}


class DigestOffIn(BaseModel):
    token: str


@router.post("/digest/unsubscribe")
def digest_unsubscribe(b: DigestOffIn, request: Request):
    """One click from the email, no sign-in needed: the token is an HMAC of the user id."""
    from . import digest
    _throttle("digest-off-ip:" + _client_ip(request), 60, 3600)
    uid = digest.user_from_token(b.token)
    if uid is None:
        raise HTTPException(404, "This link isn't valid")
    db.execute("UPDATE users SET digest_off=TRUE WHERE id=%s", (uid,))
    return {"ok": True}


@router.put("/auth/hot-alerts")
def hot_pref(b: DigestPrefIn, ctx: Ctx = Depends(current)):
    db.execute("UPDATE users SET hot_off=%s WHERE id=%s", (not b.enabled, ctx.user_id))
    return {"hot_alerts": b.enabled}


@router.post("/hot/unsubscribe")
def hot_unsubscribe(b: DigestOffIn, request: Request):
    from . import digest
    _throttle("digest-off-ip:" + _client_ip(request), 60, 3600)
    uid = digest.user_from_token(b.token, "hot-off")
    if uid is None:
        raise HTTPException(404, "This link isn't valid")
    db.execute("UPDATE users SET hot_off=TRUE WHERE id=%s", (uid,))
    return {"ok": True}


@router.post("/digest/preview")
def digest_preview(ctx: Ctx = Depends(admin)):
    """Build this workspace's digest now; email it to the caller when email is set up. Returns the text either way."""
    from . import digest, mailer
    d = digest.build(ctx.workspace_id)
    name = (db.one("SELECT name FROM workspaces WHERE id=%s", (ctx.workspace_id,)) or {}).get("name") or "Your workspace"
    subject, text = digest.render(d, name, mailer.public_url() or "http://localhost", digest.unsubscribe_token(ctx.user_id))
    sent = mailer.send(ctx.email, f"[Preview] {subject}", text) if mailer.configured() else False
    return {"subject": subject, "text": text, "sent": sent, "empty": d["empty"]}


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
    _mark_verified(row["user_id"])                                                     # they read mail sent to that address
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


# ---------- delete my account ----------

def account_deletion_plan(user_id: int) -> dict:
    """What deleting this person would do, workspace by workspace.
      delete  - they are its only member: the workspace and all its data go with them
      leave   - others remain and another owner exists: they are simply removed
      blocked - they are the only owner and others depend on it: needs a new owner first (or an explicit delete)"""
    rows = db.query(
        """SELECT w.id, w.name, m.role,
                  (SELECT COUNT(*) FROM memberships x WHERE x.workspace_id = w.id) AS members,
                  (SELECT COUNT(*) FROM memberships x WHERE x.workspace_id = w.id AND x.role = 'owner') AS owners
             FROM memberships m JOIN workspaces w ON w.id = m.workspace_id
            WHERE m.user_id = %s ORDER BY w.name""", (user_id,))
    from datetime import datetime, timezone
    out = []
    for r in rows:
        if r["members"] <= 1:
            action = "delete"
        elif r["role"] == "owner" and r["owners"] <= 1:
            action = "blocked"
        else:
            action = "leave"
        item = {"id": r["id"], "name": r["name"], "role": r["role"], "members": r["members"], "action": action}
        if action in ("delete", "blocked"):
            sub = db.one("SELECT plan_code, status, current_period_end, auto_renew, card_auth_enc "
                         "FROM workspace_subscriptions WHERE workspace_id=%s", (r["id"],))
            if sub and sub["plan_code"] != "free":
                item["paid_until"] = sub["current_period_end"]
                end = sub["current_period_end"]
                item["has_paid_plan"] = sub["status"] == "active" and (end is None or end > datetime.now(timezone.utc))
                item["saved_card"] = bool(sub["card_auth_enc"])
        out.append(item)
    return {"workspaces": out, "blocked": [w for w in out if w["action"] == "blocked"]}


def delete_account(user_id: int, delete_shared: bool = False) -> dict:
    """Permanently remove a person. Their sole-member workspaces are deleted with all data; shared ones they
    own alone are only deleted when `delete_shared` is set (otherwise the caller must hand over ownership)."""
    plan = account_deletion_plan(user_id)
    if plan["blocked"] and not delete_shared:
        names = ", ".join(w["name"] for w in plan["blocked"])
        raise HTTPException(409, f"You are the only owner of {names}, which other people use. Make another member "
                                 f"an owner first, or choose to delete those workspaces too.")
    u = db.one("SELECT email FROM users WHERE id=%s", (user_id,))
    if u is None:
        raise HTTPException(404, "Account not found")
    deleted, left = [], []
    for w in plan["workspaces"]:
        if w["action"] == "delete" or (w["action"] == "blocked" and delete_shared):
            db.execute("UPDATE sessions SET workspace_id=NULL WHERE workspace_id=%s", (w["id"],))
            db.execute("DELETE FROM workspaces WHERE id=%s", (w["id"],))
            purge_workspace_files(w["id"])
            deleted.append(w["name"])
        else:
            left.append(w["name"])
    db.execute("DELETE FROM invites WHERE email=%s", (u["email"],))     # invitations addressed to them
    db.execute("DELETE FROM users WHERE id=%s", (user_id,))           # memberships, sessions, resets cascade
    log.info("account %s deleted (workspaces deleted=%d left=%d)", user_id, len(deleted), len(left))
    return {"email": u["email"], "deleted_workspaces": deleted, "left_workspaces": left}


@router.get("/auth/account/deletion-preview")
def account_deletion_preview(request: Request):
    ctx = session_ctx(request)
    if ctx is None:
        raise HTTPException(401, "Not signed in")
    return account_deletion_plan(ctx.user_id)


class AccountDeleteIn(BaseModel):
    password: str
    confirm_email: str
    delete_shared_workspaces: bool = False


@router.delete("/auth/account")
def account_delete(b: AccountDeleteIn, request: Request, response: Response):
    """Self-service, irreversible. Needs the password and the account's email typed exactly."""
    ctx = session_ctx(request)
    if ctx is None:
        raise HTTPException(401, "Not signed in")
    _throttle(f"acct-delete:{ctx.user_id}", 5, 3600)
    if b.confirm_email.strip().lower() != ctx.email.lower():
        raise HTTPException(400, "Type your account email exactly to confirm")
    u = db.one("SELECT password_hash FROM users WHERE id=%s", (ctx.user_id,))
    if not verify_password(b.password, u["password_hash"]):
        raise HTTPException(400, "Password is wrong")
    out = delete_account(ctx.user_id, b.delete_shared_workspaces)
    try:
        from . import mailer
        mailer.send(out["email"], "Your HookTrack account was deleted",
                    "Your HookTrack account and the data in the workspaces it owned alone have been permanently "
                    "deleted. If you did not do this, contact us straight away.")
    except Exception:
        log.exception("account deletion email failed")
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True, "deleted_workspaces": out["deleted_workspaces"], "left_workspaces": out["left_workspaces"]}


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
    emailguard.require_permanent(email)
    if b.role not in ("admin", "member"):
        raise HTTPException(400, "Invite as admin or member")
    if db.one("SELECT 1 FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=%s AND u.email=%s",
              (ctx.workspace_id, email)):
        raise HTTPException(409, "That person is already a member")
    from . import billing
    billing.require_capacity(ctx.workspace_id, "members", replacing_invite_for=email)
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
        emailguard.require_permanent(i["email"])
        _check_password(b.password)
        user = db.one("INSERT INTO users(email, name, password_hash, terms_accepted_at, terms_version) "
                      "VALUES (%s,%s,%s, now(), %s) RETURNING *",
                      (i["email"], b.name.strip(), hash_password(b.password), legal.TERMS_VERSION))
    db.execute("INSERT INTO memberships(workspace_id, user_id, role) VALUES (%s,%s,%s) "
               "ON CONFLICT (workspace_id, user_id) DO NOTHING", (i["workspace_id"], user["id"], i["role"]))
    db.execute("DELETE FROM invites WHERE token_hash=%s", (i["token_hash"],))
    _new_session(response, request, user["id"], i["workspace_id"])
    role = db.one("SELECT role FROM memberships WHERE workspace_id=%s AND user_id=%s", (i["workspace_id"], user["id"]))["role"]
    return _me(Ctx(user["id"], user["email"], user["name"], i["workspace_id"], role))
