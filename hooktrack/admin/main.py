"""HookTrack platform admin: a standalone console for the operator of the whole deployment.

It is its own process with its own login (the `platform_admins` table, never a workspace user), its own cookie
and its own UI. It shares only the database and the `app` package with the customer app, so it can be run,
exposed and secured separately (e.g. on a private network or behind a VPN).

    python -m uvicorn admin.main:app --host 127.0.0.1 --port 8040

First admin: set HOOKTRACK_ADMIN_EMAIL + HOOKTRACK_ADMIN_PASSWORD (used only while no admin exists), or run
    python -m admin.create_admin you@company.com
"""
from __future__ import annotations

import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import activation, auth, backup, billing, db, digest, emailguard, legal, mailer, sociavault, vaultwatch

log = logging.getLogger("hooktrack.admin")
STATIC = Path(__file__).parent / "static"
COOKIE = "ht_admin"
SESSION_HOURS = 12
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 10


def bootstrap_admin() -> None:
    """Create the first admin from the environment, only while the table is empty."""
    email = (os.environ.get("HOOKTRACK_ADMIN_EMAIL") or "").strip().lower()
    pw = os.environ.get("HOOKTRACK_ADMIN_PASSWORD") or ""
    if not email or not pw or db.one("SELECT 1 FROM platform_admins LIMIT 1"):
        return
    if not EMAIL_RE.match(email) or len(pw) < MIN_PASSWORD:
        log.warning("HOOKTRACK_ADMIN_EMAIL/PASSWORD ignored: need a valid email and a %d+ character password", MIN_PASSWORD)
        return
    db.execute("INSERT INTO platform_admins(email, name, password_hash) VALUES (%s,%s,%s)",
               (email, "Admin", auth.hash_password(pw)))
    log.info("created first platform admin %s", email)


@asynccontextmanager
async def lifespan(_app):
    db.init_schema()
    bootstrap_admin()
    if not db.one("SELECT 1 FROM platform_admins LIMIT 1"):
        log.warning("No platform admin exists yet. Run: python -m admin.create_admin you@company.com")
    yield


app = FastAPI(title="HookTrack Platform Admin", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Cache-Control", "no-store")
    return resp


# ---------- auth ----------

class Admin(BaseModel):
    id: int
    email: str
    name: str


def _admin_from(request: Request) -> Optional[Admin]:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    row = db.one(
        "SELECT a.id, a.email, a.name FROM admin_sessions s JOIN platform_admins a ON a.id = s.admin_id "
        "WHERE s.token_hash=%s AND s.expires_at > now() AND NOT a.disabled", (auth._hash_token(token),))
    return Admin(**row) if row else None


def require_admin(request: Request) -> Admin:
    a = _admin_from(request)
    if a is None:
        raise HTTPException(401, "Not signed in")
    return a


class LoginIn(BaseModel):
    email: str
    password: str


@app.post("/api/auth/login")
def login(b: LoginIn, request: Request, response: Response):
    email = b.email.strip().lower()
    ip_key, em_key = f"admin-login-ip:{auth._client_ip(request)}", f"admin-login:{email}"
    auth._blocked(ip_key, 20)
    auth._blocked(em_key, 6)
    row = db.one("SELECT * FROM platform_admins WHERE email=%s AND NOT disabled", (email,))
    # verify against a dummy hash when the email is unknown so timing does not reveal which admins exist
    ok = auth.verify_password(b.password, row["password_hash"] if row else _DUMMY)
    if not row or not ok:
        auth._record(ip_key)
        auth._record(em_key)
        raise HTTPException(401, "Wrong email or password")
    auth._clear(em_key)
    token = secrets.token_urlsafe(32)
    db.execute("INSERT INTO admin_sessions(token_hash, admin_id, expires_at) VALUES (%s,%s, now() + make_interval(hours => %s))",
               (auth._hash_token(token), row["id"], SESSION_HOURS))
    db.execute("DELETE FROM admin_sessions WHERE expires_at < now()")
    secure = os.environ.get("HOOKTRACK_SECURE_COOKIES") == "1" or request.url.scheme == "https"
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=secure,
                        max_age=SESSION_HOURS * 3600, path="/")
    return {"id": row["id"], "email": row["email"], "name": row["name"]}


