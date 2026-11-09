"""Lifetime deals: licenses sold on AppSumo that unlock a lifetime tier for one workspace.

AppSumo tells us what happened by webhook (purchase, activate, upgrade, downgrade, deactivate) and we only ever react to
those: a licence is never created, upgraded or refunded by hand (AppSumo forbids it). A customer ties a licence to their
workspace by activating it (the OAuth redirect) or by pasting the key in Billing.

Rules:
  * one active licence per workspace; a higher tier replaces a lower one
  * a refund (deactivate) ends that workspace's lifetime access at once, unless it has already moved to a newer licence
  * limits live on the ltd_1..ltd_3 plans and are monthly caps, never unlimited
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException

from . import cache, db

log = logging.getLogger("hooktrack.lifetime")
MODES = ("subscriptions", "lifetime")
MAX_TIER = 2                 # 1 code = tier 1, 2 stacked codes = tier 2


def mode() -> str:
    from . import billing
    return billing.billing_mode()


def set_mode(value: str) -> str:
    from . import billing
    if value not in MODES:
        raise HTTPException(400, "Billing mode must be 'subscriptions' or 'lifetime'")
    billing._pset("billing_mode", value)
    return value


def is_lifetime_mode() -> bool:
    return mode() == "lifetime"


DEFAULT_CAP = 100
ALERT_AT = 90


def cap() -> int:
    from . import billing
    try:
        return max(0, int(billing._pget("lifetime_cap", DEFAULT_CAP)))
    except (TypeError, ValueError):
        return DEFAULT_CAP


def set_cap(n: int) -> int:
    from . import billing
    billing._pset("lifetime_cap", max(0, min(100000, int(n))))
    return cap()


def sold() -> int:
    """Licences in use: bought and not refunded, and not replaced by an upgrade (an upgrade is one customer, not two)."""
    return int(db.one("SELECT COUNT(*) AS n FROM lifetime_licenses WHERE status IN ('inactive','active')")["n"])


def spots() -> dict:
    c, n = cap(), sold()
    return {"cap": c, "sold": n, "left": max(0, c - n)}


def plans() -> list[dict]:
    return db.query("SELECT * FROM plans WHERE interval='lifetime' AND code IN ('ltd_1','ltd_2') ORDER BY position, code")


def plan_code_for_tier(tier: int) -> str:
    return f"ltd_{max(1, min(MAX_TIER, int(tier or 1)))}"


def _tier(payload: dict) -> int:
    try:
        return max(1, min(MAX_TIER, int(payload.get("tier") or 1)))
    except (TypeError, ValueError):
        return 1


def get(license_key: str) -> Optional[dict]:
    return db.one("SELECT * FROM lifetime_licenses WHERE license_key=%s", (license_key,))


def for_workspace(ws: int) -> Optional[dict]:
    """The licence this workspace currently holds (the highest tier if it somehow has several active ones)."""
    return db.one("SELECT * FROM lifetime_licenses WHERE workspace_id=%s AND status IN ('inactive','active') "
                  "ORDER BY tier DESC, claimed_at DESC LIMIT 1", (ws,))


def _grant(ws: int, plan_code: str) -> None:
    """Make the workspace's subscription this lifetime plan: no end date, no card, no renewals."""
    db.execute(
        """INSERT INTO workspace_subscriptions(workspace_id, plan_code, status, current_period_end, note, updated_at)
           VALUES (%s,%s,'lifetime',NULL,'AppSumo lifetime deal', now())
           ON CONFLICT (workspace_id) DO UPDATE SET plan_code=EXCLUDED.plan_code, status='lifetime',
             current_period_end=NULL, note=EXCLUDED.note, auto_renew=FALSE, trial_ends_at=NULL, updated_at=now()""",
        (ws, plan_code))
    cache.bump(ws)


def _revoke(ws: int) -> None:
    """End a workspace's lifetime access: the same state as a paid plan that ran out."""
    db.execute("UPDATE workspace_subscriptions SET status='active', current_period_end = now() - interval '1 second', "
               "note='Lifetime licence deactivated', auto_renew=FALSE, updated_at=now() WHERE workspace_id=%s", (ws,))
    cache.bump(ws)


