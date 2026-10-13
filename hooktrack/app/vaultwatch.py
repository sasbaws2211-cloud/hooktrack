"""Watch the platform's own SociaVault credit balance and the health of syncing.

Every customer's sync spends from one pool of the operator's credits, so this is a single point of failure.
  * check()        asks SociaVault for the balance (GET /v1/credits, free) and classifies it:
                   ok -> low (<= warn_pct of the peak) -> paused (<= pause_pct) -> exhausted (0)
  * paused/exhausted stop the *scheduled* sync (the non-essential, automatic spend); a customer pressing
    Sync still works until the balance is really zero, and the operator's "Run sync now" is never held back
  * failing_accounts() counts accounts whose last syncs failed for provider reasons (not "account not found")
  * the operator is emailed once per state change (at most once a day per state) and the admin console shows it

The "peak" is the highest balance seen since the last top-up, so "20% left" means 20% of the last pack.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import httpx

from . import db, leases

log = logging.getLogger("hooktrack.vaultwatch")

URL = "https://api.sociavault.com/v1/credits"
STATE_KEY = "sv_watch"
CFG_KEY = "sv_watch_cfg"
LEASE = "sociavault-balance-watch"
CHECK_EVERY_S = 30 * 60
REALERT_S = 24 * 3600
FAIL_STREAK = 2              # consecutive failed syncs before an account counts as failing
FAIL_WINDOW_H = 48
FAIL_ALERT_MIN = 3           # ...and this many accounts (or all of them, when there are fewer) before we shout

DEFAULTS = {"warn_pct": 20, "pause_pct": 5, "alert_email": "", "reference": 0}
RANK = {"ok": 0, "unknown": 0, "no_key": 0, "low": 1, "paused": 2, "exhausted": 3}


def _pget(key, default=None):
    row = db.one("SELECT value FROM platform_settings WHERE key=%s", (key,))
    return default if not row or "value" not in row else row["value"]


def _pset(key, value) -> None:
    db.execute("""INSERT INTO platform_settings(key, value, updated_at) VALUES (%s,%s,now())
                  ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()""", (key, db.Json(value)))


def config() -> dict:
    cfg = dict(DEFAULTS)
    saved = _pget(CFG_KEY) or {}
    if isinstance(saved, dict):
        cfg.update({k: saved[k] for k in DEFAULTS if k in saved})
    return cfg


def save_config(warn_pct=None, pause_pct=None, alert_email=None, reference=None) -> dict:
    cfg = config()
    if reference is not None:        # the size of the last credit pack, when the first reading was taken mid-pack
        cfg["reference"] = max(0, int(reference))
    if warn_pct is not None:
        cfg["warn_pct"] = max(1, min(90, int(warn_pct)))
    if pause_pct is not None:
        cfg["pause_pct"] = max(0, min(50, int(pause_pct)))
    if cfg["pause_pct"] >= cfg["warn_pct"]:
        cfg["pause_pct"] = max(0, cfg["warn_pct"] - 1)
    if alert_email is not None:
        cfg["alert_email"] = (alert_email or "").strip()[:254]
    _pset(CFG_KEY, cfg)
    return cfg


def classify(credits: int, peak: int, cfg: dict) -> str:
    if credits <= 0:
        return "exhausted"
    if peak > 0 and credits <= peak * cfg["pause_pct"] / 100:
        return "paused"
    if peak > 0 and credits <= peak * cfg["warn_pct"] / 100:
        return "low"
    return "ok"


def _fetch_credits(api_key: str) -> int:
    """Remaining credits. Raises RuntimeError with a short reason on any failure."""
    try:
        r = httpx.get(URL, headers={"X-API-Key": api_key}, timeout=20)
    except httpx.HTTPError as e:
        raise RuntimeError(f"network error: {e}"[:200])
    if r.status_code != 200:
        raise RuntimeError(f"SociaVault answered HTTP {r.status_code}")
    try:
        data = r.json()
        d = data.get("data") if isinstance(data.get("data"), dict) else data
        return int(d["credits"])
    except Exception:
        raise RuntimeError("SociaVault returned an unexpected balance response")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def state() -> dict:
    """The last known state, no network. Safe to call from any request."""
    s = _pget(STATE_KEY) or {}
    out = {"status": s.get("status") or "unknown", "credits": s.get("credits"), "peak": s.get("peak"),
           "checked_at": s.get("checked_at"), "error": s.get("error") or "", "config": config()}
    out["percent_left"] = (round(100 * out["credits"] / out["peak"]) if out["credits"] is not None and out["peak"] else None)
    out["failing"] = failing_accounts()
    return out


def scheduled_paused() -> bool:
    return (_pget(STATE_KEY) or {}).get("status") in ("paused", "exhausted")


def exhausted() -> bool:
    return (_pget(STATE_KEY) or {}).get("status") == "exhausted"


def _stale(s: dict, max_age_s: float) -> bool:
    t = s.get("checked_at")
    if not t:
        return True
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - dt).total_seconds() >= max_age_s


def check(force: bool = False, max_age_s: float = CHECK_EVERY_S) -> dict:
    """Refresh the balance (if older than max_age_s, or force), classify, and alert on a worse state."""
    from . import sociavault
    s = _pget(STATE_KEY) or {}
    if not _stale(s, 0 if force else max_age_s):
        return state()
    key = sociavault.resolve_server_key()
    if not key:
        s.update(status="no_key", checked_at=_now(), error="")
        _pset(STATE_KEY, s)
        return state()
    cfg = config()
    try:
        credits = _fetch_credits(key)
    except RuntimeError as e:
        s.update(checked_at=_now(), error=str(e))      # a failed check never pauses anything: keep the old status
        if s.get("status") in (None, "no_key"):
            s["status"] = "unknown"
        _pset(STATE_KEY, s)
        log.warning("SociaVault balance check failed: %s", e)
        return state()
    last, peak = s.get("credits"), int(s.get("peak") or 0)
    if last is None or credits > last:       # first reading, or a top-up: the new balance is the reference
        peak = credits
    peak = max(peak, credits, int(cfg.get("reference") or 0))
    status = classify(credits, peak, cfg)
    s.update(credits=credits, peak=peak, status=status, checked_at=_now(), error="")
    _pset(STATE_KEY, s)
    _maybe_alert(status, f"SociaVault balance is {credits} credits ({round(100 * credits / peak) if peak else 0}% of the last top-up).")
    return state()


def tick() -> None:
    """Called every few minutes by the background loop: refresh if due (one worker at a time), then judge failures."""
    with leases.lease(LEASE, ttl=120) as got:
        if not got:
            return
        check(force=False)
        _alert_failures()


# ---------- failures ----------

def record_sync_ok(account_id: int) -> None:
    db.execute("UPDATE accounts SET sync_fail_count=0, sync_error='', sync_error_at=NULL "
               "WHERE id=%s AND sync_fail_count<>0", (account_id,))


def record_sync_failure(account_id: int, message: str) -> None:
    db.execute("UPDATE accounts SET sync_fail_count=sync_fail_count+1, sync_error=%s, sync_error_at=now() WHERE id=%s",
               ((message or "")[:300], account_id))


def failing_accounts() -> dict:
    row = db.one(
        """SELECT COUNT(*) FILTER (WHERE sync_fail_count >= %s AND sync_error_at > now() - make_interval(hours => %s)) AS failing,
                  COUNT(*) AS total
           FROM accounts WHERE sync_enabled AND status='active'""", (FAIL_STREAK, FAIL_WINDOW_H))
    samples = db.query(
        """SELECT a.id, a.handle, a.platform, a.workspace_id, a.sync_error, a.sync_error_at FROM accounts a
           WHERE a.sync_enabled AND a.status='active' AND a.sync_fail_count >= %s
             AND a.sync_error_at > now() - make_interval(hours => %s) ORDER BY a.sync_error_at DESC LIMIT 8""",
        (FAIL_STREAK, FAIL_WINDOW_H))
    for r in samples:
        r["sync_error_at"] = r["sync_error_at"].isoformat() if r["sync_error_at"] else None
    return {"count": row["failing"], "total": row["total"], "samples": samples,
            "alerting": row["failing"] > 0 and row["failing"] >= min(FAIL_ALERT_MIN, max(1, row["total"]))}


def _alert_failures() -> None:
    f = failing_accounts()
    if f["alerting"]:
        errs = "; ".join(sorted({(r["sync_error"] or "")[:80] for r in f["samples"]}))
        _maybe_alert("failing", f"Syncing is failing for {f['count']} of {f['total']} accounts. Recent errors: {errs}")


# ---------- alerts ----------

def _recipient() -> str:
    cfg = config()
    to = cfg.get("alert_email") or os.environ.get("HOOKTRACK_OPS_EMAIL", "").strip()
    if not to:
        try:
            from . import legal
            to = (legal.get().get("email") or "").strip()
        except Exception:
            to = ""
    return to


PROBLEMS = ("low", "paused", "exhausted", "failing", "backup_failed", "drill_failed", "backup_stale")


def _maybe_alert(status: str, detail: str, subject: str = "") -> bool:
    """Email the operator when `status` is a problem and we have not mentioned it in the last day. Never raises."""
    if status not in PROBLEMS:
        return False
    try:
        row = db.one("""INSERT INTO platform_settings(key, value, updated_at) VALUES (%s, %s, now())
                        ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()
                        WHERE platform_settings.updated_at < now() - make_interval(secs => %s)
                        RETURNING key""", (f"sv_alerted_{status}", db.Json(_now()), REALERT_S))
        if row is None:
            return False                       # already told them within the day (atomic: safe with many workers)
        subject = subject or {"low": "SociaVault credits are running low",
                              "paused": "SociaVault credits nearly gone: scheduled syncs paused",
                              "exhausted": "SociaVault credits are used up: syncing is down",
                              "failing": "HookTrack syncs are failing"}.get(status, "HookTrack needs attention")
        log.warning("OPERATOR ALERT [%s] %s", status, detail)
        to = _recipient()
        from . import mailer
        if to and mailer.configured():
            return mailer.send(to, f"[HookTrack] {subject}", f"{detail}\n\nOpen the admin console, Settings -> SociaVault, to top up or check the key.")
    except Exception:
        log.exception("sending operator alert failed")
    return False