_DUMMY = auth.hash_password("not-a-real-password")


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        db.execute("DELETE FROM admin_sessions WHERE token_hash=%s", (auth._hash_token(token),))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(request: Request):
    a = _admin_from(request)
    return {"authenticated": a is not None, "admin": a.model_dump() if a else None}


class PasswordIn(BaseModel):
    current: str
    new: str = Field(min_length=MIN_PASSWORD, max_length=200)


@app.post("/api/auth/password")
def change_password(b: PasswordIn, a: Admin = Depends(require_admin)):
    row = db.one("SELECT password_hash FROM platform_admins WHERE id=%s", (a.id,))
    if not auth.verify_password(b.current, row["password_hash"]):
        raise HTTPException(400, "Current password is wrong")
    db.execute("UPDATE platform_admins SET password_hash=%s WHERE id=%s", (auth.hash_password(b.new), a.id))
    return {"ok": True}


# ---------- overview ----------

def _state_sql() -> tuple[str, tuple]:
    """CASE expression yielding a plan code | 'trial' | 'expired' per workspace (w = workspaces, s = subscriptions)."""
    at = billing._pget("billing_enabled_at")
    started = at if isinstance(at, str) and at else "1970-01-01T00:00:00+00:00"
    sql = """CASE
        WHEN s.plan_code IS NOT NULL AND s.plan_code <> 'free'
             AND (s.status = 'comp' OR s.current_period_end IS NULL OR s.current_period_end > now()) THEN s.plan_code
        WHEN s.plan_code IS NOT NULL AND s.plan_code <> 'free' THEN 'expired'
        WHEN NOT w.trial_eligible AND s.trial_ends_at IS NULL THEN 'expired'
        WHEN COALESCE(s.trial_ends_at, GREATEST(w.created_at, %s::timestamptz) + make_interval(days => %s)) > now()
             THEN 'trial'
        ELSE 'expired' END"""
    return sql, (started, billing.trial_config()["days"])


def _monthly_minor(price: int, interval: str) -> float:
    return price / 12 if interval == "year" else float(price)


@app.get("/api/overview")
def overview(_a: Admin = Depends(require_admin)):
    n = lambda sql: db.one(sql)["n"]
    paid = db.query(
        """SELECT p.code, p.name, p.price_minor, p.currency, p.interval, COUNT(*) AS n
           FROM workspace_subscriptions s JOIN plans p ON p.code = s.plan_code
           WHERE s.status = 'active' AND p.price_minor > 0 AND s.current_period_end > now()
           GROUP BY p.code, p.name, p.price_minor, p.currency, p.interval""")
    mrr: dict[str, float] = {}
    for r in paid:
        mrr[r["currency"]] = mrr.get(r["currency"], 0) + _monthly_minor(r["price_minor"], r["interval"]) * r["n"]
    revenue = db.query(
        """SELECT currency, SUM(amount_minor)::bigint AS minor, COUNT(*) AS n FROM payments
           WHERE status='success' AND paid_at > now() - interval '30 days' GROUP BY currency""")
    state_sql, state_args = _state_sql()
    dist = db.query(
        f"""SELECT {state_sql} AS plan, COUNT(*) AS n FROM workspaces w
            LEFT JOIN workspace_subscriptions s ON s.workspace_id = w.id GROUP BY 1 ORDER BY n DESC""", state_args)
    sched = sociavault.schedule_config()
    return {
        "counts": {
            "users": n("SELECT COUNT(*) AS n FROM users"), "workspaces": n("SELECT COUNT(*) AS n FROM workspaces"),
            "accounts": n("SELECT COUNT(*) AS n FROM accounts"), "posts": n("SELECT COUNT(*) AS n FROM posts"),
            "paid_workspaces": sum(r["n"] for r in paid),
            "ai_calls_this_month": db.one("SELECT COALESCE(SUM(calls),0) AS n FROM ai_usage WHERE month=%s",
                                          (billing._month(),))["n"],
        },
        "mrr_minor": {c: round(v) for c, v in mrr.items()},
        "revenue_30d": revenue,
        "plan_distribution": dist,
        "billing": {"enabled": billing.enabled(), "paystack": billing.paystack_status()},
        "legal_configured": legal.configured(),
        "recent_workspaces": db.query("SELECT id, name, created_at FROM workspaces ORDER BY created_at DESC LIMIT 8"),
        "recent_users": db.query("SELECT id, email, name, created_at FROM users ORDER BY created_at DESC LIMIT 8"),
        "vault": vaultwatch.state(),
        "system": {"sociavault_schedule_enabled": sched.get("enabled"),
                   "has_server_sociavault_key": bool(sociavault.resolve_server_key())},
    }


