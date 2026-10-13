"""Clients (a brand an agency manages) and shareable, read-only client reports.

A report shows one client's accounts, or one account, for the last N days: views and how they moved, the winning
hooks, and how the videos made from winners did against their originals. It is computed live each time it is opened,
so it is never stale, and it exposes aggregates only (no workspace ids, keys or other accounts). The link's random
token is the whole credential; deleting the link revokes it immediately.
"""
from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import auth, billing, db, logic
from .auth import Ctx, admin, current

router = APIRouter(prefix="/api")
pages = APIRouter()

PERIODS = (7, 14, 30, 90)
MAX_LISTED = 5


# ---------- clients ----------

class ClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    notes: str = Field("", max_length=500)


class ClientPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    notes: Optional[str] = Field(None, max_length=500)


def own_client(ws: int, client_id: int) -> dict:
    row = db.one("SELECT * FROM clients WHERE id=%s AND workspace_id=%s", (client_id, ws))
    if row is None:
        raise HTTPException(404, "Client not found")
    return row


@router.get("/clients")
def clients_list(ctx: Ctx = Depends(current)):
    rows = db.query(
        """SELECT c.id, c.name, c.notes, c.created_at,
                  COUNT(a.id)::int AS accounts,
                  COALESCE(array_agg(a.handle ORDER BY a.handle) FILTER (WHERE a.id IS NOT NULL), '{}') AS handles
           FROM clients c LEFT JOIN accounts a ON a.client_id = c.id
           WHERE c.workspace_id=%s GROUP BY c.id ORDER BY lower(c.name)""", (ctx.workspace_id,))
    return {"clients": rows}


@router.post("/clients")
def client_create(b: ClientIn, ctx: Ctx = Depends(admin)):
    name = b.name.strip()
    if not name:
        raise HTTPException(400, "Give the client a name")
    if db.one("SELECT 1 FROM clients WHERE workspace_id=%s AND lower(name)=lower(%s)", (ctx.workspace_id, name)):
        raise HTTPException(409, "You already have a client with that name")
    return db.one("INSERT INTO clients(workspace_id, name, notes) VALUES (%s,%s,%s) RETURNING id, name, notes",
                  (ctx.workspace_id, name, b.notes.strip()))


@router.patch("/clients/{cid}")
def client_update(cid: int, b: ClientPatch, ctx: Ctx = Depends(admin)):
    own_client(ctx.workspace_id, cid)
    d = b.model_dump(exclude_unset=True)
    if "name" in d:
        d["name"] = (d["name"] or "").strip()
        if not d["name"]:
            raise HTTPException(400, "Give the client a name")
        if db.one("SELECT 1 FROM clients WHERE workspace_id=%s AND lower(name)=lower(%s) AND id<>%s",
                  (ctx.workspace_id, d["name"], cid)):
            raise HTTPException(409, "You already have a client with that name")
    if "notes" in d:
        d["notes"] = (d["notes"] or "").strip()
    if d:
        sets = ", ".join(f"{k}=%s" for k in d)
        db.execute(f"UPDATE clients SET {sets} WHERE id=%s AND workspace_id=%s", (*d.values(), cid, ctx.workspace_id))
    return own_client(ctx.workspace_id, cid)


@router.delete("/clients/{cid}")
def client_delete(cid: int, ctx: Ctx = Depends(admin)):
    """Accounts are kept (they just become unassigned); this client's report links stop working."""
    own_client(ctx.workspace_id, cid)
    db.execute("DELETE FROM clients WHERE id=%s AND workspace_id=%s", (cid, ctx.workspace_id))
    return {"ok": True}


class AssignIn(BaseModel):
    account_ids: list[int] = Field(max_length=500)