# ---------------- a customer claims a licence ----------------

def claim(ws: int, license_key: str) -> dict:
    """Tie a licence to a workspace and unlock its tier. Idempotent for the same workspace."""
    key = (license_key or "").strip()
    row = get(key)
    if not row:
        raise HTTPException(404, "We don't recognise that licence key yet. If you just bought it, wait a minute and try "
                                 "again; otherwise check it on your AppSumo account.")
    if row["status"] in ("deactivated", "superseded"):
        raise HTTPException(400, "That licence is no longer active (it was refunded or replaced by an upgrade).")
    if row["workspace_id"] and row["workspace_id"] != ws:
        raise HTTPException(409, "That licence is already used by another workspace.")
    current = for_workspace(ws)
    if current and current["license_key"] != key and current["tier"] >= row["tier"]:
        raise HTTPException(409, f"This workspace already has a Tier {current['tier']} lifetime licence. "
                                 "Use the new one on another workspace, or upgrade on AppSumo.")
    db.execute("UPDATE lifetime_licenses SET workspace_id=%s, claimed_at=COALESCE(claimed_at, now()), updated_at=now() "
               "WHERE license_key=%s", (ws, key))
    if current and current["license_key"] != key:         # a higher tier replaces the lower one on this workspace
        db.execute("UPDATE lifetime_licenses SET status='superseded', updated_at=now() WHERE license_key=%s",
                   (current["license_key"],))
    _grant(ws, row["plan_code"])
    return {"license_key": key, "tier": row["tier"], "plan_code": row["plan_code"]}


# ---------------- AppSumo webhooks ----------------

def _record(payload: dict, outcome: str) -> None:
    db.execute("INSERT INTO appsumo_events(license_key, event, payload, outcome) VALUES (%s,%s,%s,%s)",
               (str(payload.get("license_key") or "")[:80], str(payload.get("event") or "")[:20], db.Json(payload), outcome[:200]))


def handle_event(payload: dict) -> str:
    """Apply one verified webhook. Returns a short outcome for the audit log. Safe to receive twice."""
    ev = str(payload.get("event") or "")
    key = str(payload.get("license_key") or "").strip()
    if payload.get("test"):
        _record(payload, "test: no change")
        return "test"
    if payload.get("parent_license_key") or ev == "migrate":
        _record(payload, "add-on event ignored (HookTrack sells no add-ons)")
        return "ignored"
    if not key:
        _record(payload, "no licence key")
        return "ignored"
    tier = _tier(payload)
    plan = plan_code_for_tier(tier)

    if ev == "purchase":
        db.execute("""INSERT INTO lifetime_licenses(license_key, tier, plan_code, status, last_event)
                      VALUES (%s,%s,%s,'inactive','purchase')
                      ON CONFLICT (license_key) DO UPDATE SET tier=EXCLUDED.tier, plan_code=EXCLUDED.plan_code,
                        last_event='purchase', updated_at=now()
                      WHERE lifetime_licenses.status = 'inactive'""", (key, tier, plan))
        out = "purchase stored"
        sp = spots()
        if sp["cap"] and sp["sold"] >= min(ALERT_AT, sp["cap"]):
            log.warning("OPERATOR ALERT [lifetime] %s of %s founding licences sold: end the AppSumo deal at %s",
                        sp["sold"], sp["cap"], sp["cap"])

    elif ev == "activate":
        db.execute("""INSERT INTO lifetime_licenses(license_key, tier, plan_code, status, last_event, activated_at)
                      VALUES (%s,%s,%s,'active','activate', now())
                      ON CONFLICT (license_key) DO UPDATE SET status='active', last_event='activate',
                        activated_at=COALESCE(lifetime_licenses.activated_at, now()), updated_at=now()
                      WHERE lifetime_licenses.status IN ('inactive','active')""", (key, tier, plan))
        out = "activated"

    elif ev in ("upgrade", "downgrade"):
        prev = str(payload.get("prev_license_key") or "").strip()
        prev_row = get(prev) if prev else None
        ws = prev_row["workspace_id"] if prev_row else None
        db.execute("""INSERT INTO lifetime_licenses(license_key, tier, plan_code, status, workspace_id, prev_license_key,
                                                    last_event, activated_at, claimed_at)
                      VALUES (%s,%s,%s,'active',%s,%s,%s, now(), CASE WHEN %s IS NULL THEN NULL ELSE now() END)
                      ON CONFLICT (license_key) DO UPDATE SET tier=EXCLUDED.tier, plan_code=EXCLUDED.plan_code,
                        status='active', workspace_id=COALESCE(lifetime_licenses.workspace_id, EXCLUDED.workspace_id),
                        prev_license_key=EXCLUDED.prev_license_key, last_event=EXCLUDED.last_event, updated_at=now()""",
                   (key, tier, plan, ws, prev or None, ev, ws))
        if prev_row:
            db.execute("UPDATE lifetime_licenses SET status='superseded', last_event=%s, updated_at=now() WHERE license_key=%s",
                       (ev, prev))
        if ws:
            _grant(ws, plan)                          # the customer keeps working on the new tier straight away
        out = f"{ev}: {prev[:8] or '?'} -> {key[:8]}" + (" (workspace moved)" if ws else " (not yet claimed)")

    elif ev == "deactivate":
        row = get(key)
        if row:
            db.execute("UPDATE lifetime_licenses SET status = CASE WHEN status='superseded' THEN status ELSE 'deactivated' END, "
                       "last_event='deactivate', deactivated_at=now(), updated_at=now() WHERE license_key=%s", (key,))
            ws = row["workspace_id"]
            # only end access if this licence is still what the workspace runs on (an upgrade may have replaced it)
            if ws and row["status"] != "superseded" and not for_workspace(ws):
                _revoke(ws)
            out = "deactivated" + (" (workspace access ended)" if ws and row["status"] != "superseded" else "")
        else:
            db.execute("INSERT INTO lifetime_licenses(license_key, tier, plan_code, status, last_event, deactivated_at) "
                       "VALUES (%s,%s,%s,'deactivated','deactivate', now()) ON CONFLICT DO NOTHING", (key, tier, plan))
            out = "deactivate for an unknown licence stored"
    else:
        out = f"unknown event {ev!r} ignored"
    _record(payload, out)
    return out