# ---------- workspaces ----------

def _plan_name(code): return code


@app.get("/api/workspaces")
def workspaces(q: str = "", limit: int = 100, _a: Admin = Depends(require_admin)):
    limit = max(1, min(limit, 500))
    like = f"%{q.strip()}%"
    state_sql, state_args = _state_sql()
    rows = db.query(
        f"""SELECT w.id, w.name, w.created_at,
                   (SELECT COUNT(*) FROM memberships m WHERE m.workspace_id = w.id) AS members,
                   (SELECT COUNT(*) FROM accounts a WHERE a.workspace_id = w.id) AS accounts,
                   (SELECT COUNT(*) FROM posts p WHERE p.workspace_id = w.id) AS posts,
                   s.status AS sub_status, s.current_period_end, {state_sql} AS state
            FROM workspaces w LEFT JOIN workspace_subscriptions s ON s.workspace_id = w.id
            WHERE w.name ILIKE %s ORDER BY w.created_at DESC LIMIT %s""", (*state_args, like, limit))
    for r in rows:
        r["plan_code"] = r["state"]
    return {"workspaces": rows}


@app.get("/api/workspaces/{wid}")
def workspace(wid: int, _a: Admin = Depends(require_admin)):
    w = db.one("SELECT id, name, timezone, created_at FROM workspaces WHERE id=%s", (wid,))
    if not w:
        raise HTTPException(404, "Workspace not found")
    members = db.query(
        """SELECT u.id, u.email, u.name, m.role, m.created_at FROM memberships m JOIN users u ON u.id = m.user_id
           WHERE m.workspace_id=%s ORDER BY m.role, u.email""", (wid,))
    return {
        "workspace": w, "members": members, "billing": billing.status(wid),
        "payments": db.query("SELECT * FROM payments WHERE workspace_id=%s ORDER BY created_at DESC LIMIT 20", (wid,)),
        "plans": billing.list_plans(),
    }


class RenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@app.patch("/api/workspaces/{wid}")
def workspace_rename(wid: int, b: RenameIn, _a: Admin = Depends(require_admin)):
    row = db.one("UPDATE workspaces SET name=%s WHERE id=%s RETURNING id, name", (b.name.strip(), wid))
    if not row:
        raise HTTPException(404, "Workspace not found")
    return row


class SubscriptionIn(BaseModel):
    plan_code: str
    status: str = "active"                   # active = paid until a date; comp = free grant (optional end)
    days: Optional[int] = Field(None, ge=1, le=3650)
    note: str = Field("", max_length=300)


@app.put("/api/workspaces/{wid}/subscription")
def workspace_subscription(wid: int, b: SubscriptionIn, _a: Admin = Depends(require_admin)):
    if not db.one("SELECT 1 FROM workspaces WHERE id=%s", (wid,)):
        raise HTTPException(404, "Workspace not found")
    if b.status not in ("active", "comp"):
        raise HTTPException(400, "status must be active or comp")
    plan = billing.get_plan(b.plan_code)
    if plan is None or b.plan_code == billing.FREE:
        raise HTTPException(400, f"Unknown plan '{b.plan_code}'. There is no free plan: pick a paid plan, or extend the trial.")
    end = None
    if b.days:
        end = datetime.now(timezone.utc) + timedelta(days=b.days)
    elif b.status == "active":
        end = billing._add_interval(datetime.now(timezone.utc), plan["interval"])
    billing.set_subscription(wid, b.plan_code, status=b.status, period_end=end, note=b.note.strip())
    return {"billing": billing.status(wid)}


class TrialIn(BaseModel):
    days: int = Field(ge=0, le=365)


@app.put("/api/workspaces/{wid}/trial")
def workspace_trial(wid: int, b: TrialIn, _a: Admin = Depends(require_admin)):
    """Give a workspace that has never paid `days` more trial from today (0 ends the trial now)."""
    if not db.one("SELECT 1 FROM workspaces WHERE id=%s", (wid,)):
        raise HTTPException(404, "Workspace not found")
    billing.set_trial_end(wid, b.days)
    return {"billing": billing.status(wid)}


