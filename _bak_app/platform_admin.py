"""Platform (super) admin — standalone console at /admin/.

Access: emails listed in HOOKTRACK_PLATFORM_ADMINS (comma-separated), case-insensitive.
Example: HOOKTRACK_PLATFORM_ADMINS=you@company.com,ops@company.com
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import db, sociavault
from .auth import COOKIE, Ctx, session_ctx, _hash_token

router = APIRouter(prefix="/api/platform", tags=["platform"])


def _admin_emails() -> set[str]:
    raw = os.environ.get("HOOKTRACK_PLATFORM_ADMINS") or ""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_platform_admin_email(email: str) -> bool:
    return (email or "").strip().lower() in _admin_emails()


def platform_admin(request: Request) -> Ctx:
    """Require signed-in user whose email is in HOOKTRACK_PLATFORM_ADMINS."""
    ctx = session_ctx(request)
    if ctx is None:
        raise HTTPException(401, "Not signed in")
    if not is_platform_admin_email(ctx.email):
        raise HTTPException(403, "Platform admin access required")
    return ctx


@router.get("/me")
def platform_me(request: Request):
    """Whether the current session is a platform admin (no 403 — used by UI gate)."""
    ctx = session_ctx(request)
    if ctx is None:
        return {"authenticated": False, "platform_admin": False}
    return {
        "authenticated": True,
        "platform_admin": is_platform_admin_email(ctx.email),
        "user": {"id": ctx.user_id, "email": ctx.email, "name": ctx.name},
        "admins_configured": bool(_admin_emails()),
    }


@router.get("/overview")
def overview(_ctx: Ctx = Depends(platform_admin)):
    users = db.one("SELECT COUNT(*) AS n FROM users")["n"]
    workspaces = db.one("SELECT COUNT(*) AS n FROM workspaces")["n"]
    accounts = db.one("SELECT COUNT(*) AS n FROM accounts")["n"]
    posts = db.one("SELECT COUNT(*) AS n FROM posts")["n"]
    views_rows = db.one("SELECT COUNT(*) AS n FROM daily_views")["n"]
    memberships = db.one("SELECT COUNT(*) AS n FROM memberships")["n"]
    recent_users = db.query(
        "SELECT id, email, name, created_at FROM users ORDER BY created_at DESC LIMIT 8"
    )
    recent_ws = db.query(
        "SELECT id, name, created_at FROM workspaces ORDER BY created_at DESC LIMIT 8"
    )
    for r in recent_users:
        if r.get("created_at") and hasattr(r["created_at"], "isoformat"):
            r["created_at"] = r["created_at"].isoformat()
    for r in recent_ws:
        if r.get("created_at") and hasattr(r["created_at"], "isoformat"):
            r["created_at"] = r["created_at"].isoformat()
    sched = sociavault.schedule_config()
    return {
        "counts": {
            "users": users,
            "workspaces": workspaces,
            "accounts": accounts,
            "posts": posts,
            "daily_view_rows": views_rows,
            "memberships": memberships,
        },
        "recent_users": recent_users,
        "recent_workspaces": recent_ws,
        "system": {
            "sociavault_schedule_enabled": sched.get("enabled"),
            "sociavault_interval_hours": sched.get("interval_hours"),
            "sociavault_mode": sched.get("mode"),
            "platform_admins": sorted(_admin_emails()),
            "has_server_ai_key": bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")),
            "has_server_sociavault_key": bool(sociavault.resolve_server_key()),
            "sociavault": sociavault.platform_key_status(),
        },
    }


@router.get("/workspaces")
def list_workspaces(q: str = "", limit: int = 100, _ctx: Ctx = Depends(platform_admin)):
    limit = max(1, min(limit, 500))
    if q.strip():
        like = f"%{q.strip()}%"
        rows = db.query(
            """SELECT w.id, w.name, w.created_at,
                      (SELECT COUNT(*) FROM memberships m WHERE m.workspace_id = w.id) AS members,
                      (SELECT COUNT(*) FROM accounts a WHERE a.workspace_id = w.id) AS accounts,
                      (SELECT COUNT(*) FROM posts p WHERE p.workspace_id = w.id) AS posts
               FROM workspaces w
               WHERE w.name ILIKE %s
               ORDER BY w.created_at DESC LIMIT %s""",
            (like, limit),
        )
    else:
        rows = db.query(
            """SELECT w.id, w.name, w.created_at,
                      (SELECT COUNT(*) FROM memberships m WHERE m.workspace_id = w.id) AS members,
                      (SELECT COUNT(*) FROM accounts a WHERE a.workspace_id = w.id) AS accounts,
                      (SELECT COUNT(*) FROM posts p WHERE p.workspace_id = w.id) AS posts
               FROM workspaces w
               ORDER BY w.created_at DESC LIMIT %s""",
            (limit,),
        )
    for r in rows:
        if r.get("created_at") and hasattr(r["created_at"], "isoformat"):
            r["created_at"] = r["created_at"].isoformat()
    return {"workspaces": rows}


@router.get("/workspaces/{wid}")
def workspace_detail(wid: int, _ctx: Ctx = Depends(platform_admin)):
    w = db.one("SELECT id, name, created_at FROM workspaces WHERE id=%s", (wid,))
    if not w:
        raise HTTPException(404, "Workspace not found")
    if w.get("created_at") and hasattr(w["created_at"], "isoformat"):
        w["created_at"] = w["created_at"].isoformat()
    members = db.query(
        """SELECT u.id, u.email, u.name, m.role, m.created_at
           FROM memberships m JOIN users u ON u.id = m.user_id
           WHERE m.workspace_id=%s ORDER BY m.role, u.email""",
        (wid,),
    )
    for m in members:
        if m.get("created_at") and hasattr(m["created_at"], "isoformat"):
            m["created_at"] = m["created_at"].isoformat()
    counts = {
        "accounts": db.one("SELECT COUNT(*) AS n FROM accounts WHERE workspace_id=%s", (wid,))["n"],
        "posts": db.one("SELECT COUNT(*) AS n FROM posts WHERE workspace_id=%s", (wid,))["n"],
        "phones": db.one("SELECT COUNT(*) AS n FROM phones WHERE workspace_id=%s", (wid,))["n"],
        "daily_views": db.one("SELECT COUNT(*) AS n FROM daily_views WHERE workspace_id=%s", (wid,))["n"],
    }
    return {"workspace": w, "members": members, "counts": counts}


@router.delete("/workspaces/{wid}")
def workspace_delete(wid: int, _ctx: Ctx = Depends(platform_admin)):
    """Permanently delete a workspace and cascaded tenant data."""
    w = db.one("SELECT id, name FROM workspaces WHERE id=%s", (wid,))
    if not w:
        raise HTTPException(404, "Workspace not found")
    # clear session pointers
    db.execute("UPDATE sessions SET workspace_id=NULL WHERE workspace_id=%s", (wid,))
    db.execute("DELETE FROM workspaces WHERE id=%s", (wid,))
    from .auth import purge_workspace_files
    purge_workspace_files(wid)
    return {"ok": True, "deleted": w["name"], "id": wid}


@router.get("/users")
def list_users(q: str = "", limit: int = 100, _ctx: Ctx = Depends(platform_admin)):
    limit = max(1, min(limit, 500))
    if q.strip():
        like = f"%{q.strip()}%"
        rows = db.query(
            """SELECT u.id, u.email, u.name, u.created_at,
                      (SELECT COUNT(*) FROM memberships m WHERE m.user_id = u.id) AS workspaces
               FROM users u
               WHERE u.email ILIKE %s OR u.name ILIKE %s
               ORDER BY u.created_at DESC LIMIT %s""",
            (like, like, limit),
        )
    else:
        rows = db.query(
            """SELECT u.id, u.email, u.name, u.created_at,
                      (SELECT COUNT(*) FROM memberships m WHERE m.user_id = u.id) AS workspaces
               FROM users u
               ORDER BY u.created_at DESC LIMIT %s""",
            (limit,),
        )
    for r in rows:
        if r.get("created_at") and hasattr(r["created_at"], "isoformat"):
            r["created_at"] = r["created_at"].isoformat()
        r["platform_admin"] = is_platform_admin_email(r["email"])
    return {"users": rows}


@router.get("/users/{uid}")
def user_detail(uid: int, _ctx: Ctx = Depends(platform_admin)):
    u = db.one("SELECT id, email, name, created_at FROM users WHERE id=%s", (uid,))
    if not u:
        raise HTTPException(404, "User not found")
    if u.get("created_at") and hasattr(u["created_at"], "isoformat"):
        u["created_at"] = u["created_at"].isoformat()
    memberships = db.query(
        """SELECT w.id, w.name, m.role, m.created_at
           FROM memberships m JOIN workspaces w ON w.id = m.workspace_id
           WHERE m.user_id=%s ORDER BY w.name""",
        (uid,),
    )
    for m in memberships:
        if m.get("created_at") and hasattr(m["created_at"], "isoformat"):
            m["created_at"] = m["created_at"].isoformat()
    return {
        "user": u,
        "platform_admin": is_platform_admin_email(u["email"]),
        "memberships": memberships,
    }


class RenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.patch("/workspaces/{wid}")
def workspace_rename(wid: int, b: RenameIn, _ctx: Ctx = Depends(platform_admin)):
    row = db.one(
        "UPDATE workspaces SET name=%s WHERE id=%s RETURNING id, name",
        (b.name.strip(), wid),
    )
    if not row:
        raise HTTPException(404, "Workspace not found")
    return row



class SociaVaultPlatformIn(BaseModel):
    api_key: Optional[str] = None
    clear_key: bool = False
    enabled: Optional[bool] = None
    interval_hours: Optional[float] = Field(None, ge=0, le=168)
    days_back: Optional[int] = Field(None, ge=1, le=30)
    mode: Optional[str] = None  # light | full


@router.get("/sociavault")
def sociavault_get(_ctx: Ctx = Depends(platform_admin)):
    """System-wide SociaVault key + sync schedule (for admin UI)."""
    st = sociavault.schedule_status()
    return {
        "key": sociavault.platform_key_status(),
        "schedule": sociavault.schedule_config(),
        "last_run": {
            "started_at": st.get("started_at"),
            "finished_at": st.get("finished_at"),
            "ok": st.get("ok"),
            "running": st.get("running"),
            "summary": st.get("summary"),
        },
    }


@router.put("/sociavault")
def sociavault_put(b: SociaVaultPlatformIn, _ctx: Ctx = Depends(platform_admin)):
    out = {}
    if b.clear_key or (b.api_key is not None and b.api_key.strip()):
        out["key"] = sociavault.save_platform_key(b.api_key or "", clear=b.clear_key)
    else:
        out["key"] = sociavault.platform_key_status()
    if any(x is not None for x in (b.enabled, b.interval_hours, b.days_back, b.mode)):
        out["schedule"] = sociavault.save_schedule_settings(
            enabled=b.enabled,
            interval_hours=b.interval_hours,
            days_back=b.days_back,
            mode=b.mode,
        )
    else:
        out["schedule"] = sociavault.schedule_config()
    return out


@router.post("/sociavault/test")
def sociavault_test(_ctx: Ctx = Depends(platform_admin)):
    key = sociavault.resolve_server_key()
    if not key:
        raise HTTPException(400, "No system-wide SociaVault API key configured")
    try:
        msg = sociavault.test_connection(key)
        return {"ok": True, "message": msg}
    except sociavault.SociaVaultError as e:
        raise HTTPException(e.status or 400, str(e))


@router.post("/actions/run-sync")
def run_sync(force: bool = False, _ctx: Ctx = Depends(platform_admin)):
    """Trigger SociaVault scheduled light sync across workspaces."""
    try:
        summary = sociavault.run_scheduled_sync(force)
        return {"ok": True, "summary": summary}
    except Exception as e:
        raise HTTPException(500, str(e)[:300])


@router.post("/actions/process-schedules")
def run_schedules(_ctx: Ctx = Depends(platform_admin)):
    from . import publish, video
    n_pub = publish.process_due_schedules(50)
    n_vid = video.poll_running_jobs(50)
    return {"ok": True, "published": n_pub, "video_polled": n_vid}