def recent(limit: int = 100) -> list[dict]:
    rows = db.query("""SELECT l.license_key, l.tier, l.status, l.workspace_id, w.name AS workspace_name, l.last_event,
                              l.created_at, l.claimed_at, l.deactivated_at
                       FROM lifetime_licenses l LEFT JOIN workspaces w ON w.id = l.workspace_id
                       ORDER BY l.updated_at DESC LIMIT %s""", (limit,))
    out = []
    for r in rows:
        o = dict(r)
        o["license_key_short"] = o["license_key"][:8] + "…" + o["license_key"][-4:]
        for k in ("created_at", "claimed_at", "deactivated_at"):
            if o.get(k) and hasattr(o[k], "isoformat"):
                o[k] = o[k].isoformat()
        out.append(o)
    return out


# ---------------- what the app and the landing page show ----------------

def buy_url() -> str:
    import os
    return (os.environ.get("APPSUMO_BUY_URL") or "").strip()


def _plan_card(p: dict) -> dict:
    return {"code": p["code"], "name": p["name"], "description": p["description"], "tier": int(p["code"][-1]),
            "price_minor": p["price_minor"], "currency": p["currency"], "interval": "lifetime",
            "max_accounts": p["max_accounts"], "max_members": p["max_members"], "max_ai_calls": p["max_ai_calls"],
            "max_connected": p["max_connected"], "max_credits": p["max_credits"], "max_reports": p["max_reports"]}


def public_view(trial_days: int) -> dict:
    """Landing page: the lifetime tiers (display prices; AppSumo sets the real ones) and the trial."""
    return {"billing_enabled": True, "mode": "lifetime", "trial_days": 0, "spots": spots(),
            "plans": [_plan_card(p) for p in plans()], "buy_url": buy_url()}


def workspace_view(ws: int) -> dict:
    lic = for_workspace(ws)
    return {"tiers": [_plan_card(p) for p in plans()], "buy_url": buy_url(), "spots": spots(),
            "license": ({"tier": lic["tier"], "status": lic["status"], "key_hint": lic["license_key"][:8] + "…"}
                        if lic else None)}