@app.delete("/api/workspaces/{wid}/card")
def workspace_card_remove(wid: int, _a: Admin = Depends(require_admin)):
    """Stop automatic charging for a workspace (e.g. a customer asked, or a card is being abused)."""
    if not db.one("SELECT 1 FROM workspaces WHERE id=%s", (wid,)):
        raise HTTPException(404, "Workspace not found")
    billing.remove_card(wid)
    return {"billing": billing.status(wid)}


@app.delete("/api/workspaces/{wid}")
def workspace_delete(wid: int, _a: Admin = Depends(require_admin)):
    w = db.one("SELECT id, name FROM workspaces WHERE id=%s", (wid,))
    if not w:
        raise HTTPException(404, "Workspace not found")
    db.execute("UPDATE sessions SET workspace_id=NULL WHERE workspace_id=%s", (wid,))
    db.execute("DELETE FROM workspaces WHERE id=%s", (wid,))
    auth.purge_workspace_files(wid)
    return {"ok": True, "deleted": w["name"], "id": wid}


# ---------- users ----------

@app.get("/api/users")
def users(q: str = "", limit: int = 100, _a: Admin = Depends(require_admin)):
    limit = max(1, min(limit, 500))
    like = f"%{q.strip()}%"
    rows = db.query(
        """SELECT u.id, u.email, u.name, u.created_at,
                  (SELECT COUNT(*) FROM memberships m WHERE m.user_id = u.id) AS workspaces
           FROM users u WHERE u.email ILIKE %s OR u.name ILIKE %s ORDER BY u.created_at DESC LIMIT %s""",
        (like, like, limit))
    return {"users": rows}


@app.get("/api/users/{uid}/deletion-preview")
def user_deletion_preview(uid: int, _a: Admin = Depends(require_admin)):
    if not db.one("SELECT 1 FROM users WHERE id=%s", (uid,)):
        raise HTTPException(404, "User not found")
    return auth.account_deletion_plan(uid)


class UserDeleteIn(BaseModel):
    delete_shared_workspaces: bool = False


@app.delete("/api/users/{uid}")
def user_delete(uid: int, b: UserDeleteIn, _a: Admin = Depends(require_admin)):
    """Erase a person on request (e.g. a data-protection request). Same rules as self-service."""
    if not db.one("SELECT 1 FROM users WHERE id=%s", (uid,)):
        raise HTTPException(404, "User not found")
    out = auth.delete_account(uid, b.delete_shared_workspaces)
    return {"ok": True, "deleted_workspaces": out["deleted_workspaces"], "left_workspaces": out["left_workspaces"]}


@app.get("/api/users/{uid}")
def user(uid: int, _a: Admin = Depends(require_admin)):
    u = db.one("SELECT id, email, name, created_at FROM users WHERE id=%s", (uid,))
    if not u:
        raise HTTPException(404, "User not found")
    memberships = db.query(
        """SELECT w.id, w.name, m.role, m.created_at FROM memberships m JOIN workspaces w ON w.id = m.workspace_id
           WHERE m.user_id=%s ORDER BY w.name""", (uid,))
    return {"user": u, "memberships": memberships}


# ---------- plans & payments ----------

class PlanIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field("", max_length=300)
    price_minor: int = Field(0, ge=0, le=100_000_000)
    currency: str = Field("USD", min_length=3, max_length=3)
    interval: str = "month"
    max_accounts: int = Field(3, ge=-1)
    max_members: int = Field(2, ge=-1)
    max_ai_calls: int = Field(50, ge=-1)
    max_connected: int = Field(1, ge=-1)
    max_credits: int = Field(150, ge=-1)
    max_reports: int = Field(0, ge=-1)
    active: bool = True
    position: int = 0


class PlanCreate(PlanIn):
    code: str


def _check_plan(b: PlanIn, code: str):
    if b.interval not in ("month", "year"):
        raise HTTPException(400, "interval must be month or year")
    if code == billing.FREE:
        raise HTTPException(400, "There is no free plan. New workspaces get a trial; set its length under Settings.")
    if b.price_minor <= 0:
        raise HTTPException(400, "A plan must have a price above zero. Use the trial for free access.")


@app.get("/api/plans")
def plans(_a: Admin = Depends(require_admin)):
    rows = billing.list_plans()
    subs = {r["plan_code"]: r["n"] for r in db.query(
        "SELECT plan_code, COUNT(*) AS n FROM workspace_subscriptions GROUP BY plan_code")}
    for r in rows:
        r["subscribers"] = subs.get(r["code"], 0)
    return {"plans": rows}


