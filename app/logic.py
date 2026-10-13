"""Scoring and decision rules (the spreadsheet's logic, made configurable)."""
from datetime import date, timedelta

from . import db

DEFAULT_SETTINGS = {
    "scale_weekly": 100_000,   # weekly views that earn SCALE (needs a best hook)
    "kill_after_posts": 20,    # this many posts without a breakout = KILL
    "stall_views": 1_000,      # "breakout" = any single post at/above this
    "scale_vsc": 24,           # VSC /30 at or above -> SCALE
    "mutate_vsc": 21,          # VSC /30 at or above (below scale) -> MUTATE
}


def get_settings(ws: int) -> dict:
    out = dict(DEFAULT_SETTINGS)
    for row in db.query("SELECT key, value FROM settings WHERE workspace_id = %s", (ws,)):
        if row["key"] in out:
            out[row["key"]] = row["value"]
    return out


def save_settings(ws: int, values: dict) -> dict:
    for k, v in values.items():
        if k in DEFAULT_SETTINGS:
            db.execute(
                "INSERT INTO settings(workspace_id, key, value) VALUES (%s, %s, %s) "
                "ON CONFLICT (workspace_id, key) DO UPDATE SET value = EXCLUDED.value",
                (ws, k, db.Json(int(v))),
            )
    return get_settings(ws)


def vsc_total(v, s, c):
    if v is None or s is None or c is None:
        return None
    return v + s + c


def post_decision(total, st) -> str:
    if total is None:
        return ""
    if total >= st["scale_vsc"]:
        return "SCALE"
    if total >= st["mutate_vsc"]:
        return "MUTATE"
    return "KILL"


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_grid(ws: int, start: date, days: int = 7):
    """Accounts x days grid with weekly totals and per-account decision."""
    st = get_settings(ws)
    end = start + timedelta(days=days - 1)
    accounts = db.query(
        "SELECT a.*, p.label AS phone_label, p.color AS phone_color, p.position AS phone_pos "
        "FROM accounts a LEFT JOIN phones p ON p.id = a.phone_id "
        "WHERE a.workspace_id = %s ORDER BY p.position NULLS LAST, p.id NULLS LAST, a.handle",
        (ws,),
    )
    views = {}
    for r in db.query(
        "SELECT account_id, day, views, source FROM daily_views "
        "WHERE workspace_id = %s AND day BETWEEN %s AND %s",
        (ws, start, end),
    ):
        views[(r["account_id"], r["day"])] = r

    # scored hooks inside the week -> "best hook?"
    best = set()
    for r in db.query(
        "SELECT DISTINCT account_id FROM posts WHERE workspace_id = %s AND status = 'posted' "
        "AND post_date BETWEEN %s AND %s "
        "AND v IS NOT NULL AND s IS NOT NULL AND c IS NOT NULL AND (v + s + c) >= %s",
        (ws, start, end, st["scale_vsc"]),
    ):
        best.add(r["account_id"])

    stalled = stalled_accounts(ws, st)
    day_list = [start + timedelta(days=i) for i in range(days)]
    rows = []
    for a in accounts:
        cells, total = [], 0
        for d in day_list:
            cell = views.get((a["id"], d))
            n = cell["views"] if cell else None
            total += n or 0
            cells.append({"day": d.isoformat(), "views": n, "source": cell["source"] if cell else None})
        best_hook = a["id"] in best
        decision, reason = account_decision(a, total, best_hook, a["id"] in stalled, st)
        rows.append({**a, "created_at": None, "cells": cells, "weekly_total": total,
                     "best_hook": best_hook, "decision": decision, "reason": reason})
    day_totals = [sum((r["cells"][i]["views"] or 0) for r in rows) for i in range(days)]
    grand = sum(day_totals)
    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "days": [d.isoformat() for d in day_list],
        "rows": rows, "day_totals": day_totals, "grand_total": grand,
        "avg_per_account": round(grand / len(rows)) if rows else 0,
        "settings": st,
    }


