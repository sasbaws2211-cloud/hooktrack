"""Activation funnel: where do new workspaces stop?

signup -> account added -> first sync -> hook sent to Posts -> first publish -> first verdict -> subscribed

Each step is the *first time* a workspace did it (table activation_events, idempotent); signup is the workspace's
created_at and "subscribed" is its first successful payment. Steps are marked from the places that already do the
work, so a marking failure can never fail the real action.
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta, timezone

from . import db

log = logging.getLogger("hooktrack.activation")

EVENTS = ("account_added", "first_sync", "hook_sent", "first_publish", "first_verdict")

STEPS = [
    ("signup", "Signed up"),
    ("account_added", "Added an account"),
    ("first_sync", "First sync"),
    ("hook_sent", "Sent a hook to Posts"),
    ("first_publish", "First publish"),
    ("first_verdict", "First verdict"),
    ("subscribed", "Subscribed"),
]


def mark(ws: int, event: str) -> None:
    """Record that `ws` reached `event` (only the first time counts). Never raises."""
    if event not in EVENTS or not ws:
        return
    try:
        db.execute("INSERT INTO activation_events(workspace_id, event) VALUES (%s,%s) ON CONFLICT DO NOTHING", (ws, event))
    except Exception:
        log.exception("could not record activation event %s", event)


def funnel(days: int = 30) -> dict:
    """Workspaces created in the last `days` days (0 = all of them), and how far each got."""
    since = None if days <= 0 else datetime.now(timezone.utc) - timedelta(days=days)
    where, args = ("", ()) if since is None else ("WHERE w.created_at >= %s", (since,))
    wss = db.query(f"SELECT w.id, w.name, w.created_at FROM workspaces w {where} ORDER BY w.created_at", args)
    ids = [w["id"] for w in wss]
    at: dict[int, dict] = {w["id"]: {"signup": w["created_at"]} for w in wss}
    if ids:
        for r in db.query("SELECT workspace_id, event, at FROM activation_events WHERE workspace_id = ANY(%s)", (ids,)):
            at[r["workspace_id"]][r["event"]] = r["at"]
        for r in db.query("SELECT workspace_id, MIN(paid_at) AS t FROM payments WHERE status='success' "
                          "AND workspace_id = ANY(%s) GROUP BY workspace_id", (ids,)):
            if r["t"]:
                at[r["workspace_id"]]["subscribed"] = r["t"]
    total = len(wss)
    steps, prev = [], total
    for key, label in STEPS:
        reached = [w for w in wss if key in at[w["id"]]]
        hours = [(at[w["id"]][key] - w["created_at"]).total_seconds() / 3600 for w in reached if key != "signup"]
        n = len(reached)
        steps.append({"key": key, "label": label, "reached": n,
                      "pct_of_signups": round(100 * n / total) if total else 0,
                      "pct_of_previous": min(100, round(100 * n / prev)) if prev and key != "signup" else (100 if key == "signup" else 0),
                      "median_hours": round(statistics.median(hours), 1) if hours else None})
        prev = n
    # "stuck": the workspaces that got furthest-not-all-the-way, oldest first, with the step they are waiting at
    now = datetime.now(timezone.utc)
    order = [k for k, _ in STEPS]
    stuck = []
    for w in wss:
        reached = [k for k in order if k in at[w["id"]]]
        if len(reached) == len(order):
            continue
        nxt = next(k for k in order if k not in at[w["id"]])
        age = (now - w["created_at"]).total_seconds() / 86400
        stuck.append({"id": w["id"], "name": w["name"], "created_at": w["created_at"].isoformat(),
                      "waiting_for": nxt, "waiting_label": dict(STEPS)[nxt], "age_days": round(age, 1)})
    stuck = [s for s in stuck if s["age_days"] >= 1]
    stuck.sort(key=lambda s: -s["age_days"])
    return {"days": days, "total": total, "steps": steps, "stuck": stuck[:25], "stuck_total": len(stuck)}