@router.put("/clients/{cid}/accounts")
def client_set_accounts(cid: int, b: AssignIn, ctx: Ctx = Depends(admin)):
    """Make exactly these accounts this client's. Accounts removed from the list become unassigned."""
    own_client(ctx.workspace_id, cid)
    ids = list(dict.fromkeys(b.account_ids))
    if ids:
        n = db.one("SELECT COUNT(*) AS n FROM accounts WHERE workspace_id=%s AND id = ANY(%s)",
                   (ctx.workspace_id, ids))["n"]
        if n != len(ids):
            raise HTTPException(400, "Unknown account")
    db.execute("UPDATE accounts SET client_id=NULL WHERE workspace_id=%s AND client_id=%s AND NOT (id = ANY(%s))",
               (ctx.workspace_id, cid, ids))
    if ids:
        db.execute("UPDATE accounts SET client_id=%s WHERE workspace_id=%s AND id = ANY(%s)", (cid, ctx.workspace_id, ids))
    return {"ok": True, "accounts": len(ids)}


# ---------- the report itself ----------

def _scope(link: dict) -> dict:
    ws = link["workspace_id"]
    if link.get("client_id"):
        c = db.one("SELECT name FROM clients WHERE id=%s AND workspace_id=%s", (link["client_id"], ws))
        accs = db.query("SELECT id, handle, platform FROM accounts WHERE workspace_id=%s AND client_id=%s ORDER BY handle",
                        (ws, link["client_id"]))
        return {"type": "client", "name": (c or {}).get("name") or "Client", "accounts": accs}
    a = db.one("SELECT id, handle, platform FROM accounts WHERE id=%s AND workspace_id=%s", (link["account_id"], ws))
    return {"type": "account", "name": (a or {}).get("handle") or "Account", "accounts": [a] if a else []}


def _pct(cur: int, prev: int) -> Optional[int]:
    return round(100 * (cur - prev) / prev) if prev else None


