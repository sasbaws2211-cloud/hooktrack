"""Subscriptions: plans, per-workspace limits, AI metering and Paystack checkout.

Plans and prices are managed in the standalone admin console. Enforcement is OFF until the operator turns on
`billing_enabled` there, so a fresh or self-hosted install is never limited by surprise.

There is no free tier. A workspace gets a TRIAL (7 days by default, with the limits of the trial plan) and then
must subscribe. When the trial or a paid period ends the workspace is EXPIRED: it can still be viewed, but every
write is refused with 402 until it pays, and scheduled syncs/publishing for it pause. The trial clock starts at
max(workspace created, the moment billing was switched on), so turning billing on never expires anyone instantly.

Money flow: checkout creates a pending `payments` row and a Paystack transaction and hands the page an
access_code, which opens Paystack's in-app popup (no redirect). The page then POLLS `GET /payments/{reference}`;
the server asks Paystack for the truth and, if paid, applies it. The signed webhook (charge.success) is a backup
for a customer who closed the tab. Everything goes through `apply_payment`, which is idempotent, so polling plus a
webhook never double-extends.
Auto-renew: if the customer ticks it at checkout and pays by card, Paystack's reusable authorization is stored
(encrypted) and `run_renewals` charges it a day before the period ends (3 tries, ~a day apart, never twice in
parallel). Without a saved card each payment buys one interval and a lapsed workspace falls back to free.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import auth, crypto, db
from .auth import Ctx, current

log = logging.getLogger("hooktrack.billing")
router = APIRouter(prefix="/api/billing", tags=["billing"])

PAYSTACK_API = os.environ.get("HOOKTRACK_PAYSTACK_API", "https://api.paystack.co").rstrip("/")   # override only to point tests at a fake
FREE = "free"
CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


# ---------- platform settings (shared with the admin console through the database) ----------

def _pget(key: str, default=None):
    row = db.one("SELECT value FROM platform_settings WHERE key=%s", (key,))
    return default if row is None else row.get("value", default)


def _pset(key: str, value) -> None:
    db.execute(
        """INSERT INTO platform_settings(key, value, updated_at) VALUES (%s, %s, now())
           ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()""",
        (key, db.Json(value)))


def enabled() -> bool:
    return bool(_pget("billing_enabled", False))


def set_enabled(on: bool) -> None:
    _pset("billing_enabled", bool(on))
    if on and not _pget("billing_enabled_at"):
        _pset("billing_enabled_at", datetime.now(timezone.utc).isoformat())


TRIAL_DAYS = 7
EXPIRED_PLAN = {"code": "expired", "name": "No active plan", "description": "", "price_minor": 0, "currency": "USD",
                "interval": "month", "max_accounts": 0, "max_members": 0, "max_ai_calls": 0, "max_connected": 0,
                "max_credits": 0, "max_reports": 0, "active": False,
                "position": 0}
TRIAL_ENDED_MSG = "Your free trial has ended. Choose a plan under Billing to keep working."
TRIAL_USED_MSG = ("This email has already used its free trial on another workspace. "
                  "Choose a plan under Billing to start using this one.")


def normalize_email(email: str) -> str:
    """The identity a trial belongs to: case-folded, `+tag` removed, and Gmail's dots ignored, so the usual
    alias tricks (a+1@gmail.com, a.b@gmail.com) are the same person. Different real mailboxes are not caught."""
    e = (email or "").strip().lower()
    local, _, domain = e.partition("@")
    local = local.split("+", 1)[0]
    if domain in ("gmail.com", "googlemail.com"):
        local, domain = local.replace(".", ""), "gmail.com"
    return f"{local}@{domain}"


def claim_trial(email: str, ws: int) -> bool:
    """Called once when a workspace is created. True if this person had not had a trial yet (it is now theirs);
    False records that the workspace starts without one."""
    row = db.one(
        """INSERT INTO trial_claims(email, workspace_id) VALUES (%s, %s)
           ON CONFLICT (email) DO NOTHING RETURNING email""", (normalize_email(email), ws))
    eligible = row is not None
    if not eligible:
        db.execute("UPDATE workspaces SET trial_eligible=FALSE WHERE id=%s", (ws,))
    return eligible


def trial_config() -> dict:
    try:
        days = int(_pget("trial_days", TRIAL_DAYS))
    except (TypeError, ValueError):
        days = TRIAL_DAYS
    return {"days": max(0, min(days, 365)), "plan": str(_pget("trial_plan", "") or "")}


def save_trial_config(days: Optional[int], plan: Optional[str]) -> dict:
    if days is not None:
        if not (0 <= int(days) <= 365):
            raise HTTPException(400, "Trial length must be between 0 and 365 days")
        _pset("trial_days", int(days))
    if plan is not None:
        if plan and (plan == FREE or not get_plan(plan)):
            raise HTTPException(400, "Choose one of your paid plans for the trial limits")
        _pset("trial_plan", plan)
    return trial_config()


def _trial_plan() -> dict:
    """The limits a trial gets: the configured plan, else the first (cheapest) plan on sale."""
    code = trial_config()["plan"]
    plan = get_plan(code) if code and code != FREE else None
    if plan is None:
        paid = [p for p in list_plans(only_active=True)]
        plan = paid[0] if paid else None
    return {**(plan or EXPIRED_PLAN), "name": "Free trial"} if plan else dict(EXPIRED_PLAN)


def paystack_secret() -> str:
    enc = _pget("paystack_secret_enc")
    if isinstance(enc, str) and enc:
        return crypto.decrypt(enc)
    return (os.environ.get("PAYSTACK_SECRET_KEY") or "").strip()


def paystack_status() -> dict:
    hint = _pget("paystack_key_hint") or ""
    has_db = bool(_pget("paystack_secret_enc"))
    has_env = bool((os.environ.get("PAYSTACK_SECRET_KEY") or "").strip())
    return {"configured": has_db or has_env, "source": "platform" if has_db else ("env" if has_env else "none"),
            "key_hint": hint if has_db else ("from env" if has_env else "")}


def save_paystack_key(key: str, clear: bool = False) -> dict:
    key = (key or "").strip()
    if clear or not key:
        db.execute("DELETE FROM platform_settings WHERE key IN ('paystack_secret_enc','paystack_key_hint')")
        return paystack_status()
    if not re.match(r"^sk_(test|live)_[A-Za-z0-9]{10,}$", key):
        raise HTTPException(400, "That is not a Paystack secret key (it starts with sk_test_ or sk_live_)")
    _pset("paystack_secret_enc", crypto.encrypt(key))
    _pset("paystack_key_hint", key[:8] + "…" + key[-4:])
    return paystack_status()


# ---------- display exchange rates ----------
# Prices are charged in the plan's own currency. For display, the operator may set "1 USD = N <currency>" so
# customers can see an approximate dollar amount. It is never used to charge anything.

def usd_rates() -> dict:
    raw = _pget("fx_usd_rates", {}) or {}
    out = {}
    for cur, v in raw.items():
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f > 0:
            out[str(cur).upper()] = f
    return out


def save_usd_rates(rates: dict) -> dict:
    clean = {}
    for cur, v in (rates or {}).items():
        c = str(cur).strip().upper()
        if not re.match(r"^[A-Z]{3}$", c) or c == "USD":
            raise HTTPException(400, f"'{cur}' is not a currency code")
        if v in (None, ""):
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise HTTPException(400, f"The rate for {c} must be a number")
        if not (0 < f < 1_000_000):
            raise HTTPException(400, f"The rate for {c} must be above zero")
        clean[c] = round(f, 6)
    _pset("fx_usd_rates", clean)
    return clean


def charge_currency() -> str:
    """Currency customers are actually charged in when plans are priced in USD ('' = the plan's own currency).
    Needed when the Paystack account cannot take USD (e.g. a GHS-only merchant)."""
    c = str(_pget("charge_currency", "") or "").strip().upper()
    return "" if c in ("", "USD") else c


def save_charge_currency(code: str) -> str:
    code = (code or "").strip().upper()
    if code in ("", "USD"):
        _pset("charge_currency", "")
        return ""
    if not re.match(r"^[A-Z]{3}$", code):
        raise HTTPException(400, f"'{code}' is not a currency code")
    if code not in usd_rates():
        raise HTTPException(400, f"Set the exchange rate for {code} first (1 USD = ? {code}).")
    _pset("charge_currency", code)
    return code


def charge_for(plan: dict) -> tuple[int, str]:
    """(amount_minor, currency) to actually charge for one interval of `plan`. A USD plan is converted at the
    current exchange rate when a charge currency is configured; anything else is charged as priced."""
    cur = charge_currency()
    if plan["currency"].upper() == "USD" and cur:
        rate = usd_rates().get(cur)
        if not rate:
            raise HTTPException(503, f"Payments are not ready: the USD to {cur} exchange rate is not set.")
        return int(round(plan["price_minor"] * rate)), cur
    return int(plan["price_minor"]), plan["currency"]


# ---------- plans ----------

def list_plans(only_active: bool = False) -> list[dict]:
    """Sellable plans. The old 'free' row is kept only as an internal marker and is never listed."""
    sql = "SELECT * FROM plans WHERE code <> 'free'" + (" AND active" if only_active else "") \
          + " ORDER BY position, price_minor, code"
    return db.query(sql)


def get_plan(code: str) -> Optional[dict]:
    return db.one("SELECT * FROM plans WHERE code=%s", (code,))


def _unlimited(plan: dict) -> dict:
    return {**plan, "max_accounts": -1, "max_members": -1, "max_ai_calls": -1, "max_connected": -1, "max_credits": -1,
                 "max_reports": -1}


def subscription(ws: int) -> dict:
    """The workspace's subscription row with `lapsed` set when a paid period has run out."""
    row = db.one("SELECT * FROM workspace_subscriptions WHERE workspace_id=%s", (ws,))
    if row is None:
        return {"plan_code": FREE, "status": "active", "current_period_end": None, "note": "", "lapsed": False,
                "has_row": False}
    end = row["current_period_end"]
    lapsed = row["status"] == "active" and row["plan_code"] != FREE and end is not None \
        and end < datetime.now(timezone.utc)
    return {**row, "lapsed": lapsed, "has_row": True}


def workspace_state(ws: int) -> dict:
    """paid | comp | trial | expired for a workspace, with the dates that explain it."""
    sub = subscription(ws)
    now = datetime.now(timezone.utc)
    has_paid_history = sub["has_row"] and sub["plan_code"] != FREE
    if has_paid_history and not sub["lapsed"]:
        return {"state": "comp" if sub["status"] == "comp" else "paid", "trial_ends_at": None, "sub": sub}
    if has_paid_history and sub["lapsed"]:
        return {"state": "expired", "trial_ends_at": None, "sub": sub, "reason": "paid_period_ended"}
    # never paid: the trial window
    override = sub.get("trial_ends_at") if sub["has_row"] else None
    if override is not None:
        ends = override
    else:
        w = db.one("SELECT created_at FROM workspaces WHERE id=%s", (ws,))
        started = w["created_at"] if w else now
        at = _pget("billing_enabled_at")
        if at:
            try:
                started = max(started, datetime.fromisoformat(at))
            except ValueError:
                pass
        ends = started + timedelta(days=trial_config()["days"])
    if override is None:
        w = db.one("SELECT trial_eligible FROM workspaces WHERE id=%s", (ws,))
        if w is not None and not w["trial_eligible"]:
            return {"state": "expired", "trial_ends_at": None, "sub": sub, "reason": "trial_used"}
    if ends > now:
        return {"state": "trial", "trial_ends_at": ends, "sub": sub}
    return {"state": "expired", "trial_ends_at": ends, "sub": sub, "reason": "trial_ended"}


def blocked_message(ws: int) -> str:
    st = workspace_state(ws)
    return TRIAL_USED_MSG if st.get("reason") == "trial_used" else TRIAL_ENDED_MSG


def access_blocked(ws: int) -> bool:
    """True when billing is on and this workspace has neither a trial nor a paid plan."""
    return enabled() and workspace_state(ws)["state"] == "expired"


def set_trial_end(ws: int, days: int) -> dict:
    """Operator: give a never-paid workspace `days` more trial from now (0 ends it today)."""
    st = workspace_state(ws)
    if st["state"] in ("paid", "comp") or (st["state"] == "expired" and st.get("reason") == "paid_period_ended"):
        raise HTTPException(400, "This workspace has a paid plan history; give it a plan instead of a trial")
    end = datetime.now(timezone.utc) + timedelta(days=days)
    db.execute(
        """INSERT INTO workspace_subscriptions(workspace_id, plan_code, status, current_period_end, trial_ends_at)
           VALUES (%s, 'free', 'active', NULL, %s)
           ON CONFLICT (workspace_id) DO UPDATE SET trial_ends_at=EXCLUDED.trial_ends_at, updated_at=now()""",
        (ws, end))
    return workspace_state(ws)


def effective_plan(ws: int) -> dict:
    """The plan whose limits apply today: the subscribed one, or free once a paid period has lapsed."""
    st = workspace_state(ws)
    if st["state"] in ("paid", "comp"):
        return get_plan(st["sub"]["plan_code"]) or dict(EXPIRED_PLAN)
    if st["state"] == "trial":
        return _trial_plan()
    return dict(EXPIRED_PLAN)


def _month() -> date:
    return datetime.now(timezone.utc).date().replace(day=1)


def usage(ws: int) -> dict:
    accounts = db.one("SELECT COUNT(*) AS n FROM accounts WHERE workspace_id=%s", (ws,))["n"]
    members = db.one("SELECT COUNT(*) AS n FROM memberships WHERE workspace_id=%s", (ws,))["n"]
    pending = db.one("SELECT COUNT(*) AS n FROM invites WHERE workspace_id=%s AND expires_at > now()", (ws,))["n"]
    ai = db.one("SELECT calls FROM ai_usage WHERE workspace_id=%s AND month=%s", (ws, _month()))
    return {"accounts": accounts, "members": members, "pending_invites": pending,
            "ai_calls": ai["calls"] if ai else 0, "credits": credits_used(ws)}


def credits_used(ws: int) -> int:
    r = db.one("SELECT COALESCE(SUM(credits), 0)::int AS n FROM credit_usage "
               "WHERE workspace_id=%s AND day >= date_trunc('month', CURRENT_DATE)::date", (ws,))
    return int(r["n"]) if r else 0


# What can be bought on top of a plan. `per` is how long one purchase lasts: connected / tracked accounts for 30 days,
# credits until the end of the calendar month they were bought in. Prices are USD minor units, overridable by the
# platform admin through platform_settings "addon_prices".
ADDONS = {
    "connected": {"label": "Connected publishing account", "price_minor": 800, "unit": 1, "lasts": "30 days",
                  "what": "Another TikTok, Instagram or YouTube account you can publish to."},
    "accounts": {"label": "25 more tracked accounts", "price_minor": 1000, "unit": 25, "lasts": "30 days",
                 "what": "Track 25 more accounts for views and decisions."},
    "credits": {"label": "1,000 data credits", "price_minor": 800, "unit": 1000, "lasts": "this month",
                "what": "More syncing and reading of videos and comments this month."},
}
ADDON_MAX_QTY = 20
RENEWABLE = ("connected", "accounts")        # credits are for one month and are bought again when needed
ADDON_RENEW_MAX_FAILURES = 3


def addon_price_minor(kind: str) -> int:
    over = _pget("addon_prices", {}) or {}
    try:
        return int(over.get(kind, ADDONS[kind]["price_minor"]))
    except (TypeError, ValueError):
        return ADDONS[kind]["price_minor"]


def _extras(ws: int) -> dict:
    r = db.one("SELECT extra_connected, extra_credits FROM workspace_subscriptions WHERE workspace_id=%s", (ws,))
    out = {"connected": int((r or {}).get("extra_connected") or 0), "credits": int((r or {}).get("extra_credits") or 0),
           "accounts": 0}
    for p in db.query("SELECT kind, COALESCE(SUM(units), 0)::int AS n FROM addon_purchases "
                      "WHERE workspace_id=%s AND starts_at <= now() AND expires_at > now() GROUP BY kind", (ws,)):
        out[p["kind"]] = out.get(p["kind"], 0) + int(p["n"])
    return out


def active_purchases(ws: int) -> list[dict]:
    return [{"id": r["id"], "kind": r["kind"], "units": r["units"], "expires_at": r["expires_at"],
             "auto_renew": bool(r["auto_renew"]) and r["renew_state"] == "", "renew_state": r["renew_state"],
             "renewable": r["kind"] in RENEWABLE}
            for r in db.query("SELECT id, kind, units, expires_at, auto_renew, renew_state FROM addon_purchases "
                              "WHERE workspace_id=%s AND starts_at <= now() AND expires_at > now() "
                              "ORDER BY expires_at, id", (ws,))]


def limit_for(ws: int, kind: str) -> int:
    """The plan's limit plus the workspace's own add-on, or -1 for unlimited. kind: connected | credits."""
    if not enabled():
        return -1
    plan = effective_plan(ws)
    base = plan.get("max_" + kind, -1)
    return -1 if base == -1 else int(base) + _extras(ws).get(kind, 0)


def set_extras(ws: int, extra_connected: int, extra_credits: int) -> dict:
    db.execute(
        """INSERT INTO workspace_subscriptions(workspace_id, plan_code, status, extra_connected, extra_credits)
           VALUES (%s, %s, 'active', %s, %s)
           ON CONFLICT (workspace_id) DO UPDATE SET extra_connected=EXCLUDED.extra_connected,
             extra_credits=EXCLUDED.extra_credits, updated_at=now()""",
        (ws, FREE, max(0, int(extra_connected)), max(0, int(extra_credits))))
    return _extras(ws)


def status(ws: int) -> dict:
    on = enabled()
    st = workspace_state(ws)
    plan = effective_plan(ws)
    sub = subscription(ws)
    now = datetime.now(timezone.utc)
    ends = st.get("trial_ends_at")
    return {
        "enabled": on,
        "state": st["state"] if on else "off",
        "trial": {"ends_at": ends, "days_left": max(0, -(-int((ends - now).total_seconds()) // 86400)) if ends else None,
                  "days": trial_config()["days"], "reason": st.get("reason", "")},
        "plan": plan,
        "limits": {"accounts": -1, "members": -1, "ai_calls": -1, "connected": -1, "credits": -1} if not on else
                  {"accounts": limit_for(ws, "accounts"), "members": plan["max_members"], "ai_calls": plan["max_ai_calls"],
                   "connected": limit_for(ws, "connected"), "credits": limit_for(ws, "credits")},
        "addons": [{"kind": k, "label": v["label"], "what": v["what"], "lasts": v["lasts"], "unit": v["unit"],
                    "price_minor": addon_price_minor(k)} for k, v in ADDONS.items()],
        "addon_purchases": active_purchases(ws),
        "usage": usage(ws),
        "subscription": {"plan_code": sub["plan_code"], "status": sub["status"], "lapsed": sub["lapsed"],
                         "current_period_end": sub["current_period_end"], "note": sub.get("note", "")},
        "payments_ready": paystack_status()["configured"],
        "card": card_info(sub),
    }


def card_info(sub: dict) -> dict:
    """What the customer may see about their saved card. Never the authorization code."""
    has = bool(sub.get("card_auth_enc"))
    end = sub.get("current_period_end")
    return {
        "has_card": has, "brand": sub.get("card_brand", ""), "last4": sub.get("card_last4", ""),
        "exp": sub.get("card_exp", ""), "auto_renew": bool(sub.get("auto_renew")) and has,
        "renew_error": sub.get("renew_error", ""), "failures": sub.get("renew_failures", 0),
        "next_charge_at": (end - timedelta(days=RENEW_AHEAD_DAYS)) if (has and sub.get("auto_renew") and end) else None,
    }


# ---------- enforcement ----------

def _blocked(what: str, plan: dict, limit: int) -> HTTPException:
    return HTTPException(402, f"Your {plan['name']} plan allows {limit} {what}. "
                              f"Upgrade your plan under Billing to add more.")


def require_active(ws: int) -> None:
    if access_blocked(ws):
        raise HTTPException(402, blocked_message(ws))


def require_capacity(ws: int, kind: str, replacing_invite_for: str = "") -> None:
    """Raise 402 when adding one more `accounts` or `members` would exceed the plan. No-op while billing is off.
    `replacing_invite_for`: a re-invite of that email replaces its pending invite, so that one is not counted."""
    if not enabled():
        return
    require_active(ws)
    plan = effective_plan(ws)
    u = usage(ws)
    if kind == "accounts":
        limit, used, label = limit_for(ws, "accounts"), u["accounts"], "accounts"
    elif kind == "members":
        pending = u["pending_invites"]
        if replacing_invite_for and db.one(
                "SELECT 1 FROM invites WHERE workspace_id=%s AND email=%s AND expires_at > now()",
                (ws, replacing_invite_for)):
            pending -= 1
        limit, used, label = plan["max_members"], u["members"] + pending, "team members"
    else:
        raise ValueError(kind)
    if limit != -1 and used >= limit:
        raise _blocked(label, plan, limit)


def check_ai(ws: int) -> None:
    if not enabled():
        return
    require_active(ws)
    plan = effective_plan(ws)
    limit = plan["max_ai_calls"]
    if limit != -1 and usage(ws)["ai_calls"] >= limit:
        raise HTTPException(402, f"Your {plan['name']} plan includes {limit} AI requests a month and this "
                                 f"workspace has used them. Upgrade under Billing, or wait for next month.")


def check_credits(ws: int, need: int = 1) -> None:
    """Raise 402 when spending `need` more data credits would pass the month's allowance. No-op while billing is off."""
    if not enabled():
        return
    require_active(ws)
    limit = limit_for(ws, "credits")
    if limit != -1 and credits_used(ws) + max(1, need) > limit:
        plan = effective_plan(ws)
        raise HTTPException(402, f"Your {plan['name']} plan includes {limit} data credits a month (syncing and reading "
                                 f"videos and comments) and this workspace has used them. Upgrade under Billing, "
                                 f"or wait for next month.")


TRIAL_PUBLISH_MSG = ("Publishing starts when you choose a plan. Your trial includes tracking, decisions, cards and "
                     "briefs; connecting accounts and publishing unlock with a paid plan.")


def publishing_held(ws: int) -> bool:
    """True while a workspace is only on its free trial: connecting accounts and publishing are held back, because
    each connected account costs real money. A paid or complimentary plan, or billing being off, is never held."""
    if not enabled():
        return False
    return workspace_state(ws)["state"] == "trial"


def require_connected(ws: int, connected_now: int) -> None:
    """Raise 402 when connecting one more publishing account would pass the plan's allowance."""
    if not enabled():
        return
    require_active(ws)
    if publishing_held(ws):
        raise HTTPException(402, TRIAL_PUBLISH_MSG)
    limit = limit_for(ws, "connected")
    if limit != -1 and connected_now >= limit:
        plan = effective_plan(ws)
        raise HTTPException(402, f"Your {plan['name']} plan includes {limit} connected publishing "
                                 f"account{'' if limit == 1 else 's'}. Upgrade under Billing to connect more.")


def record_ai(ws: int) -> None:
    db.execute(
        """INSERT INTO ai_usage(workspace_id, month, calls) VALUES (%s, %s, 1)
           ON CONFLICT (workspace_id, month) DO UPDATE SET calls = ai_usage.calls + 1""", (ws, _month()))


# ---------- subscription changes (shared by checkout and the admin console) ----------

def _add_interval(start: datetime, interval: str) -> datetime:
    return start + timedelta(days=365 if interval == "year" else 30)


def set_subscription(ws: int, plan_code: str, *, status: str = "active", period_end: Optional[datetime] = None,
                     note: str = "") -> dict:
    if not get_plan(plan_code):
        raise HTTPException(400, f"Unknown plan '{plan_code}'")
    db.execute(
        """INSERT INTO workspace_subscriptions(workspace_id, plan_code, status, current_period_end, note, updated_at)
           VALUES (%s,%s,%s,%s,%s, now())
           ON CONFLICT (workspace_id) DO UPDATE SET plan_code=EXCLUDED.plan_code, status=EXCLUDED.status,
             current_period_end=EXCLUDED.current_period_end, note=EXCLUDED.note, updated_at=now(),
             auto_renew=FALSE, trial_ends_at=NULL""",                       # a manual change by the operator stops automatic charging
        (ws, plan_code, status, period_end, note))
    return subscription(ws)


def apply_payment(reference: str, paid_amount_minor: Optional[int], paid_currency: Optional[str],
                  authorization: Optional[dict] = None) -> dict:
    """Mark a pending payment successful and extend the subscription. Safe to call any number of times.
    `authorization` is Paystack's authorization object: kept (encrypted) only when the customer agreed at checkout
    and it is a reusable card."""
    with db.conn() as c, c.cursor(cursor_factory=__import__("psycopg2.extras", fromlist=["x"]).RealDictCursor) as cur:
        cur.execute("SELECT * FROM payments WHERE reference=%s FOR UPDATE", (reference,))
        pay = cur.fetchone()
        if pay is None:
            raise HTTPException(404, "Unknown payment reference")
        if pay["status"] == "success":
            return {"applied": False, "already": True, "payment": pay}
        if paid_amount_minor is not None and int(paid_amount_minor) < int(pay["amount_minor"]):
            raise HTTPException(400, "The amount paid is less than the plan price")
        if paid_currency and paid_currency.upper() != pay["currency"].upper():
            raise HTTPException(400, "The payment currency does not match the plan")
        if str(pay["plan_code"]).startswith("addon:"):
            return _apply_addon(cur, pay)
        plan = get_plan(pay["plan_code"])
        if plan is None or pay["workspace_id"] is None:
            cur.execute("UPDATE payments SET status='success', paid_at=now() WHERE id=%s", (pay["id"],))
            return {"applied": False, "already": False, "payment": pay}
        cur.execute("SELECT * FROM workspace_subscriptions WHERE workspace_id=%s FOR UPDATE", (pay["workspace_id"],))
        cur_sub = cur.fetchone()
        now = datetime.now(timezone.utc)
        base = now
        if cur_sub and cur_sub["plan_code"] == plan["code"] and cur_sub["current_period_end"] \
                and cur_sub["current_period_end"] > now:
            base = cur_sub["current_period_end"]          # renewing early adds to what is already paid for
        new_end = _add_interval(base, plan["interval"])
        cur.execute(
            """INSERT INTO workspace_subscriptions(workspace_id, plan_code, status, current_period_end, note, updated_at)
               VALUES (%s,%s,'active',%s,'', now())
               ON CONFLICT (workspace_id) DO UPDATE SET plan_code=EXCLUDED.plan_code, status='active',
                 current_period_end=EXCLUDED.current_period_end, note='', updated_at=now()""",
            (pay["workspace_id"], plan["code"], new_end))
        cur.execute("UPDATE payments SET status='success', paid_at=now() WHERE id=%s", (pay["id"],))
        card = _reusable_card(authorization) if pay.get("save_card") else None
        if card:
            cur.execute(
                """UPDATE workspace_subscriptions SET auto_renew=TRUE, card_auth_enc=%s, card_brand=%s, card_last4=%s,
                     card_exp=%s, card_email=%s, renew_failures=0, renew_error='' WHERE workspace_id=%s""",
                (crypto.encrypt(card["code"]), card["brand"], card["last4"], card["exp"], pay["payer_email"],
                 pay["workspace_id"]))
        elif pay.get("kind") == "renewal":
            cur.execute("UPDATE workspace_subscriptions SET renew_failures=0, renew_error='' WHERE workspace_id=%s",
                        (pay["workspace_id"],))
    return {"applied": True, "already": False, "payment": pay, "period_end": new_end}


def _apply_addon(cur, pay: dict) -> dict:
    """A paid add-on: it counts from now until its expiry. Runs inside apply_payment's locked transaction."""
    try:
        _, kind, qty = str(pay["plan_code"]).split(":")
        qty = int(qty)
    except ValueError:
        cur.execute("UPDATE payments SET status='success', paid_at=now() WHERE id=%s", (pay["id"],))
        return {"applied": False, "already": False, "payment": pay}
    spec = ADDONS.get(kind)
    if spec is None or pay["workspace_id"] is None or qty < 1:
        cur.execute("UPDATE payments SET status='success', paid_at=now() WHERE id=%s", (pay["id"],))
        return {"applied": False, "already": False, "payment": pay}
    if kind == "credits":                  # until the end of the calendar month it was bought in
        cur.execute("SELECT (date_trunc('month', now()) + interval '1 month') AS e")
        expires = cur.fetchone()["e"]
    else:
        expires = datetime.now(timezone.utc) + timedelta(days=30)
    starts = datetime.now(timezone.utc)
    src_id = None
    ref = str(pay["reference"])
    if pay.get("kind") == "renewal" and ref.startswith("ht_ar_"):
        try:
            src_id = int(ref.split("_")[2])
        except (IndexError, ValueError):
            src_id = None
    if src_id is not None:                 # a renewal begins where the purchase it renews ends
        cur.execute("SELECT expires_at FROM addon_purchases WHERE id=%s AND workspace_id=%s FOR UPDATE",
                    (src_id, pay["workspace_id"]))
        src = cur.fetchone()
        if src:
            starts = max(starts, src["expires_at"])
            expires = starts + timedelta(days=30)
            cur.execute("UPDATE addon_purchases SET renew_state='renewed' WHERE id=%s", (src_id,))
    cur.execute(
        """SELECT (auto_renew AND card_auth_enc <> '') AS ok FROM workspace_subscriptions WHERE workspace_id=%s""",
        (pay["workspace_id"],))
    r = cur.fetchone()
    renew = kind in RENEWABLE and (src_id is not None or bool(r and r["ok"]))
    cur.execute(
        """INSERT INTO addon_purchases (workspace_id, kind, units, reference, starts_at, expires_at, auto_renew)
           VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (reference) DO NOTHING""",
        (pay["workspace_id"], kind, qty * spec["unit"], pay["reference"], starts, expires, renew))
    cur.execute("UPDATE payments SET status='success', paid_at=now() WHERE id=%s", (pay["id"],))
    return {"applied": True, "already": False, "payment": pay, "addon": kind, "expires_at": expires,
            "auto_renew": renew}


def _reusable_card(auth_obj: Optional[dict]) -> Optional[dict]:
    a = auth_obj or {}
    if not a.get("reusable") or not a.get("authorization_code") or (a.get("channel") or "card") != "card":
        return None
    exp = f"{str(a.get('exp_month') or '').zfill(2)}/{str(a.get('exp_year') or '')[-2:]}" if a.get("exp_month") else ""
    return {"code": a["authorization_code"], "brand": (a.get("brand") or a.get("card_type") or "card").strip(),
            "last4": str(a.get("last4") or ""), "exp": exp}


# ---------- Paystack ----------

def _paystack(method: str, path: str, **kw) -> dict:
    key = paystack_secret()
    if not key:
        raise HTTPException(503, "Payments are not set up yet. Ask the platform operator to add a Paystack key.")
    try:
        r = httpx.request(method, PAYSTACK_API + path, headers={"Authorization": f"Bearer {key}"},
                          timeout=30, follow_redirects=False, **kw)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Could not reach Paystack: {str(e)[:120]}")
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400 or not data.get("status"):
        raise HTTPException(502, f"Paystack said: {str(data.get('message') or r.status_code)[:200]}")
    return data


def verify_signature(raw: bytes, signature: str, secret: str) -> bool:
    if not secret or not signature:
        return False
    want = hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest()
    return hmac.compare_digest(want, signature.strip().lower())


# ---------- auto-renew ----------

RENEW_AHEAD_DAYS = 1          # charge this long before the paid period ends, so there is no gap
RENEW_RETRY_HOURS = 20
RENEW_MAX_FAILURES = 3
RENEW_GIVE_UP_DAYS = 7        # never charge a card for a plan that lapsed longer ago than this


def set_auto_renew(ws: int, on: bool) -> dict:
    sub = subscription(ws)
    if on and not sub.get("card_auth_enc"):
        raise HTTPException(400, "There is no saved card. Pay once by card and tick 'renew automatically'.")
    db.execute("UPDATE workspace_subscriptions SET auto_renew=%s, renew_failures=0, renew_error='', updated_at=now() "
               "WHERE workspace_id=%s", (on, ws))
    return card_info(subscription(ws))


def remove_card(ws: int) -> None:
    db.execute("UPDATE workspace_subscriptions SET auto_renew=FALSE, card_auth_enc='', card_brand='', card_last4='', "
               "card_exp='', card_email='', renew_failures=0, renew_error='', updated_at=now() WHERE workspace_id=%s", (ws,))


def _notify(email: str, subject: str, text: str) -> None:
    try:
        from . import mailer
        if email:
            mailer.send(email, subject, text)
    except Exception:                                   # a mail problem must never undo a billing outcome
        log.exception("billing email failed")


def _fail_renewal(ws: int, reason: str, email: str, plan_name: str) -> None:
    row = db.one(
        """UPDATE workspace_subscriptions SET renew_failures = renew_failures + 1, renew_error=%s, updated_at=now()
           WHERE workspace_id=%s RETURNING renew_failures""", (reason[:200], ws))
    n = row["renew_failures"] if row else 0
    if n >= RENEW_MAX_FAILURES:
        db.execute("UPDATE workspace_subscriptions SET auto_renew=FALSE WHERE workspace_id=%s", (ws,))
        _notify(email, f"Your {plan_name} plan could not be renewed",
                f"We tried to renew your {plan_name} plan {n} times and the card was declined ({reason}).\n"
                f"Automatic renewal is now off. Open Billing and pay again to keep your plan.")
    else:
        _notify(email, f"Payment problem renewing your {plan_name} plan",
                f"We could not charge your saved card to renew {plan_name} ({reason}). We will try again shortly. "
                f"You can also pay from Billing at any time.")


def _renew_one(sub: dict, plan: dict) -> str:
    """One renewal attempt for one workspace. Returns: renewed | pending | failed."""
    ws = sub["workspace_id"]
    # a charge we started earlier and never saw the end of is settled first: never charge twice for one period
    stale = db.one("SELECT reference FROM payments WHERE workspace_id=%s AND kind='renewal' AND "
                   "status='pending' AND created_at > now() - interval '3 days' ORDER BY created_at DESC LIMIT 1", (ws,))
    if stale:
        settled = check_payment(stale["reference"])
        if settled["status"] == "success":
            return "renewed"
        if settled["status"] == "pending":
            return "pending"
    code = crypto.decrypt(sub["card_auth_enc"])
    if not code:                                         # key changed or data damaged: ask the customer to re-pay
        remove_card(ws)
        return "failed"
    try:
        amount, currency = charge_for(plan)               # USD plan -> cedis at today's rate, if configured
    except HTTPException as e:
        _fail_renewal(ws, str(e.detail), sub["card_email"], plan["name"])
        return "failed"
    ref = "ht_r_" + secrets.token_urlsafe(14).replace("-", "a").replace("_", "b")
    wsrow = db.one("SELECT name FROM workspaces WHERE id=%s", (ws,))
    db.execute(
        "INSERT INTO payments(workspace_id, workspace_name, plan_code, amount_minor, currency, reference, "
        "payer_email, kind) VALUES (%s,%s,%s,%s,%s,%s,%s,'renewal')",
        (ws, wsrow["name"] if wsrow else "", plan["code"], amount, currency, ref, sub["card_email"]))
    try:
        data = _paystack("POST", "/transaction/charge_authorization", json={
            "email": sub["card_email"], "amount": amount, "currency": currency,
            "authorization_code": code, "reference": ref,
            "metadata": {"workspace_id": ws, "plan_code": plan["code"], "renewal": True}})
    except HTTPException as e:
        # Paystack answered "no" (declined / invalid card): a real failure. A network error is ambiguous: the
        # charge may have gone through, so leave it pending and let verification settle it.
        if e.status_code == 502 and str(e.detail).startswith("Could not reach Paystack"):
            return "pending"
        db.execute("UPDATE payments SET status='failed' WHERE reference=%s AND status='pending'", (ref,))
        _fail_renewal(ws, str(e.detail).replace("Paystack said: ", ""), sub["card_email"], plan["name"])
        return "failed"
    d = data.get("data") or {}
    outcome = d.get("status")
    if outcome == "success":
        try:
            apply_payment(ref, d.get("amount"), d.get("currency"))
        except HTTPException as e:
            db.execute("UPDATE payments SET status='failed' WHERE reference=%s AND status='pending'", (ref,))
            _fail_renewal(ws, str(e.detail), sub["card_email"], plan["name"])
            return "failed"
        new = subscription(ws)
        _notify(sub["card_email"], f"Your {plan['name']} plan was renewed",
                f"We charged your saved card and renewed {plan['name']} until "
                f"{new['current_period_end']:%d %b %Y}. Thank you.")
        return "renewed"
    if outcome in ("failed", "abandoned", "reversed"):
        db.execute("UPDATE payments SET status='failed' WHERE reference=%s AND status='pending'", (ref,))
        _fail_renewal(ws, str(d.get("gateway_response") or d.get("message") or "card declined"),
                      sub["card_email"], plan["name"])
        return "failed"
    return "pending"            # needs a PIN/OTP or is still processing: a saved card cannot be charged silently


def run_renewals(limit: int = 50) -> dict:
    """Charge every saved card whose paid period ends within a day. Safe to run from several workers at once."""
    from . import leases
    out = {"renewed": 0, "pending": 0, "failed": 0, "skipped": 0}
    if not enabled() or not paystack_secret():
        return out
    with leases.lease("billing-renewals", ttl=600) as got:
        if not got:
            return out
        due = db.query(
            """SELECT * FROM workspace_subscriptions
               WHERE auto_renew AND card_auth_enc <> '' AND status='active' AND plan_code <> 'free'
                 AND current_period_end IS NOT NULL
                 AND current_period_end <= now() + make_interval(days => %s)
                 AND current_period_end > now() - make_interval(days => %s)
                 AND renew_failures < %s
                 AND (last_renew_attempt IS NULL OR last_renew_attempt < now() - make_interval(hours => %s))
               ORDER BY current_period_end LIMIT %s""",
            (RENEW_AHEAD_DAYS, RENEW_GIVE_UP_DAYS, RENEW_MAX_FAILURES, RENEW_RETRY_HOURS, limit))
        for sub in due:
            # claim this attempt in the database too, so a second worker (or a crash + restart) cannot repeat it
            claimed = db.execute(
                """UPDATE workspace_subscriptions SET last_renew_attempt=now()
                   WHERE workspace_id=%s AND (last_renew_attempt IS NULL
                         OR last_renew_attempt < now() - make_interval(hours => %s))""",
                (sub["workspace_id"], RENEW_RETRY_HOURS))
            plan = get_plan(sub["plan_code"])
            if not claimed or plan is None or plan["price_minor"] <= 0:
                out["skipped"] += 1
                continue
            try:
                out[_renew_one(sub, plan)] += 1
            except Exception:
                log.exception("renewal for workspace %s crashed", sub["workspace_id"])
                out["skipped"] += 1
    if any(out.values()):
        log.info("renewals: %s", out)
    return out


def _fail_addon_renewal(p: dict, reason: str, email: str) -> None:
    row = db.one("UPDATE addon_purchases SET failures = failures + 1, last_attempt = now() WHERE id=%s RETURNING failures",
                 (p["id"],))
    n = row["failures"] if row else 0
    label = ADDONS.get(p["kind"], {}).get("label", "add-on")
    if n >= ADDON_RENEW_MAX_FAILURES:
        db.execute("UPDATE addon_purchases SET auto_renew=FALSE, renew_state='failed' WHERE id=%s", (p["id"],))
        _notify(email, f"Your {label} add-on could not be renewed",
                f"We tried {n} times to renew your {label} add-on and the card was declined ({reason}). It will end on "
                f"{p['expires_at']:%d %b %Y}. Open Billing and buy it again to keep it.")
    else:
        _notify(email, f"Payment problem renewing your {label} add-on",
                f"We could not charge your saved card to renew {label} ({reason}). We will try again shortly.")


def _renew_addon(p: dict, sub: dict) -> str:
    """One renewal attempt for one add-on purchase. Returns: renewed | pending | failed."""
    ws = p["workspace_id"]
    spec = ADDONS[p["kind"]]
    qty = max(1, p["units"] // spec["unit"])
    code = crypto.decrypt(sub["card_auth_enc"])
    if not code:
        return "failed"
    amount, currency = charge_for({"price_minor": addon_price_minor(p["kind"]) * qty, "currency": "USD"})
    ref = f"ht_ar_{p['id']}_" + secrets.token_urlsafe(10).replace("-", "a").replace("_", "b")
    wsrow = db.one("SELECT name FROM workspaces WHERE id=%s", (ws,))
    db.execute(
        "INSERT INTO payments(workspace_id, workspace_name, plan_code, amount_minor, currency, reference, "
        "payer_email, kind) VALUES (%s,%s,%s,%s,%s,%s,%s,'renewal')",
        (ws, wsrow["name"] if wsrow else "", f"addon:{p['kind']}:{qty}", amount, currency, ref, sub["card_email"]))
    try:
        data = _paystack("POST", "/transaction/charge_authorization", json={
            "email": sub["card_email"], "amount": amount, "currency": currency, "authorization_code": code,
            "reference": ref, "metadata": {"workspace_id": ws, "addon": p["kind"], "qty": qty, "renewal": True}})
    except HTTPException as e:
        if e.status_code == 502 and str(e.detail).startswith("Could not reach Paystack"):
            return "pending"                     # ambiguous: settled by verification, never charged twice
        db.execute("UPDATE payments SET status='failed' WHERE reference=%s AND status='pending'", (ref,))
        _fail_addon_renewal(p, str(e.detail).replace("Paystack said: ", ""), sub["card_email"])
        return "failed"
    d = data.get("data") or {}
    outcome = d.get("status")
    if outcome == "success":
        try:
            apply_payment(ref, d.get("amount"), d.get("currency"))
        except HTTPException as e:
            db.execute("UPDATE payments SET status='failed' WHERE reference=%s AND status='pending'", (ref,))
            _fail_addon_renewal(p, str(e.detail), sub["card_email"])
            return "failed"
        _notify(sub["card_email"], f"Your {spec['label']} add-on was renewed",
                f"We charged your saved card and renewed your {spec['label']} add-on for another 30 days. Thank you.")
        return "renewed"
    if outcome in ("failed", "abandoned", "reversed"):
        db.execute("UPDATE payments SET status='failed' WHERE reference=%s AND status='pending'", (ref,))
        _fail_addon_renewal(p, str(d.get("gateway_response") or d.get("message") or "card declined"), sub["card_email"])
        return "failed"
    return "pending"


def run_addon_renewals(limit: int = 100) -> dict:
    """Renew add-ons that end within a day, with the card saved for the plan. Safe from several workers."""
    from . import leases
    out = {"renewed": 0, "pending": 0, "failed": 0, "skipped": 0}
    if not enabled() or not paystack_secret():
        return out
    with leases.lease("billing-addon-renewals", ttl=600) as got:
        if not got:
            return out
        due = db.query(
            """SELECT p.* FROM addon_purchases p
               WHERE p.auto_renew AND p.renew_state = '' AND p.kind = ANY(%s)
                 AND p.expires_at <= now() + make_interval(days => %s)
                 AND p.expires_at > now() - make_interval(days => %s)
                 AND p.failures < %s
                 AND (p.last_attempt IS NULL OR p.last_attempt < now() - make_interval(hours => %s))
               ORDER BY p.expires_at LIMIT %s""",
            (list(RENEWABLE), RENEW_AHEAD_DAYS, RENEW_GIVE_UP_DAYS, ADDON_RENEW_MAX_FAILURES, RENEW_RETRY_HOURS, limit))
        for p in due:
            claimed = db.execute(
                """UPDATE addon_purchases SET last_attempt = now() WHERE id=%s AND renew_state=''
                   AND (last_attempt IS NULL OR last_attempt < now() - make_interval(hours => %s))""",
                (p["id"], RENEW_RETRY_HOURS))
            sub = db.one("SELECT * FROM workspace_subscriptions WHERE workspace_id=%s", (p["workspace_id"],))
            if (not claimed or not sub or not sub.get("auto_renew") or not sub.get("card_auth_enc")
                    or access_blocked(p["workspace_id"])):
                out["skipped"] += 1
                continue
            try:
                out[_renew_addon(p, sub)] += 1
            except Exception:
                log.exception("add-on renewal %s crashed", p["id"])
                out["skipped"] += 1
    if any(out.values()):
        log.info("add-on renewals: %s", out)
    return out


# ---------- routes ----------

@router.get("")
def billing_status(ctx: Ctx = Depends(current)):
    out = status(ctx.workspace_id)
    out["plans"] = list_plans(only_active=True)
    for p in out["plans"]:
        try:
            p["charge_minor"], p["charge_currency"] = charge_for(p)
        except HTTPException:
            p["charge_minor"], p["charge_currency"] = p["price_minor"], p["currency"]
    out["paywalled"] = out["state"] == "expired"
    out["can_manage"] = ctx.role in ("owner", "admin")
    out["fx_usd"] = usd_rates()          # {"GHS": 15.5} means 1 USD = 15.5 GHS (display only)
    pays = db.query("SELECT plan_code, amount_minor, currency, status, created_at, paid_at, reference "
                    "FROM payments WHERE workspace_id=%s ORDER BY created_at DESC LIMIT 20", (ctx.workspace_id,))
    out["payments"] = pays
    pend = db.one("SELECT reference, plan_code FROM payments WHERE workspace_id=%s AND status='pending' "
                  "AND created_at > now() - interval '" + PENDING_WINDOW + "' ORDER BY created_at DESC LIMIT 1",
                  (ctx.workspace_id,))
    out["pending"] = pend
    return out


class CheckoutIn(BaseModel):
    plan_code: str
    save_card: bool = False        # explicit consent to renew automatically with the card used


def public_pricing() -> dict:
    """What the public landing page may show: plans, prices and the trial length. Nothing about any workspace."""
    on = enabled()
    plans = []
    if on:
        for p in list_plans(only_active=True):
            try:
                charge_minor, charge_cur = charge_for(p)
            except HTTPException:
                charge_minor, charge_cur = p["price_minor"], p["currency"]
            plans.append({
                "code": p["code"], "name": p["name"], "description": p["description"],
                "price_minor": p["price_minor"], "currency": p["currency"], "interval": p["interval"],
                "max_accounts": p["max_accounts"], "max_members": p["max_members"], "max_ai_calls": p["max_ai_calls"],
                "max_connected": p.get("max_connected", -1), "max_credits": p.get("max_credits", -1),
                "max_reports": p.get("max_reports", -1),
                "charge_minor": charge_minor, "charge_currency": charge_cur})
    return {"billing_enabled": on, "trial_days": trial_config()["days"] if on else 0, "plans": plans}


class AutoRenewIn(BaseModel):
    enabled: bool


@router.patch("/auto-renew")
def auto_renew(b: AutoRenewIn, ctx: Ctx = Depends(auth.admin)):
    return set_auto_renew(ctx.workspace_id, b.enabled)


@router.delete("/card")
def delete_card(ctx: Ctx = Depends(auth.admin)):
    remove_card(ctx.workspace_id)
    return {"ok": True}


PENDING_WINDOW = "1 hour"


@router.post("/checkout")
def checkout(b: CheckoutIn, ctx: Ctx = Depends(auth.admin)):
    """Start a payment. Returns the Paystack access_code for the in-app popup and our own reference to poll."""
    if not enabled():
        raise HTTPException(400, "Billing is not turned on for this platform")
    plan = get_plan(b.plan_code)
    if plan is None or not plan["active"]:
        raise HTTPException(404, "That plan is not available")
    if plan["price_minor"] <= 0:
        raise HTTPException(400, "That plan is free: there is nothing to pay")
    amount, currency = charge_for(plan)
    ws = db.one("SELECT name FROM workspaces WHERE id=%s", (ctx.workspace_id,))
    ref = "ht_" + secrets.token_urlsafe(16).replace("-", "a").replace("_", "b")
    db.execute(
        "INSERT INTO payments(workspace_id, workspace_name, plan_code, amount_minor, currency, reference, payer_email, save_card) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (ctx.workspace_id, ws["name"] if ws else "", plan["code"], amount, currency, ref,
         ctx.email, b.save_card))
    try:
        data = _paystack("POST", "/transaction/initialize", json={
            "email": ctx.email, "amount": amount, "currency": currency, "reference": ref,
            "metadata": {"workspace_id": ctx.workspace_id, "plan_code": plan["code"]}})
    except HTTPException:
        db.execute("DELETE FROM payments WHERE reference=%s AND status='pending'", (ref,))   # never reached Paystack
        raise
    d = data.get("data") or {}
    if not d.get("access_code"):
        db.execute("DELETE FROM payments WHERE reference=%s AND status='pending'", (ref,))   # never reached Paystack
        raise HTTPException(502, "Paystack did not return a checkout session")
    return {"reference": ref, "access_code": d["access_code"], "url": d.get("authorization_url"),
            "amount_minor": amount, "currency": currency, "plan": plan["name"]}


class AddonRenewIn(BaseModel):
    on: bool


@router.patch("/addons/{purchase_id}/auto-renew")
def addon_auto_renew(purchase_id: int, b: AddonRenewIn, ctx: Ctx = Depends(auth.admin)):
    p = db.one("SELECT * FROM addon_purchases WHERE id=%s AND workspace_id=%s", (purchase_id, ctx.workspace_id))
    if not p:
        raise HTTPException(404, "Add-on not found")
    if p["kind"] not in RENEWABLE:
        raise HTTPException(400, "This add-on is for one month and is bought again when needed")
    if b.on:
        sub = db.one("SELECT auto_renew, card_auth_enc FROM workspace_subscriptions WHERE workspace_id=%s",
                     (ctx.workspace_id,))
        if not sub or not sub["auto_renew"] or not sub["card_auth_enc"]:
            raise HTTPException(400, "Pay for your plan by card and keep \"renew automatically\" on first: add-ons "
                                     "renew with the same saved card.")
        if p["renew_state"] in ("renewed",):
            raise HTTPException(400, "This add-on has already been renewed")
        db.execute("UPDATE addon_purchases SET auto_renew=TRUE, renew_state='', failures=0 WHERE id=%s", (purchase_id,))
    else:
        db.execute("UPDATE addon_purchases SET auto_renew=FALSE, renew_state=CASE WHEN renew_state='renewed' THEN "
                   "renew_state ELSE 'cancelled' END WHERE id=%s", (purchase_id,))
    return {"addon_purchases": active_purchases(ctx.workspace_id)}


class AddonCheckoutIn(BaseModel):
    kind: str
    qty: int = Field(1, ge=1, le=ADDON_MAX_QTY)


@router.post("/addons/checkout")
def addon_checkout(b: AddonCheckoutIn, ctx: Ctx = Depends(auth.admin)):
    """Start a payment for an add-on. Same Paystack popup and polling as a plan."""
    if not enabled():
        raise HTTPException(400, "Billing is not turned on for this platform")
    spec = ADDONS.get(b.kind)
    if spec is None:
        raise HTTPException(404, "Unknown add-on")
    require_active(ctx.workspace_id)
    total = addon_price_minor(b.kind) * b.qty
    amount, currency = charge_for({"price_minor": total, "currency": "USD"})
    ws = db.one("SELECT name FROM workspaces WHERE id=%s", (ctx.workspace_id,))
    ref = "ht_" + secrets.token_urlsafe(16).replace("-", "a").replace("_", "b")
    db.execute(
        "INSERT INTO payments(workspace_id, workspace_name, plan_code, amount_minor, currency, reference, payer_email, save_card) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,FALSE)",
        (ctx.workspace_id, ws["name"] if ws else "", f"addon:{b.kind}:{b.qty}", amount, currency, ref, ctx.email))
    try:
        data = _paystack("POST", "/transaction/initialize", json={
            "email": ctx.email, "amount": amount, "currency": currency, "reference": ref,
            "metadata": {"workspace_id": ctx.workspace_id, "addon": b.kind, "qty": b.qty}})
    except HTTPException:
        db.execute("DELETE FROM payments WHERE reference=%s AND status='pending'", (ref,))
        raise
    d = data.get("data") or {}
    if not d.get("access_code"):
        db.execute("DELETE FROM payments WHERE reference=%s AND status='pending'", (ref,))
        raise HTTPException(502, "Paystack did not return a checkout session")
    return {"reference": ref, "access_code": d["access_code"], "url": d.get("authorization_url"),
            "amount_minor": amount, "currency": currency, "plan": f"{b.qty} × {spec['label']}"}


def check_payment(reference: str) -> dict:
    """Ask Paystack what happened to a pending payment and record it. Safe to call repeatedly."""
    pay = db.one("SELECT * FROM payments WHERE reference=%s", (reference,))
    if pay is None:
        raise HTTPException(404, "Unknown payment")
    if pay["status"] != "pending":
        return pay
    try:
        d = _paystack("GET", f"/transaction/verify/{reference}").get("data") or {}
    except HTTPException as e:
        log.warning("verify %s: %s", reference, e.detail)
        return pay                                   # Paystack hiccup: still pending, the next poll tries again
    outcome = d.get("status")
    if outcome == "success":
        try:
            apply_payment(reference, d.get("amount"), d.get("currency"), d.get("authorization"))
        except HTTPException as e:                   # paid, but not the amount/currency we asked for: do not grant
            log.warning("payment %s rejected: %s", reference, e.detail)
            db.execute("UPDATE payments SET status='failed' WHERE reference=%s AND status='pending'", (reference,))
    elif outcome in ("failed", "reversed"):
        db.execute("UPDATE payments SET status='failed' WHERE reference=%s AND status='pending'", (reference,))
    return db.one("SELECT * FROM payments WHERE reference=%s", (reference,))


@router.get("/payments/{reference}")
def payment_status(reference: str, ctx: Ctx = Depends(current)):
    """Polled by the page while the Paystack popup is open. Scoped to the caller's workspace."""
    pay = db.one("SELECT workspace_id FROM payments WHERE reference=%s", (reference,))
    if pay is None or pay["workspace_id"] != ctx.workspace_id:
        raise HTTPException(404, "Unknown payment")
    p = check_payment(reference)
    out = {"reference": reference, "status": p["status"], "plan_code": p["plan_code"]}
    if p["status"] == "success":
        out["billing"] = status(ctx.workspace_id)
    return out


@router.post("/webhook/paystack")
async def paystack_webhook(request: Request):
    raw = await request.body()
    if not verify_signature(raw, request.headers.get("x-paystack-signature", ""), paystack_secret()):
        raise HTTPException(401, "Bad signature")
    try:
        event = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "Bad body")
    if event.get("event") == "charge.success":
        d = event.get("data") or {}
        ref = d.get("reference") or ""
        if ref and db.one("SELECT 1 FROM payments WHERE reference=%s", (ref,)):
            try:
                apply_payment(ref, d.get("amount"), d.get("currency"), d.get("authorization"))
            except HTTPException as e:       # a mismatching amount must not make Paystack retry forever
                log.warning("webhook payment %s rejected: %s", ref, e.detail)
    return {"ok": True}