@app.post("/api/plans")
def plan_create(b: PlanCreate, _a: Admin = Depends(require_admin)):
    code = b.code.strip().lower()
    if not billing.CODE_RE.match(code):
        raise HTTPException(400, "Code: letters, numbers, - or _ (up to 32), e.g. 'team'")
    if code == billing.FREE:
        raise HTTPException(400, "There is no free plan. New workspaces get a trial; set its length under Settings.")
    if billing.get_plan(code):
        raise HTTPException(409, "A plan with that code already exists")
    _check_plan(b, code)
    return db.one(
        """INSERT INTO plans(code,name,description,price_minor,currency,interval,max_accounts,max_members,max_ai_calls,
                             max_connected,max_credits,max_reports,active,position)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
        (code, b.name.strip(), b.description.strip(), b.price_minor, b.currency.upper(), b.interval,
         b.max_accounts, b.max_members, b.max_ai_calls, b.max_connected, b.max_credits, b.max_reports, b.active, b.position))


@app.put("/api/plans/{code}")
def plan_update(code: str, b: PlanIn, _a: Admin = Depends(require_admin)):
    if not billing.get_plan(code) or code == billing.FREE:
        raise HTTPException(404, "Plan not found")
    _check_plan(b, code)
    return db.one(
        """UPDATE plans SET name=%s, description=%s, price_minor=%s, currency=%s, interval=%s, max_accounts=%s,
             max_members=%s, max_ai_calls=%s, max_connected=%s, max_credits=%s, max_reports=%s, active=%s, position=%s
           WHERE code=%s RETURNING *""",
        (b.name.strip(), b.description.strip(), b.price_minor, b.currency.upper(), b.interval, b.max_accounts,
         b.max_members, b.max_ai_calls, b.max_connected, b.max_credits, b.max_reports, b.active, b.position, code))


class ExtrasIn(BaseModel):
    extra_connected: int = Field(0, ge=0, le=1000)
    extra_credits: int = Field(0, ge=0, le=10_000_000)


@app.put("/api/workspaces/{wid}/extras")
def workspace_extras(wid: int, b: ExtrasIn, _a: Admin = Depends(require_admin)):
    """Add-ons on top of the plan: extra connected publishing accounts and extra monthly credits."""
    if not db.one("SELECT 1 FROM workspaces WHERE id=%s", (wid,)):
        raise HTTPException(404, "Workspace not found")
    return billing.set_extras(wid, b.extra_connected, b.extra_credits)


@app.get("/api/payments")
def payments(status: str = "", limit: int = 100, _a: Admin = Depends(require_admin)):
    limit = max(1, min(limit, 500))
    if status in ("pending", "success", "failed"):
        rows = db.query("SELECT * FROM payments WHERE status=%s ORDER BY created_at DESC LIMIT %s", (status, limit))
    else:
        rows = db.query("SELECT * FROM payments ORDER BY created_at DESC LIMIT %s", (limit,))
    return {"payments": rows}


# ---------- settings ----------

class BillingSettingsIn(BaseModel):
    enabled: Optional[bool] = None
    paystack_key: Optional[str] = None
    clear_paystack_key: bool = False


class LegalIn(BaseModel):
    company: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    jurisdiction: Optional[str] = None
    refund_policy: Optional[str] = None


@app.put("/api/settings/legal")
def settings_legal(b: LegalIn, _a: Admin = Depends(require_admin)):
    return {"legal": legal.save(b.model_dump()), "configured": legal.configured()}


class EmailGuardIn(BaseModel):
    enabled: Optional[bool] = None
    extra: Optional[list[str]] = None        # extra domains to block
    allow: Optional[list[str]] = None        # domains to let through even if listed


def _email_guard_state() -> dict:
    enabled, extra, allow = emailguard._settings()
    return {"enabled": enabled, "extra": sorted(extra), "allow": sorted(allow), "builtin_count": len(emailguard.BUILTIN)}


@app.put("/api/settings/email-guard")
def settings_email_guard(b: EmailGuardIn, _a: Admin = Depends(require_admin)):
    if b.enabled is not None:
        billing._pset(emailguard.SETTING_ENABLED, bool(b.enabled))
    if b.extra is not None:
        billing._pset(emailguard.SETTING_EXTRA, emailguard.clean_domains(b.extra))
    if b.allow is not None:
        billing._pset(emailguard.SETTING_ALLOW, emailguard.clean_domains(b.allow))
    return _email_guard_state()


class EmailCheckIn(BaseModel):
    email: str


@app.post("/api/settings/email-guard/check")
def settings_email_check(b: EmailCheckIn, _a: Admin = Depends(require_admin)):
    return emailguard.classify(b.email)


class TrialSettingsIn(BaseModel):
    days: Optional[int] = Field(None, ge=0, le=365)
    plan: Optional[str] = None


@app.put("/api/settings/trial")
def settings_trial(b: TrialSettingsIn, _a: Admin = Depends(require_admin)):
    return billing.save_trial_config(b.days, b.plan)


class FxIn(BaseModel):
    rates: dict          # {"GHS": 15.5}: 1 USD = 15.5 GHS


@app.put("/api/settings/fx")
def settings_fx(b: FxIn, _a: Admin = Depends(require_admin)):
    return {"fx_usd": billing.save_usd_rates(b.rates), "charge_currency": billing.charge_currency()}


class ChargeCurrencyIn(BaseModel):
    currency: str = ""          # '' or USD = charge the plan's own currency


@app.put("/api/settings/charge-currency")
def settings_charge_currency(b: ChargeCurrencyIn, _a: Admin = Depends(require_admin)):
    return {"charge_currency": billing.save_charge_currency(b.currency)}


@app.get("/api/settings")
def settings(_a: Admin = Depends(require_admin)):
    st = sociavault.schedule_status()
    in_use = sorted(({r["currency"] for r in db.query("SELECT DISTINCT currency FROM plans WHERE active")}
                     | set(billing.usd_rates()) | ({billing.charge_currency()} - {""})) - {"USD"})
    return {
        "fx": {"rates": billing.usd_rates(), "currencies": in_use, "charge_currency": billing.charge_currency(),
               "rate_currencies": sorted(billing.usd_rates())},
        "email_guard": _email_guard_state(),
        "legal": {**legal.get(), "configured": legal.configured()},
        "trial": {**billing.trial_config(), "plans": [p["code"] for p in billing.list_plans()]},
        "billing": {"enabled": billing.enabled(), "paystack": billing.paystack_status(),
                    "webhook_path": "/api/billing/webhook/paystack"},
        "late": __import__("app.latehub", fromlist=["x"]).key_status(),
        "ai": __import__("app.ai", fromlist=["x"]).platform_status(),
        "signup_protection": _signup_protection(),
        "backups": backup.status(),
        "vault": vaultwatch.state(),
        "sociavault": {"key": sociavault.platform_key_status(), "schedule": sociavault.schedule_config(),
                       "last_run": {k: st.get(k) for k in ("started_at", "finished_at", "ok", "running", "summary")}},
    }


@app.put("/api/settings/billing")
def settings_billing(b: BillingSettingsIn, _a: Admin = Depends(require_admin)):
    if b.clear_paystack_key or (b.paystack_key and b.paystack_key.strip()):
        billing.save_paystack_key(b.paystack_key or "", clear=b.clear_paystack_key)
    if b.enabled is not None:
        if b.enabled and not billing.paystack_status()["configured"] and \
                db.one("SELECT 1 FROM plans WHERE active AND price_minor > 0 LIMIT 1"):
            # allowed (limits still protect the platform), but say so: nobody can pay yet
            log.warning("billing turned on without a Paystack key: paid plans cannot be bought yet")
        billing.set_enabled(b.enabled)
    return {"enabled": billing.enabled(), "paystack": billing.paystack_status()}


@app.post("/api/settings/paystack/test")
def paystack_test(_a: Admin = Depends(require_admin)):
    data = billing._paystack("GET", "/balance")
    return {"ok": True, "message": "Paystack accepted the key", "balances": data.get("data")}


class SociaVaultIn(BaseModel):
    api_key: Optional[str] = None
    clear_key: bool = False
    enabled: Optional[bool] = None
    interval_hours: Optional[float] = Field(None, ge=0, le=168)
    days_back: Optional[int] = Field(None, ge=1, le=30)
    mode: Optional[str] = None


@app.put("/api/settings/sociavault")
def settings_sociavault(b: SociaVaultIn, _a: Admin = Depends(require_admin)):
    out = {}
    if b.clear_key or (b.api_key is not None and b.api_key.strip()):
        out["key"] = sociavault.save_platform_key(b.api_key or "", clear=b.clear_key)
    else:
        out["key"] = sociavault.platform_key_status()
    if any(x is not None for x in (b.enabled, b.interval_hours, b.days_back, b.mode)):
        out["schedule"] = sociavault.save_schedule_settings(
            enabled=b.enabled, interval_hours=b.interval_hours, days_back=b.days_back, mode=b.mode)
    else:
        out["schedule"] = sociavault.schedule_config()
    return out


@app.get("/api/backups")
def backups_status(_a: Admin = Depends(require_admin)):
    return backup.status()


@app.post("/api/actions/backup-now")
def action_backup_now(_a: Admin = Depends(require_admin)):
    try:
        return backup.backup_now()
    except Exception as e:
        raise HTTPException(500, f"Backup failed: {e}")


@app.post("/api/actions/restore-drill")
def action_restore_drill(_a: Admin = Depends(require_admin)):
    """Restore the newest backup into a scratch database, verify it table by table, drop it."""
    try:
        return backup.drill_now()
    except backup.BackupError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Restore test failed: {e}")


@app.get("/api/funnel")
def funnel(days: int = 30, _a: Admin = Depends(require_admin)):
    return activation.funnel(max(0, min(days, 3650)))


@app.post("/api/actions/send-digests")
def action_send_digests(_a: Admin = Depends(require_admin)):
    """Send every workspace its weekly digest right now (workspaces with nothing to report get nothing)."""
    return digest.run_due(force=True)


class VaultWatchIn(BaseModel):
    warn_pct: Optional[int] = Field(None, ge=1, le=90)
    pause_pct: Optional[int] = Field(None, ge=0, le=50)
    alert_email: Optional[str] = Field(None, max_length=254)
    reference: Optional[int] = Field(None, ge=0, le=100_000_000)


@app.put("/api/settings/vault-watch")
def settings_vault_watch(b: VaultWatchIn, _a: Admin = Depends(require_admin)):
    vaultwatch.save_config(b.warn_pct, b.pause_pct, b.alert_email, b.reference)
    return vaultwatch.state()


@app.post("/api/actions/check-balance")
def action_check_balance(_a: Admin = Depends(require_admin)):
    return vaultwatch.check(force=True)


def _signup_protection() -> dict:
    return {"require_verification": bool(auth._psetting(auth.SETTING_REQUIRE_VERIFY, True)),
            "enforced": auth.verification_required(), "email_configured": mailer.configured(),
            "ip_daily_limit": int(auth._psetting(auth.SETTING_SIGNUP_IP_DAILY, auth.DEFAULT_SIGNUP_IP_DAILY) or 0),
            "unverified_users": db.one("SELECT COUNT(*) AS n FROM users WHERE email_verified_at IS NULL")["n"]}


class SignupProtectionIn(BaseModel):
    require_verification: Optional[bool] = None
    ip_daily_limit: Optional[int] = Field(None, ge=0, le=1000)


@app.put("/api/settings/signup-protection")
def settings_signup_protection(b: SignupProtectionIn, _a: Admin = Depends(require_admin)):
    if b.require_verification is not None:
        billing._pset(auth.SETTING_REQUIRE_VERIFY, bool(b.require_verification))
    if b.ip_daily_limit is not None:
        billing._pset(auth.SETTING_SIGNUP_IP_DAILY, int(b.ip_daily_limit))
    return _signup_protection()


class AIPlatformIn(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    clear_key: bool = False


@app.put("/api/settings/ai")
def settings_ai(b: AIPlatformIn, _a: Admin = Depends(require_admin)):
    from app import ai
    try:
        return ai.save_platform(b.provider or "", b.model or "", b.base_url or "", b.api_key or "", b.clear_key)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/settings/ai/test")
def ai_test_platform(_a: Admin = Depends(require_admin)):
    from app import ai
    cfg = ai._platform_resolved()
    if cfg is None:
        raise HTTPException(400, "Save a key (or an endpoint and model) first")
    try:
        return {"ok": True, "model": cfg.model, "reply": ai.test_connection(cfg)[:40]}
    except Exception as e:
        raise HTTPException(400, f"The provider rejected the request: {str(e)[:200]}")


class LateIn(BaseModel):
    api_key: Optional[str] = None
    clear_key: bool = False


@app.put("/api/settings/late")
def settings_late(b: LateIn, _a: Admin = Depends(require_admin)):
    from app import latehub
    if b.clear_key or (b.api_key is not None and b.api_key.strip()):
        return {"key": latehub.save_key(b.api_key or "", clear=b.clear_key)}
    return {"key": latehub.key_status()}


@app.post("/api/settings/late/test")
def late_test(_a: Admin = Depends(require_admin)):
    from app import latehub
    if not latehub.available():
        raise HTTPException(400, "No Late key is set")
    try:
        latehub._call("GET", "/profiles", params={"limit": 1})
    except latehub.LateHubError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "message": "Late accepted the key"}


@app.post("/api/settings/sociavault/test")
def sociavault_test(_a: Admin = Depends(require_admin)):
    key = sociavault.resolve_server_key()
    if not key:
        raise HTTPException(400, "No system-wide SociaVault API key configured")
    try:
        return {"ok": True, "message": sociavault.test_connection(key)}
    except sociavault.SociaVaultError as e:
        raise HTTPException(e.status or 400, str(e))


@app.post("/api/actions/run-sync")
def run_sync(force: bool = False, _a: Admin = Depends(require_admin)):
    try:
        return {"ok": True, "summary": sociavault.run_scheduled_sync(force)}
    except Exception as e:
        raise HTTPException(500, str(e)[:300])


@app.post("/api/actions/run-renewals")
def run_renewals(_a: Admin = Depends(require_admin)):
    if not billing.enabled():
        raise HTTPException(400, "Billing is off")
    if not billing.paystack_secret():
        raise HTTPException(400, "No Paystack key is set")
    return {"ok": True, "result": billing.run_renewals()}


@app.post("/api/actions/process-schedules")
def run_schedules(_a: Admin = Depends(require_admin)):
    from app import publish
    return {"ok": True, "published": publish.process_due_schedules(50)}


# ---------- admins ----------

@app.get("/api/admins")
def admins(_a: Admin = Depends(require_admin)):
    return {"admins": db.query("SELECT id, email, name, disabled, created_at FROM platform_admins ORDER BY email")}


class AdminCreate(BaseModel):
    email: str
    name: str = Field("", max_length=100)
    password: str = Field(min_length=MIN_PASSWORD, max_length=200)


@app.post("/api/admins")
def admin_create(b: AdminCreate, _a: Admin = Depends(require_admin)):
    email = b.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Enter a valid email")
    if db.one("SELECT 1 FROM platform_admins WHERE email=%s", (email,)):
        raise HTTPException(409, "That admin already exists")
    return db.one("INSERT INTO platform_admins(email, name, password_hash) VALUES (%s,%s,%s) "
                  "RETURNING id, email, name, disabled, created_at",
                  (email, b.name.strip(), auth.hash_password(b.password)))


class AdminPatch(BaseModel):
    disabled: Optional[bool] = None
    password: Optional[str] = Field(None, min_length=MIN_PASSWORD, max_length=200)


@app.patch("/api/admins/{aid}")
def admin_patch(aid: int, b: AdminPatch, me_: Admin = Depends(require_admin)):
    row = db.one("SELECT id, disabled FROM platform_admins WHERE id=%s", (aid,))
    if not row:
        raise HTTPException(404, "Admin not found")
    if b.disabled:
        if aid == me_.id:
            raise HTTPException(400, "You cannot disable your own account")
        if db.one("SELECT COUNT(*) AS n FROM platform_admins WHERE NOT disabled AND id<>%s", (aid,))["n"] == 0:
            raise HTTPException(400, "That would leave no active admin")
    if b.disabled is not None:
        db.execute("UPDATE platform_admins SET disabled=%s WHERE id=%s", (b.disabled, aid))
        if b.disabled:
            db.execute("DELETE FROM admin_sessions WHERE admin_id=%s", (aid,))
    if b.password:
        db.execute("UPDATE platform_admins SET password_hash=%s WHERE id=%s", (auth.hash_password(b.password), aid))
        db.execute("DELETE FROM admin_sessions WHERE admin_id=%s AND admin_id<>%s", (aid, me_.id))
    return db.one("SELECT id, email, name, disabled, created_at FROM platform_admins WHERE id=%s", (aid,))


# ---------- UI ----------

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/", StaticFiles(directory=STATIC), name="static")