def build(link: dict) -> dict:
    """The report for one link row (needs workspace_id, client_id|account_id, days, show_hooks, title)."""
    from . import main as m, results

    ws = link["workspace_id"]
    sc = _scope(link)
    ids = [a["id"] for a in sc["accounts"]]
    days = int(link.get("days") or 30)
    today = logic.today(ws)
    start = today - timedelta(days=days - 1)
    pstart = start - timedelta(days=days)
    wsname = (db.one("SELECT name FROM workspaces WHERE id=%s", (ws,)) or {}).get("name") or ""

    per = {a["id"]: {"handle": a["handle"], "platform": a["platform"], "views": 0, "prev_views": 0, "posts": 0, "winners": 0}
           for a in sc["accounts"]}
    series: dict = {}
    if ids:
        for r in db.query("""SELECT account_id, day, views FROM daily_views
                             WHERE account_id = ANY(%s) AND day BETWEEN %s AND %s""", (ids, pstart, today)):
            if r["day"] >= start:
                per[r["account_id"]]["views"] += r["views"] or 0
                series[r["day"]] = series.get(r["day"], 0) + (r["views"] or 0)
            else:
                per[r["account_id"]]["prev_views"] += r["views"] or 0

    perf = logic.performance(ws) if ids else {}
    winners, posts_total = [], 0
    if ids:
        st = logic.get_settings(ws)
        rows = db.query(m.POST_SELECT + "WHERE p.workspace_id=%s AND p.account_id = ANY(%s) AND p.external_id IS NOT NULL "
                        "AND p.post_date BETWEEN %s AND %s ORDER BY p.views DESC", (ws, ids, start, today))
        for r in rows:
            posts_total += 1
            per[r["account_id"]]["posts"] += 1
            pf = perf.get(r["id"]) or {}
            if pf.get("label") == "WINNER":
                per[r["account_id"]]["winners"] += 1
                winners.append({"hook": (r.get("hook") or "").strip()[:160] if link.get("show_hooks") else "",
                                "handle": r.get("handle") or "", "views": r.get("views") or 0,
                                "multiple": pf.get("multiple"), "date": r["post_date"].isoformat(),
                                "url": r.get("url") or ""})
        winners.sort(key=lambda w: -(w["multiple"] or 0))

    # videos made from winning hooks whose results landed in this period
    made = {"made": 0, "posted": 0, "judged": 0, "beat": 0, "matched": 0, "fell_short": 0, "examples": []}
    if ids:
        st = logic.get_settings(ws)
        cards = db.query(m.POST_SELECT + "WHERE p.workspace_id=%s AND p.account_id = ANY(%s) AND p.external_id IS NULL "
                         "AND (p.followup_of IS NOT NULL OR p.cloned_from IS NOT NULL)", (ws, ids))
        cards = [m._post_out(c, st) for c in cards]
        made["made"] = sum(1 for c in cards if c.get("created_at") and str(c["created_at"])[:10] >= start.isoformat())
        results.attach(ws, cards, perf)
        for c in cards:
            res = c.get("result")
            if not res:
                continue
            rp = db.one("SELECT post_date FROM posts WHERE id=%s", (res["post_id"],))
            if not rp or not (start <= rp["post_date"] <= today):
                continue
            made["posted"] += 1
            v = res.get("verdict")
            if v and res.get("settled"):
                made["judged"] += 1
                made[v] += 1
                if v == "beat" and len(made["examples"]) < MAX_LISTED:
                    made["examples"].append({"hook": (c.get("hook") or "").strip()[:160] if link.get("show_hooks") else "",
                                             "handle": c.get("handle") or "", "made": res.get("multiple"),
                                             "original": res.get("original_multiple")})

    total, prev = sum(p["views"] for p in per.values()), sum(p["prev_views"] for p in per.values())
    accounts = sorted(per.values(), key=lambda p: -p["views"])
    for p in accounts:
        p["change_pct"] = _pct(p["views"], p["prev_views"])
    day_list = [(start + timedelta(days=i)) for i in range(days)]
    return {
        "title": (link.get("title") or "").strip() or f"{sc['name']}: performance report",
        "prepared_by": wsname, "scope": {"type": sc["type"], "name": sc["name"]},
        "period": {"days": days, "start": start.isoformat(), "end": today.isoformat()},
        "headline": {"views": total, "prev_views": prev, "change_pct": _pct(total, prev), "posts": posts_total,
                     "winners": len(winners), "accounts": len(accounts)},
        "accounts": accounts, "winners": winners[:MAX_LISTED], "winner_count": len(winners), "made": made,
        "series": [{"day": d.isoformat(), "views": series.get(d, 0)} for d in day_list],
        "show_hooks": bool(link.get("show_hooks")),
        "note": "Views come from each platform's own public numbers as of the last sync. A winner is a post that "
                "did at least 2x its own account's usual views.",
    }


# ---------- managing links ----------

class LinkIn(BaseModel):
    client_id: Optional[int] = None
    account_id: Optional[int] = None
    title: str = Field("", max_length=120)
    days: int = 30
    show_hooks: bool = True


def _link_out(r: dict) -> dict:
    return {"id": r["id"], "token": r["token"], "path": f"/r/{r['token']}", "title": r["title"], "days": r["days"],
            "show_hooks": r["show_hooks"], "client_id": r["client_id"], "account_id": r["account_id"],
            "scope_name": r.get("scope_name") or "", "created_at": r["created_at"], "view_count": r["view_count"],
            "last_viewed_at": r["last_viewed_at"]}


def _own_link(ws: int, lid: int) -> dict:
    row = db.one("SELECT * FROM report_links WHERE id=%s AND workspace_id=%s", (lid, ws))
    if row is None:
        raise HTTPException(404, "Report link not found")
    return row


def require_report_capacity(ws: int) -> None:
    """402 when one more active link would pass the plan's allowance. No-op while billing is off."""
    if not billing.enabled():
        return
    billing.require_active(ws)
    limit = billing.effective_plan(ws).get("max_reports", -1)
    used = db.one("SELECT COUNT(*) AS n FROM report_links WHERE workspace_id=%s", (ws,))["n"]
    if limit != -1 and used >= limit:
        plan = billing.effective_plan(ws)
        what = "doesn't include shareable client reports" if limit == 0 else \
            f"includes {limit} shareable client report{'' if limit == 1 else 's'}"
        raise HTTPException(402, f"Your {plan['name']} plan {what}. Upgrade under Billing to share more. "
                                 f"You can still preview any report here.")