def stalled_accounts(ws: int, st) -> set:
    """Accounts whose last N posted posts never broke `stall_views`."""
    n = st["kill_after_posts"]
    out = set()
    rows = db.query(
        "SELECT account_id, views FROM ("
        "  SELECT account_id, views, ROW_NUMBER() OVER (PARTITION BY account_id "
        "    ORDER BY post_date DESC, id DESC) AS rn FROM posts WHERE workspace_id = %s AND status = 'posted'"
        ") t WHERE rn <= %s",
        (ws, n),
    )
    agg = {}
    for r in rows:
        cnt, mx = agg.get(r["account_id"], (0, 0))
        agg[r["account_id"]] = (cnt + 1, max(mx, r["views"]))
    for acc, (cnt, mx) in agg.items():
        if cnt >= n and mx < st["stall_views"]:
            out.add(acc)
    return out


def account_decision(a, weekly_total, best_hook, is_stalled, st):
    if a["status"] == "killed":
        return "KILLED", "Marked killed"
    if a["status"] in ("paused", "warming"):
        return "—", a["status"].capitalize()
    if is_stalled:
        return "KILL", f"No post above {st['stall_views']:,} views in the last {st['kill_after_posts']} posts"
    if weekly_total >= st["scale_weekly"] and best_hook:
        return "SCALE", f"{weekly_total:,} views this week with a top-scoring hook"
    if weekly_total >= st["scale_weekly"]:
        return "HOLD", f"{weekly_total:,} views but no hook scored {st['scale_vsc']}+ yet — score your hooks"
    return "HOLD", ""


def dashboard(ws: int, today: date | None = None):
    today = today or date.today()
    st = get_settings(ws)
    this_week = week_grid(ws, monday(today))
    prev_week = week_grid(ws, monday(today) - timedelta(days=7))
    # last 8 weeks trend
    trend = []
    first = monday(today) - timedelta(days=7 * 7)
    sums = {
        r["wk"]: r["v"]
        for r in db.query(
            "SELECT date_trunc('week', day)::date AS wk, SUM(views)::bigint AS v "
            "FROM daily_views WHERE workspace_id = %s AND day >= %s GROUP BY 1", (ws, first))
    }
    for i in range(8):
        wk = first + timedelta(days=7 * i)
        trend.append({"week": wk.isoformat(), "views": int(sums.get(wk, 0))})

    by_phone = {}
    for r in this_week["rows"]:
        key = r["phone_label"] or "No phone"
        p = by_phone.setdefault(key, {"phone": key, "color": r["phone_color"] or "#94a3b8", "views": 0})
        p["views"] += r["weekly_total"]

    top_hooks = db.query(
        "SELECT p.id, p.hook, p.views, p.comments, p.intent, p.v, p.s, p.c, a.handle "
        "FROM posts p LEFT JOIN accounts a ON a.id = p.account_id "
        "WHERE p.workspace_id = %s AND p.status = 'posted' AND p.hook <> '' ORDER BY p.views DESC LIMIT 5",
        (ws,),
    )
    for h in top_hooks:
        h["vsc"] = vsc_total(h["v"], h["s"], h["c"])
    upcoming = db.query(
        "SELECT p.id, p.post_date, p.hook, p.status, a.handle FROM posts p "
        "LEFT JOIN accounts a ON a.id = p.account_id "
        "WHERE p.workspace_id = %s AND p.status IN ('planned','idea') AND p.post_date >= %s "
        "ORDER BY p.post_date LIMIT 6",
        (ws, today),
    )
    unscored = db.one(
        "SELECT COUNT(*) AS n FROM posts WHERE workspace_id = %s AND status='posted' "
        "AND (v IS NULL OR s IS NULL OR c IS NULL)", (ws,)
    )["n"]
    counts = {"SCALE": 0, "HOLD": 0, "KILL": 0}
    for r in this_week["rows"]:
        if r["decision"] in counts:
            counts[r["decision"]] += 1
    return {
        "week_start": this_week["start"],
        "week_views": this_week["grand_total"],
        "prev_week_views": prev_week["grand_total"],
        "accounts": len(this_week["rows"]),
        "avg_per_account": this_week["avg_per_account"],
        "decisions": counts,
        "trend": trend,
        "by_phone": list(by_phone.values()),
        "leaders": sorted(
            [{"handle": r["handle"], "views": r["weekly_total"], "decision": r["decision"]}
             for r in this_week["rows"]], key=lambda x: -x["views"])[:5],
        "act_on": [
            {"handle": r["handle"], "decision": r["decision"], "reason": r["reason"]}
            for r in this_week["rows"] if r["decision"] in ("SCALE", "KILL")],
        "top_hooks": top_hooks,
        "upcoming": upcoming,
        "unscored_posts": unscored,
        "settings": st,
    }
