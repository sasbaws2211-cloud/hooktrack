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

import csv
import io
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException

from . import cache, db

log = logging.getLogger("hooktrack.lifetime")
MODES = ("subscriptions", "lifetime", "both")
MAX_TIER = 2                 # 1 code = tier 1, 2 stacked codes = tier 2


def mode() -> str:
    from . import billing
    return billing.billing_mode()


def set_mode(value: str) -> str:
    from . import billing
    if value not in MODES:
        raise HTTPException(400, "Billing mode must be 'subscriptions', 'lifetime' or 'both'")
    billing._pset("billing_mode", value)
    return value


def is_lifetime_mode() -> bool:
    """Lifetime ONLY: no card plans, no trial. In 'both' mode this is False (card plans and a trial exist as well)."""
    return mode() == "lifetime"


def offer() -> Optional[dict]:
    """What the pricing page shows next to the normal plans: the lifetime deal on AppSumo. None until the buy link is set."""
    url = buy_url()
    if not url:
        return None
    p = db.one("SELECT price_minor, currency FROM plans WHERE code='ltd_1'") or {}
    return {"buy_url": url, "price_minor": p.get("price_minor", 4880), "currency": p.get("currency", "USD"),
            "spots": spots()}


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
    return int(db.one("SELECT COUNT(*) AS n FROM lifetime_licenses WHERE status IN ('inactive','active') "
                      "AND (source <> 'code' OR workspace_id IS NOT NULL)")["n"])


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


# ---------------- codes we generate (the CSV we give AppSumo) ----------------

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"        # no 0/O/1/I: easy to read out and retype


def _new_code() -> str:
    body = "".join(secrets.choice(CODE_ALPHABET) for _ in range(12))
    return f"HT-{body[:4]}-{body[4:8]}-{body[8:]}"


def generate_codes(n: int, tier: int = 1) -> list[str]:
    """Make n new unused codes (one workspace redeems each; two can be stacked on one workspace)."""
    n = max(1, min(int(n), 10000))
    tier = max(1, min(MAX_TIER, int(tier)))
    made: list[str] = []
    plan = plan_code_for_tier(tier)
    while len(made) < n:
        batch = list({_new_code() for _ in range(min(250, n - len(made)))})      # a few hundred at a time: one round trip each
        rows = db.query("""INSERT INTO lifetime_licenses(license_key, tier, plan_code, status, last_event, source)
                           SELECT k, %s, %s, 'inactive', 'generated', 'code' FROM unnest(%s::text[]) AS k
                           ON CONFLICT (license_key) DO NOTHING RETURNING license_key""", (tier, plan, batch))
        made.extend(r["license_key"] for r in rows)
    return made


def codes_csv(only_unclaimed: bool = True) -> str:
    """The file AppSumo asks for: plain text, one code per line, one column, no header."""
    rows = db.query("SELECT license_key FROM lifetime_licenses WHERE source='code' AND status='inactive'"
                    + (" AND workspace_id IS NULL" if only_unclaimed else "") + " ORDER BY created_at, license_key")
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for r in rows:
        w.writerow([r["license_key"]])
    return buf.getvalue()


def codes_summary() -> dict:
    r = db.one("""SELECT COUNT(*) FILTER (WHERE source='code') AS total,
                         COUNT(*) FILTER (WHERE source='code' AND status='inactive' AND workspace_id IS NULL) AS unused,
                         COUNT(*) FILTER (WHERE source='code' AND workspace_id IS NOT NULL AND status IN ('inactive','active')) AS redeemed,
                         COUNT(*) FILTER (WHERE source='code' AND status='deactivated') AS refunded
                  FROM lifetime_licenses""")
    return {k: int(r[k] or 0) for k in ("total", "unused", "redeemed", "refunded")}


def normalise_code(raw: str) -> str:
    """Accept a code typed with spaces, lower case or without dashes."""
    flat = re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()
    if flat.startswith("HT") and len(flat) == 14:
        return f"HT-{flat[2:6]}-{flat[6:10]}-{flat[10:14]}"
    return (raw or "").strip()