@router.get("/reports")
def reports_list(ctx: Ctx = Depends(current)):
    rows = db.query(
        """SELECT l.*, COALESCE(c.name, a.handle) AS scope_name FROM report_links l
           LEFT JOIN clients c ON c.id = l.client_id LEFT JOIN accounts a ON a.id = l.account_id
           WHERE l.workspace_id=%s ORDER BY l.created_at DESC""", (ctx.workspace_id,))
    limit = billing.effective_plan(ctx.workspace_id).get("max_reports", -1) if billing.enabled() else -1
    return {"links": [_link_out(r) for r in rows], "limit": limit}


@router.post("/reports")
def report_create(b: LinkIn, ctx: Ctx = Depends(admin)):
    ws = ctx.workspace_id
    if bool(b.client_id) == bool(b.account_id):
        raise HTTPException(400, "Choose a client or an account")
    if b.client_id:
        own_client(ws, b.client_id)
    elif not db.one("SELECT 1 FROM accounts WHERE id=%s AND workspace_id=%s", (b.account_id, ws)):
        raise HTTPException(404, "Account not found")
    if b.days not in PERIODS:
        raise HTTPException(400, f"Choose a period of {', '.join(map(str, PERIODS))} days")
    require_report_capacity(ws)
    row = db.one(
        """INSERT INTO report_links(workspace_id, token, title, client_id, account_id, days, show_hooks, created_by)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
        (ws, secrets.token_urlsafe(24), b.title.strip(), b.client_id, b.account_id, b.days, b.show_hooks, ctx.user_id))
    return _link_out(row)


@router.delete("/reports/{lid}")
def report_revoke(lid: int, ctx: Ctx = Depends(admin)):
    _own_link(ctx.workspace_id, lid)
    db.execute("DELETE FROM report_links WHERE id=%s AND workspace_id=%s", (lid, ctx.workspace_id))
    return {"ok": True}


class PreviewIn(BaseModel):
    client_id: Optional[int] = None
    account_id: Optional[int] = None
    title: str = Field("", max_length=120)
    days: int = 30
    show_hooks: bool = True


@router.post("/reports/preview")
def report_preview(b: PreviewIn, ctx: Ctx = Depends(current)):
    """What a link would show, without creating one (works on every plan)."""
    ws = ctx.workspace_id
    if bool(b.client_id) == bool(b.account_id):
        raise HTTPException(400, "Choose a client or an account")
    if b.client_id:
        own_client(ws, b.client_id)
    elif not db.one("SELECT 1 FROM accounts WHERE id=%s AND workspace_id=%s", (b.account_id, ws)):
        raise HTTPException(404, "Account not found")
    if b.days not in PERIODS:
        raise HTTPException(400, "Unsupported period")
    return build({"workspace_id": ws, "client_id": b.client_id, "account_id": b.account_id, "title": b.title,
                  "days": b.days, "show_hooks": b.show_hooks})


# ---------- the public side ----------

NO_INDEX = {"X-Robots-Tag": "noindex, nofollow, noarchive", "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer"}


@router.get("/public/report/{token}")
def public_report(token: str, request: Request):
    auth._throttle("report-ip:" + auth._client_ip(request), 240, 3600)
    link = db.one("SELECT * FROM report_links WHERE token=%s", (token[:100],))
    if link is None or billing.access_blocked(link["workspace_id"]):
        raise HTTPException(404, "This report link is no longer active.")
    data = build(link)
    db.execute("UPDATE report_links SET view_count=view_count+1, last_viewed_at=now() WHERE id=%s", (link["id"],))
    return JSONResponse(data, headers=NO_INDEX)


@pages.get("/r/{token}")
def report_page(token: str):
    from .main import STATIC
    return FileResponse(STATIC / "report.html", headers=NO_INDEX)