def find(key: str) -> Optional[dict]:
    k = (key or "").strip()
    return get(k) or get(normalise_code(k))


# ---------------- a customer claims a licence ----------------

def workspace_total(ws: int) -> int:
    """How many codes' worth this workspace holds (an AppSumo tier-2 licence counts as 2)."""
    r = db.one("SELECT COALESCE(SUM(tier), 0) AS n FROM lifetime_licenses WHERE workspace_id=%s AND status IN ('inactive','active')", (ws,))
    return int(r["n"] or 0)


def apply_total(ws: int) -> int:
    """Set the workspace's plan from the licences it holds: 1 code = tier 1, 2 stacked codes = tier 2, none = ended."""
    total = workspace_total(ws)
    if total > 0:
        _grant(ws, plan_code_for_tier(min(total, MAX_TIER)))
    else:
        sub = db.one("SELECT plan_code FROM workspace_subscriptions WHERE workspace_id=%s", (ws,))
        if sub and str(sub["plan_code"]).startswith("ltd_"):      # never end a plan that was bought another way
            _revoke(ws)
    return total


def claim(ws: int, license_key: str) -> dict:
    """Tie a licence to a workspace and unlock its tier. Idempotent for the same workspace; a second code stacks."""
    row = find(license_key)
    if not row:
        raise HTTPException(404, "We don't recognise that code. Check it for typos, or find it on your AppSumo account. "
                                 "If you only just bought it, wait a minute and try again.")
    if row["status"] in ("deactivated", "superseded"):
        raise HTTPException(400, "That code is no longer active (it was refunded or replaced).")
    if row["workspace_id"] and row["workspace_id"] != ws:
        raise HTTPException(409, "That code is already used by another workspace.")
    if row["workspace_id"] != ws:
        if workspace_total(ws) >= MAX_TIER:
            raise HTTPException(409, f"This workspace already has the most you can stack ({MAX_TIER} codes). "
                                     "Use this code on another workspace.")
        db.execute("UPDATE lifetime_licenses SET workspace_id=%s, claimed_at=COALESCE(claimed_at, now()), updated_at=now() "
                   "WHERE license_key=%s", (ws, row["license_key"]))
    total = apply_total(ws)
    plan = plan_code_for_tier(min(total, MAX_TIER))
    return {"license_key": row["license_key"], "tier": min(total, MAX_TIER), "plan_code": plan, "codes": total}


def deactivate(key: str, event: str = "deactivate") -> str:
    """A licence was refunded: it stops counting. The workspace drops a level, or ends if it was its only one."""
    row = find(key)
    if not row:
        return "unknown"
    if row["status"] == "superseded":
        return "superseded: nothing to end"
    db.execute("UPDATE lifetime_licenses SET status='deactivated', last_event=%s, deactivated_at=now(), updated_at=now() "
               "WHERE license_key=%s", (event, row["license_key"]))
    ws = row["workspace_id"]
    if ws:
        apply_total(ws)
    return "deactivated" + (" (workspace updated)" if ws else "")


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
            apply_total(ws)                           # the customer keeps working on the new tier straight away
        out = f"{ev}: {prev[:8] or '?'} -> {key[:8]}" + (" (workspace moved)" if ws else " (not yet claimed)")

    elif ev == "deactivate":
        if get(key):
            out = deactivate(key)
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
                              l.created_at, l.claimed_at, l.deactivated_at, l.source
                       FROM lifetime_licenses l LEFT JOIN workspaces w ON w.id = l.workspace_id
                       WHERE NOT (l.source = 'code' AND l.workspace_id IS NULL AND l.status = 'inactive')
                       ORDER BY l.updated_at DESC LIMIT %s""", (limit,))
    out = []
    for r in rows:
        o = dict(r)
        o["license_key_short"] = o["license_key"] if o["source"] == "code" else o["license_key"][:8] + "…" + o["license_key"][-4:]
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
            "license": ({"tier": min(workspace_total(ws), MAX_TIER) or lic["tier"], "status": lic["status"],
                         "key_hint": lic["license_key"][:8] + "…"}
                        if lic else None)}
