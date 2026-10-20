import asyncio
import logging
import os
import re
import tempfile
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi.middleware.gzip import GZipMiddleware
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import activation, ai, auth, baseline, billing, cache, crypto, db, demo, legal, logic, media, publish, seo, sociavault, vaultwatch
from . import backup, digest, reports
from .auth import Ctx, admin, current
from .import_xlsx import import_workbook

import mimetypes

# Python's MIME table depends on the machine: without this a .webp can be served as application/octet-stream
mimetypes.add_type("image/webp", ".webp")
STATIC = Path(__file__).parent.parent / "static"
UPLOADS = Path(__file__).parent.parent / "uploads"
ALLOWED_IMAGES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MAX_UPLOAD = 8 * 1024 * 1024


log = logging.getLogger("hooktrack")


async def _sync_scheduler_loop():
    """Every worker runs this loop, but the run itself needs a database lease and the schedule is
    judged by the *last run anywhere* (not this process's start time), so N workers = one sync."""
    await asyncio.sleep(30)    # let the app finish booting
    while True:
        try:
            if await asyncio.to_thread(sociavault.scheduled_run_due):
                summary = await asyncio.to_thread(sociavault.run_scheduled_sync, False)
                if summary.get("ok"):
                    log.info(
                        "scheduled SociaVault sync: synced=%s failed=%s skipped=%s credits~%s",
                        summary.get("synced"), summary.get("failed"),
                        summary.get("skipped_fresh"), summary.get("credits_est"),
                    )
        except Exception:
            log.exception("scheduled SociaVault sync failed")
        try:
            await asyncio.to_thread(vaultwatch.tick)     # balance + failure watch (lease: one worker at a time)
        except Exception:
            log.exception("balance watch failed")
        await asyncio.sleep(300)


async def _publish_loop():
    await asyncio.sleep(15)
    while True:
        try:
            rec = await asyncio.to_thread(publish.recover_stuck_jobs)
            if any(rec.values()):
                log.warning("recovered stuck publish jobs: %s", rec)
        except Exception:
            log.exception("publish recovery failed")
        try:
            n = await asyncio.to_thread(publish.process_due_schedules, 20)
            if n:
                log.info("published %s due scheduled posts", n)
        except Exception:
            log.exception("scheduled publish failed")
        try:
            got = await asyncio.to_thread(publish.sync_posted_links, 20)
            if got.get("linked"):
                log.info("filled %s posted video links from Late", got["linked"])
        except Exception:
            log.exception("posted link sync failed")
        await asyncio.sleep(45)


async def _renewal_loop():
    """Every worker runs this; billing.run_renewals takes a database lease, so N workers = one run."""
    await asyncio.sleep(60)
    while True:
        try:
            await asyncio.to_thread(billing.run_renewals)
            await asyncio.to_thread(billing.run_addon_renewals)
        except Exception:
            log.exception("subscription renewals failed")
        await asyncio.sleep(1800)


async def _digest_loop():
    """Hourly. digest.run_due claims each workspace atomically, so N workers send each digest once."""
    await asyncio.sleep(90)
    while True:
        try:
            r = await asyncio.to_thread(digest.run_due)
            if r.get("sent"):
                log.info("weekly digest: %s workspaces, %s emails", r["workspaces"], r["sent"])
        except Exception:
            log.exception("weekly digest failed")
        await asyncio.sleep(3600)


async def _backup_loop():
    """Every worker runs this; backup.run_due takes a database lease and goes by stored timestamps, so N workers
    make one backup per interval and one restore test per week."""
    await asyncio.sleep(120)
    while True:
        try:
            r = await asyncio.to_thread(backup.run_due)
            if r.get("backup"):
                log.info("database backup: %s (%s bytes)", r["backup"]["file"], r["backup"]["bytes"])
            if r.get("drill"):
                log.info("restore drill passed: %s rows in %ss", r["drill"]["rows"], r["drill"]["seconds"])
        except Exception:
            log.exception("backup loop failed")
        await asyncio.sleep(1800)


async def _maintenance_loop():
    """Hourly: purge expired sessions, invites, reset tokens and old rate-limit rows."""
    while True:
        try:
            gone = await asyncio.to_thread(auth.cleanup_expired)
            if any(gone.values()):
                log.info("cleanup removed %s", gone)
        except Exception:
            log.exception("cleanup failed")
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(_app):
    db.init_schema()
    log.info("cache backend: %s", cache.status()["backend"])
    tasks = [asyncio.create_task(_maintenance_loop())]
    tasks.append(asyncio.create_task(_sync_scheduler_loop()))   # checks the (runtime-editable) schedule each tick
    cfg = sociavault.schedule_config()
    log.info("SociaVault scheduler %s (every %sh, mode=%s)", "on" if cfg["enabled"] else "off",
             cfg["interval_hours"], cfg["mode"])
    tasks.append(asyncio.create_task(_publish_loop()))
    tasks.append(asyncio.create_task(_renewal_loop()))
    tasks.append(asyncio.create_task(_digest_loop()))
    tasks.append(asyncio.create_task(_backup_loop()))
    yield
    for task in tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="HookTrack", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)   # app.js is ~200 KB; no streaming responses exist


@app.middleware("http")
async def invalidate_cache_on_write(request: Request, call_next):
    """Any API write that went through (or may have half gone through) makes this workspace's cached reads stale."""
    resp = await call_next(request)
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/"):
        ws = getattr(request.state, "ws", None)
        if ws and (resp.status_code < 400 or resp.status_code >= 500):
            await asyncio.to_thread(cache.bump, ws)
    return resp


@app.middleware("http")
async def revalidate_static(request: Request, call_next):
    """The app's own JS/CSS/HTML must be re-checked on every load (a cheap 304 when unchanged). Without this a browser
    keeps running an old app.js after an update, because StaticFiles sets no Cache-Control and browsers then guess."""
    resp = await call_next(request)
    path = request.url.path
    if not path.startswith(("/api/", "/media/", "/uploads/")) and "cache-control" not in resp.headers:
        ctype = resp.headers.get("content-type", "")
        if ctype.startswith(("text/html", "text/css", "application/javascript", "text/javascript")):
            resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.middleware("http")
async def trial_paywall(request: Request, call_next):
    """Once billing is on, a workspace with no trial and no paid plan can look but not change anything."""
    path = request.url.path
    if (request.method in ("POST", "PUT", "PATCH", "DELETE") and path.startswith("/api/")
            and not path.startswith(("/api/auth/", "/api/billing"))
            and path not in ("/api/workspaces", "/api/workspace")):      # create / rename / delete a workspace
        try:
            ctx = await asyncio.to_thread(auth.session_ctx, request)
            if ctx and ctx.workspace_id and await asyncio.to_thread(billing.access_blocked, ctx.workspace_id):
                return JSONResponse({"detail": await asyncio.to_thread(billing.blocked_message, ctx.workspace_id),
                                     "code": "trial_ended"}, status_code=402)
        except Exception:
            log.exception("paywall check failed (letting the request through)")
    return await call_next(request)
app.include_router(auth.router)
app.include_router(billing.router)
app.include_router(baseline.router)
app.include_router(media.router)
app.include_router(reports.router)
app.include_router(reports.pages)


@app.middleware("http")
async def same_origin_writes(request: Request, call_next):
    """Cookie auth + browsers: refuse state-changing API calls that originate from another site."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        if origin:
            allowed = {request.headers.get("host", "")} | {
                h.strip() for h in os.environ.get("HOOKTRACK_ALLOWED_ORIGINS", "").split(",") if h.strip()}
            if urlparse(origin).netloc not in allowed:
                return JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)
    return await call_next(request)


# ---------- helpers (every query below is scoped by workspace_id) ----------

def _update(table: str, id_: int, ws: int, fields: dict, allowed: set):
    data = {k: v for k, v in fields.items() if k in allowed}
    if not data:
        raise HTTPException(400, "Nothing to update")
    sets = ", ".join(f"{k} = %s" for k in data)
    row = db.one(f"UPDATE {table} SET {sets} WHERE id = %s AND workspace_id = %s RETURNING *",
                 (*data.values(), id_, ws))
    if row is None:
        raise HTTPException(404, "Not found")
    return row


def _delete(table: str, id_: int, ws: int):
    if not db.execute(f"DELETE FROM {table} WHERE id = %s AND workspace_id = %s", (id_, ws)):
        raise HTTPException(404, "Not found")
    return {"ok": True}


def _own_account(ws: int, account_id: Optional[int]):
    if account_id is not None and not db.one("SELECT 1 FROM accounts WHERE id=%s AND workspace_id=%s", (account_id, ws)):
        raise HTTPException(400, "Unknown account")


def _own_client(ws: int, client_id: Optional[int]):
    if client_id is not None and not db.one("SELECT 1 FROM clients WHERE id=%s AND workspace_id=%s", (client_id, ws)):
        raise HTTPException(400, "Unknown client")


def _own_phone(ws: int, phone_id: Optional[int]):
    if phone_id is not None and not db.one("SELECT 1 FROM phones WHERE id=%s AND workspace_id=%s", (phone_id, ws)):
        raise HTTPException(400, "Unknown phone")


def _ai_guard(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ai.AIUnavailable as e:
        raise HTTPException(503, str(e))
    except HTTPException:
        raise
    except ValueError as e:   # e.g. a custom endpoint that now resolves to a blocked address
        raise HTTPException(400, str(e))
    except Exception as e:  # model/network failure: report, don't 500 with a stack
        raise HTTPException(502, f"AI request failed: {_scrub(e)}")


def _ai_metered(ws: int, fn, *a, **kw):
    """_ai_guard plus the workspace's monthly AI allowance (no-op while billing is off). Only calls that
    succeed are counted, so a provider outage does not eat a customer's allowance."""
    billing.check_ai(ws)
    out = _ai_guard(fn, *a, **kw)
    billing.record_ai(ws)
    return out


def _scrub(e: Exception, secret: str = "") -> str:
    text = str(e).replace(secret, "…") if secret else str(e)
    return re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "sk-…", text)[:300]


def _post_out(p: dict, st: dict) -> dict:
    p["has_video_file"] = bool(p.pop("video_file", ""))
    p["vsc"] = logic.vsc_total(p.get("v"), p.get("s"), p.get("c"))
    p["decision"] = logic.post_decision(p["vsc"], st)
    # live decision always uses current thresholds; snapshot has historical
    if p.get("created_at") and hasattr(p["created_at"], "isoformat"):
        p["created_at"] = p["created_at"].isoformat()
    return p


POST_SELECT = (
    "SELECT p.*, a.handle, ph.color AS phone_color FROM posts p "
    "LEFT JOIN accounts a ON a.id = p.account_id LEFT JOIN phones ph ON ph.id = a.phone_id "
)


def _get_post(ws: int, post_id: int) -> dict:
    row = db.one(POST_SELECT + "WHERE p.id = %s AND p.workspace_id = %s", (post_id, ws))
    if row is None:
        raise HTTPException(404, "Post not found")
    return _post_out(row, logic.get_settings(ws))


# ---------- phones ----------

class PhoneIn(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    color: str = Field("#6366f1", pattern=r"^#[0-9a-fA-F]{6}$")
    position: int = 0


class PhonePatch(BaseModel):
    label: Optional[str] = Field(None, min_length=1, max_length=60)
    color: Optional[str] = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    position: Optional[int] = None


@app.get("/api/phones")
def phones(ctx: Ctx = Depends(current)):
    return db.query("SELECT * FROM phones WHERE workspace_id=%s ORDER BY position, id", (ctx.workspace_id,))


@app.post("/api/phones")
def phone_create(b: PhoneIn, ctx: Ctx = Depends(current)):
    return db.one("INSERT INTO phones(workspace_id, label, color, position) VALUES (%s,%s,%s,%s) RETURNING *",
                  (ctx.workspace_id, b.label, b.color, b.position))


@app.patch("/api/phones/{pid}")
def phone_update(pid: int, b: PhonePatch, ctx: Ctx = Depends(current)):
    return _update("phones", pid, ctx.workspace_id, b.model_dump(exclude_unset=True), {"label", "color", "position"})


@app.delete("/api/phones/{pid}")
def phone_delete(pid: int, ctx: Ctx = Depends(current)):
    return _delete("phones", pid, ctx.workspace_id)


# ---------- accounts ----------

STATUSES = {"active", "warming", "paused", "killed"}


class AccountIn(BaseModel):
    handle: str = Field(min_length=1, max_length=80)
    phone_id: Optional[int] = None
    platform: str = Field("TikTok", max_length=40)
    niche: str = Field("", max_length=120)
    status: str = "active"
    times_per_day: int = Field(1, ge=0, le=50)
    content_hook: str = Field("", max_length=200)
    model: str = Field("", max_length=80)
    sync_enabled: bool = True
    client_id: Optional[int] = None


class AccountPatch(BaseModel):
    handle: Optional[str] = Field(None, min_length=1, max_length=80)
    phone_id: Optional[int] = None
    platform: Optional[str] = Field(None, max_length=40)
    niche: Optional[str] = Field(None, max_length=120)
    status: Optional[str] = None
    times_per_day: Optional[int] = Field(None, ge=0, le=50)
    content_hook: Optional[str] = Field(None, max_length=200)
    model: Optional[str] = Field(None, max_length=80)
    sync_enabled: Optional[bool] = None
    client_id: Optional[int] = None


ACCOUNT_FIELDS = {
    "handle", "phone_id", "platform", "niche", "status", "times_per_day",
    "content_hook", "model", "sync_enabled", "client_id",
}


@app.get("/api/accounts")
def accounts(ctx: Ctx = Depends(current)):
    return db.query(
        """SELECT a.*, p.label AS phone_label, p.color AS phone_color,
                  f.followers, f.day AS followers_day, f.video_count,
                  CASE WHEN f.followers IS NOT NULL AND o.followers IS NOT NULL THEN f.followers - o.followers END
                      AS followers_change, o.day AS followers_since
           FROM accounts a
           LEFT JOIN phones p ON p.id = a.phone_id
           LEFT JOIN LATERAL (SELECT followers, day, video_count FROM account_snapshots s
                              WHERE s.account_id = a.id AND followers IS NOT NULL ORDER BY day DESC LIMIT 1) f ON TRUE
           LEFT JOIN LATERAL (SELECT followers, day FROM account_snapshots s
                              WHERE s.account_id = a.id AND followers IS NOT NULL AND day <= f.day - 6
                              ORDER BY day DESC LIMIT 1) o ON TRUE
           WHERE a.workspace_id = %s
           ORDER BY p.position NULLS LAST, a.handle""", (ctx.workspace_id,))


def _clean_handle(handle: str, platform: str) -> tuple[str, str]:
    """Accept a handle, an @handle or a profile/video URL; return the canonical '@handle' and platform name."""
    h, p = sociavault.normalize_handle(handle, platform)
    if not h:
        raise HTTPException(400, "Could not find an account name in that. Enter a handle like @name, "
                                 "or a profile link such as tiktok.com/@name.")
    return h, sociavault.canonical_platform(p)


class LookupIn(BaseModel):
    handle: str = Field(min_length=1, max_length=300)
    platform: str = Field("", max_length=40)


@app.post("/api/accounts/lookup")
def account_lookup(b: LookupIn, ctx: Ctx = Depends(current)):
    """Confirm a handle is a real account (and show who it is) before tracking it. Costs 1 SociaVault credit."""
    handle, platform = _clean_handle(b.handle, b.platform)
    try:
        out = sociavault.lookup_profile(ctx.workspace_id, platform, handle)
    except sociavault.SociaVaultError as e:
        raise HTTPException(e.status if e.status in (401, 403) else 400, str(e))
    out["niche"] = ""
    if out.get("found") and out.get("bio"):     # a suggestion only, and only from a bio (a name alone says nothing); never blocks the lookup
        try:
            cfg = ai.resolve(ctx.workspace_id)
            if cfg.available:
                out["niche"] = _ai_metered(ctx.workspace_id, ai.suggest_niche, cfg, out.get("display_name") or "", out.get("bio") or "")
        except Exception:
            out["niche"] = ""
    return out


@app.post("/api/accounts")
def account_create(b: AccountIn, ctx: Ctx = Depends(current)):
    ws = ctx.workspace_id
    if b.status not in STATUSES:
        raise HTTPException(400, "Invalid status")
    b.handle, b.platform = _clean_handle(b.handle, b.platform)
    billing.require_capacity(ws, "accounts")
    _own_phone(ws, b.phone_id)
    _own_client(ws, b.client_id)
    if db.one("SELECT 1 FROM accounts WHERE workspace_id=%s AND handle=%s", (ws, b.handle)):
        raise HTTPException(409, "That handle already exists")
    cols = list(ACCOUNT_FIELDS)
    d = b.model_dump()
    row = db.one(
        f"INSERT INTO accounts(workspace_id, {','.join(cols)}) VALUES (%s, {','.join(['%s'] * len(cols))}) RETURNING *",
        [ws] + [d[c] for c in cols])
    activation.mark(ws, "account_added")
    return row


@app.patch("/api/accounts/{aid}")
def account_update(aid: int, b: AccountPatch, ctx: Ctx = Depends(current)):
    d = b.model_dump(exclude_unset=True)
    if "status" in d and d["status"] not in STATUSES:
        raise HTTPException(400, "Invalid status")
    if "handle" in d or "platform" in d:
        cur = db.one("SELECT handle, platform FROM accounts WHERE id=%s AND workspace_id=%s", (aid, ctx.workspace_id))
        if cur is None:
            raise HTTPException(404, "Not found")
        d["handle"], d["platform"] = _clean_handle(d.get("handle", cur["handle"]), d.get("platform", cur["platform"]))
    if d.get("phone_id") is not None:
        _own_phone(ctx.workspace_id, d["phone_id"])
    _own_client(ctx.workspace_id, d.get("client_id"))
    if "handle" in d and db.one("SELECT 1 FROM accounts WHERE workspace_id=%s AND handle=%s AND id<>%s",
                                (ctx.workspace_id, d["handle"], aid)):
        raise HTTPException(409, "That handle already exists")
    return _update("accounts", aid, ctx.workspace_id, d, ACCOUNT_FIELDS)


@app.delete("/api/accounts/{aid}")
def account_delete(aid: int, ctx: Ctx = Depends(current)):
    return _delete("accounts", aid, ctx.workspace_id)


# ---------- daily views ----------

class ViewEntry(BaseModel):
    account_id: int
    day: date
    views: Optional[int] = Field(None, ge=0)   # null clears the cell
    source: str = "manual"


class ViewsIn(BaseModel):
    entries: list[ViewEntry] = Field(max_length=2000)


@app.get("/api/views")
def views(start: Optional[date] = None, days: int = 7, ctx: Ctx = Depends(current)):
    if not 1 <= days <= 31:
        raise HTTPException(400, "days must be 1-31")
    return logic.week_grid(ctx.workspace_id, start or logic.monday(logic.today(ctx.workspace_id)), days)


@app.put("/api/views")
def views_put(b: ViewsIn, ctx: Ctx = Depends(current)):
    ws = ctx.workspace_id
    known = {r["id"] for r in db.query("SELECT id FROM accounts WHERE workspace_id=%s", (ws,))}
    for e in b.entries:   # validate everything before writing anything
        if e.account_id not in known:
            raise HTTPException(400, f"Unknown account {e.account_id}")
        if e.source not in ("manual", "ai"):
            raise HTTPException(400, "Invalid source")
    for e in b.entries:
        if e.views is None:
            db.execute("DELETE FROM daily_views WHERE account_id=%s AND day=%s AND workspace_id=%s",
                       (e.account_id, e.day, ws))
        else:
            db.execute(
                "INSERT INTO daily_views(account_id, workspace_id, day, views, source) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (account_id, day) DO UPDATE SET views=EXCLUDED.views, "
                "source=EXCLUDED.source, updated_at=now()",
                (e.account_id, ws, e.day, e.views, e.source))
    return {"saved": len(b.entries)}


# ---------- posts / calendar ----------

# Pipeline: idea → scripted → filmed → posted → analysed
# "planned" kept as alias of "scripted" for older data / imports
POST_STATUSES = {"idea", "scripted", "filmed", "posted", "analysed", "planned"}
PIPELINE_ORDER = ["idea", "scripted", "filmed", "posted", "analysed"]
STATUS_ALIASES = {"planned": "scripted"}  # normalize on write
POST_FIELDS = {"account_id", "post_date", "status", "ai_prompt", "hook", "body", "caption",
               "views", "comments", "intent", "v", "s", "c", "score_notes",
               "score_confidence", "score_reasoning", "parent_id", "variant_label", "experiment_id", "video_url", "url"}


class PostIn(BaseModel):
    account_id: Optional[int] = None
    post_date: date
    status: str = "scripted"
    ai_prompt: str = Field("", max_length=500)
    hook: str = Field("", max_length=500)
    body: str = Field("", max_length=1000)
    caption: str = Field("", max_length=2200)
    views: int = Field(0, ge=0)
    comments: int = Field(0, ge=0)
    intent: bool = False
    v: Optional[int] = Field(None, ge=0, le=10)
    s: Optional[int] = Field(None, ge=0, le=10)
    c: Optional[int] = Field(None, ge=0, le=10)


class PostPatch(BaseModel):
    account_id: Optional[int] = None
    post_date: Optional[date] = None
    status: Optional[str] = None
    ai_prompt: Optional[str] = Field(None, max_length=500)
    hook: Optional[str] = Field(None, max_length=500)
    body: Optional[str] = Field(None, max_length=1000)
    caption: Optional[str] = Field(None, max_length=2200)
    views: Optional[int] = Field(None, ge=0)
    comments: Optional[int] = Field(None, ge=0)
    intent: Optional[bool] = None
    v: Optional[int] = Field(None, ge=0, le=10)
    s: Optional[int] = Field(None, ge=0, le=10)
    c: Optional[int] = Field(None, ge=0, le=10)
    score_notes: Optional[str] = Field(None, max_length=2000)
    score_confidence: Optional[float] = Field(None, ge=0, le=1)
    score_reasoning: Optional[str] = Field(None, max_length=4000)
    parent_id: Optional[int] = None
    variant_label: Optional[str] = Field(None, max_length=40)
    experiment_id: Optional[int] = None
    url: Optional[str] = Field(None, max_length=500)       # the address of the video this card became once posted


def _normalize_status(s: str) -> str:
    s = STATUS_ALIASES.get((s or "scripted").strip().lower(), (s or "scripted").strip().lower())
    if s not in PIPELINE_ORDER:
        raise HTTPException(400, f"Invalid status (use: {', '.join(PIPELINE_ORDER)})")
    return s


def _insert_post(ws: int, b: PostIn) -> int:
    b.status = _normalize_status(b.status)
    _own_account(ws, b.account_id)
    scored = None not in (b.v, b.s, b.c)
    row = db.one(
        "INSERT INTO posts(workspace_id, account_id, post_date, status, ai_prompt, hook, body, caption, views, "
        "comments, intent, v, s, c, score_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (ws, b.account_id, b.post_date, b.status, b.ai_prompt, b.hook, b.body, b.caption, b.views,
         b.comments, b.intent, b.v, b.s, b.c, "manual" if scored else ""))
    return row["id"]



# ---------- search + bulk stage (P4) ----------

@app.get("/api/search")
def global_search(q: str = "", limit: int = 20, ctx: Ctx = Depends(current)):
    """Lightweight search across posts and accounts for the command palette."""
    q = (q or "").strip()
    if len(q) < 1:
        return {"posts": [], "accounts": [], "pages": _palette_pages()}
    ws = ctx.workspace_id
    like = f"%{q}%"
    posts = db.query(
        """SELECT p.id, p.hook, p.status, p.post_date, a.handle
           FROM posts p LEFT JOIN accounts a ON a.id = p.account_id
           WHERE p.workspace_id=%s AND (p.hook ILIKE %s OR p.body ILIKE %s OR p.caption ILIKE %s OR a.handle ILIKE %s)
           ORDER BY p.post_date DESC LIMIT %s""",
        (ws, like, like, like, like, min(limit, 30)),
    )
    accounts = db.query(
        """SELECT id, handle, platform, niche, status FROM accounts
           WHERE workspace_id=%s AND (handle ILIKE %s OR niche ILIKE %s OR platform ILIKE %s)
           ORDER BY handle LIMIT %s""",
        (ws, like, like, like, min(limit, 15)),
    )
    return {"posts": posts, "accounts": accounts, "pages": _palette_pages(q)}


def _palette_pages(q: str = ""):
    pages = [
        {"id": "dashboard", "label": "Home / Dashboard", "href": "#/dashboard"},
        {"id": "today", "label": "Today (quick entry)", "href": "#/today"},
        {"id": "views", "label": "Views", "href": "#/views"},
        {"id": "hooks", "label": "Hooks", "href": "#/hooks"},
        {"id": "pipeline", "label": "Posts (board)", "href": "#/pipeline"},
        {"id": "accounts", "label": "Accounts", "href": "#/accounts"},
        {"id": "settings", "label": "Settings", "href": "#/settings"},
        {"id": "inspiration", "label": "Inspiration", "href": "#/inspiration"},
        {"id": "scheduled", "label": "Scheduled publishes", "href": "#/scheduled"},
    ]
    if not q:
        return pages
    ql = q.lower()
    return [p for p in pages if ql in p["label"].lower() or ql in p["id"]]


class BulkStageIn(BaseModel):
    post_ids: list[int] = Field(min_length=1, max_length=100)
    stage: str


@app.patch("/api/posts/bulk-stage")
def posts_bulk_stage(b: BulkStageIn, ctx: Ctx = Depends(current)):
    stage = _normalize_status(b.stage)
    if stage not in POST_STATUSES:
        raise HTTPException(400, f"Invalid stage")
    updated = 0
    for pid in b.post_ids:
        row = db.one(
            "UPDATE posts SET status=%s WHERE id=%s AND workspace_id=%s RETURNING id",
            (stage, pid, ctx.workspace_id),
        )
        if row:
            updated += 1
            if stage != "idea":
                _promote_tweak(ctx.workspace_id, pid)
    return {"updated": updated, "stage": stage}


@app.get("/api/today")
def today_screen(day: Optional[date] = None, ctx: Ctx = Depends(current)):
    """Everything the Today screen needs: which accounts still need views for `day`, and which
    posts are due. The browser passes its own local date as `day` (the server has no time zone)."""
    ws = ctx.workspace_id
    tz = logic.workspace_tz(ws)
    day = day or logic.today_in(tz)
    accounts_ = db.query(
        "SELECT a.id, a.handle, a.platform, a.niche, a.status, a.sync_enabled, p.label AS phone_label, p.color AS phone_color "
        "FROM accounts a LEFT JOIN phones p ON p.id = a.phone_id "
        "WHERE a.workspace_id = %s AND a.status IN ('active', 'warming') "
        "ORDER BY p.position NULLS LAST, p.id NULLS LAST, a.handle", (ws,))
    cells = {}
    for r in db.query(
            "SELECT account_id, day, views, source FROM daily_views "
            "WHERE workspace_id = %s AND day BETWEEN %s AND %s", (ws, day - timedelta(days=8), day)):
        cells[(r["account_id"], r["day"])] = r
    rows, entered = [], 0
    for a in accounts_:
        cur = cells.get((a["id"], day))
        prev = cells.get((a["id"], day - timedelta(days=1)))
        hist = [cells[(a["id"], day - timedelta(days=i))]["views"] for i in range(1, 8)
                if (a["id"], day - timedelta(days=i)) in cells]
        entered += cur is not None
        rows.append({**a, "views": cur["views"] if cur else None, "source": cur["source"] if cur else None,
                     "prev": prev["views"] if prev else None,
                     "avg7": round(sum(hist) / len(hist)) if hist else None})
    gaps = []
    for back in (1, 2, 3):
        d = day - timedelta(days=back)
        n = db.one(
            "SELECT COUNT(*) AS n FROM accounts a WHERE a.workspace_id = %s AND a.status IN ('active', 'warming') "
            "AND (a.created_at AT TIME ZONE %s)::date <= %s AND NOT EXISTS (SELECT 1 FROM daily_views v WHERE v.account_id = a.id AND v.day = %s)",
            (ws, tz, d, d))["n"]
        if n:
            gaps.append({"day": d.isoformat(), "missing": n})
    st = logic.get_settings(ws)
    due = db.query(
        "SELECT p.*, a.handle FROM posts p LEFT JOIN accounts a ON a.id = p.account_id "
        "WHERE p.workspace_id = %s AND p.status IN ('idea', 'scripted', 'planned', 'filmed') "
        "AND p.post_date BETWEEN %s AND %s ORDER BY p.post_date, p.id LIMIT 50",
        (ws, day - timedelta(days=14), day))
    due_out = [{**_post_out(p, st), "handle": p["handle"], "overdue": p["post_date"] < day} for p in due]
    return {"day": day.isoformat(), "accounts": rows, "entered": entered, "total": len(rows),
            "gaps": gaps, "due": due_out}


@app.get("/api/workspace/summary")
def workspace_week_summary(ctx: Ctx = Depends(current)):
    """Plain-text weekly summary for copy/export."""
    d = logic.dashboard(ctx.workspace_id)
    lines = [
        f"HookTrack weekly summary — week of {d['week_start']}",
        f"Views this week: {d['week_views']:,}",
        f"Accounts: {d['accounts']} · Avg/account: {d['avg_per_account']:,}",
        f"Scale: {d['decisions'].get('SCALE', 0)} · Stop: {d['decisions'].get('KILL', 0)} · Hold: {d['decisions'].get('HOLD', 0)}",
        "",
    ]
    if d.get("act_on"):
        lines.append("Act on:")
        for a in d["act_on"]:
            lines.append(f"  • {a['handle']}: {a['decision']} — {a.get('reason') or ''}")
        lines.append("")
    if d.get("top_hooks"):
        lines.append("Top hooks:")
        for h in d["top_hooks"][:5]:
            vsc = h.get("vsc")
            extra = f", score {vsc}/30" if vsc is not None else ""
            lines.append(f"  • {h.get('handle')}: {(h.get('hook') or '')[:80]} ({h.get('views',0):,} views{extra})")
    if d.get("unscored_posts"):
        lines.append("")
        lines.append(f"Note: {d['unscored_posts']} posted hooks still need scoring.")
    text = "\n".join(lines)
    return {"week_start": d["week_start"], "text": text, "dashboard": {
        "week_views": d["week_views"], "accounts": d["accounts"], "decisions": d["decisions"],
    }}



# ---------- P5: duplicate, export, activity, scheduled count ----------

@app.post("/api/posts/{pid}/duplicate")
def post_duplicate(pid: int, ctx: Ctx = Depends(current)):
    """Clone a post (hook/body/caption) as a new idea for today."""
    ws = ctx.workspace_id
    src = db.one("SELECT * FROM posts WHERE id=%s AND workspace_id=%s", (pid, ws))
    if not src:
        raise HTTPException(404, "Post not found")
    row = db.one(
        """INSERT INTO posts
           (workspace_id, account_id, post_date, status, ai_prompt, hook, body, caption, views, comments, intent, score_by)
           VALUES (%s,%s,%s,'idea',%s,%s,%s,%s,0,0,false,'')
           RETURNING id""",
        (
            ws,
            src["account_id"],
            logic.today(ws),
            src.get("ai_prompt") or "",
            src.get("hook") or "",
            src.get("body") or "",
            src.get("caption") or "",
        ),
    )
    return _get_post(ws, row["id"])


@app.get("/api/export/views.csv")
def export_views_csv(days: int = 90, ctx: Ctx = Depends(current)):
    from fastapi.responses import PlainTextResponse
    ws = ctx.workspace_id
    days = max(1, min(days, 730))
    start = logic.today(ws) - timedelta(days=days)
    rows = db.query(
        """SELECT a.handle, a.platform, v.day, v.views, v.source
           FROM daily_views v
           JOIN accounts a ON a.id = v.account_id
           WHERE v.workspace_id=%s AND v.day >= %s
           ORDER BY v.day DESC, a.handle""",
        (ws, start),
    )
    lines = ["handle,platform,day,views,source"]
    for r in rows:
        handle = (r["handle"] or "").replace('"', '""')
        lines.append(f'"{handle}",{r["platform"]},{r["day"]},{r["views"]},{r.get("source") or ""}')
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="hooktrack-views.csv"'
    })


@app.get("/api/export/posts.csv")
def export_posts_csv(ctx: Ctx = Depends(current)):
    from fastapi.responses import PlainTextResponse
    ws = ctx.workspace_id
    rows = db.query(
        """SELECT a.handle, p.post_date, p.status, p.hook, p.body, p.caption, p.views, p.comments, p.v, p.s, p.c
           FROM posts p LEFT JOIN accounts a ON a.id = p.account_id
           WHERE p.workspace_id=%s
           ORDER BY p.post_date DESC, p.id DESC
           LIMIT 5000""",
        (ws,),
    )
    def csv_cell(x):
        s = "" if x is None else str(x)
        return '"' + s.replace('"', '""').replace("\n", " ") + '"'
    lines = ["handle,post_date,status,hook,body,caption,views,comments,v,s,c,hook_score"]
    for r in rows:
        v, s, c = r.get("v"), r.get("s"), r.get("c")
        total = "" if v is None else (v + (s or 0) + (c or 0))
        lines.append(",".join([
            csv_cell(r.get("handle")), str(r.get("post_date") or ""),
            csv_cell(r.get("status")), csv_cell(r.get("hook")), csv_cell(r.get("body")),
            csv_cell(r.get("caption")), str(r.get("views") or 0), str(r.get("comments") or 0),
            str(v if v is not None else ""), str(s if s is not None else ""), str(c if c is not None else ""),
            str(total),
        ]))
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="hooktrack-posts.csv"'
    })


@app.get("/api/activity")
def recent_activity(limit: int = 15, ctx: Ctx = Depends(current)):
    """Recent posts and high-signal events for the dashboard."""
    ws = ctx.workspace_id
    limit = max(5, min(limit, 40))
    posts = db.query(
        """SELECT p.id, p.hook, p.status, p.post_date, p.views, p.v, p.s, p.c, p.score_by, p.created_at, a.handle
           FROM posts p LEFT JOIN accounts a ON a.id = p.account_id
           WHERE p.workspace_id=%s
           ORDER BY p.created_at DESC NULLS LAST, p.id DESC
           LIMIT %s""",
        (ws, limit),
    )
    out = []
    for p in posts:
        vsc = None
        if p.get("v") is not None and p.get("s") is not None and p.get("c") is not None:
            vsc = p["v"] + p["s"] + p["c"]
        out.append({
            "type": "post",
            "id": p["id"],
            "handle": p.get("handle") or "",
            "hook": p.get("hook") or "",
            "status": p.get("status") or "",
            "post_date": str(p["post_date"]) if p.get("post_date") else "",
            "views": p.get("views") or 0,
            "vsc": vsc,
            "score_by": p.get("score_by") or "",
            "at": p["created_at"].isoformat() if p.get("created_at") and hasattr(p["created_at"], "isoformat") else None,
        })
    return {"items": out}


@app.get("/api/publish/queued-count")
def publish_queued_count(ctx: Ctx = Depends(current)):
    row = db.one(
        """SELECT COUNT(*) AS n FROM publish_jobs
           WHERE workspace_id=%s AND status IN ('queued','scheduled')""",
        (ctx.workspace_id,),
    )
    attn = db.one(
        "SELECT COUNT(*) AS n FROM publish_jobs WHERE workspace_id=%s AND status='unconfirmed'",
        (ctx.workspace_id,),
    )
    return {"count": int(row["n"]) if row else 0, "attention": int(attn["n"]) if attn else 0}


@app.get("/api/posts")
def posts(start: Optional[date] = None, end: Optional[date] = None, account_id: Optional[int] = None,
          status: Optional[str] = None, source: Optional[str] = None, limit: int = 500, ctx: Ctx = Depends(current)):
    ws = ctx.workspace_id
    where, params = ["p.workspace_id = %s"], [ws]
    if source == "synced":                       # what the sync pulled in from the platforms (the Hooks page)
        where.append("p.external_id IS NOT NULL")
    if start:
        where.append("p.post_date >= %s"); params.append(start)
    if end:
        where.append("p.post_date <= %s"); params.append(end)
    if account_id:
        where.append("p.account_id = %s"); params.append(account_id)
    if status:
        where.append("p.status = %s"); params.append(status)
    sql = POST_SELECT + "WHERE " + " AND ".join(where) + " ORDER BY p.post_date DESC, p.id DESC LIMIT %s"
    st = logic.get_settings(ws)
    perf = logic.performance(ws)
    out = [_post_out(r, st) for r in db.query(sql, (*params, min(limit, 2000)))]
    for p in out:
        p["perf"] = perf.get(p["id"])
    if source == "synced":
        from . import followups
        made = followups.for_hooks(ws, [p["id"] for p in out])
        copies = followups.made_from(ws, [p["id"] for p in out])
        for p in out:
            p["followup"] = made.get(p["id"])
            p["made_from"] = copies.get(p["id"])
    return logic.apply_evidence(out, perf, st)


# ---------- comments: what people asked for ----------

@app.post("/api/posts/{pid}/comments/fetch")
def post_comments_fetch(pid: int, pages: int = 2, ctx: Ctx = Depends(current)):
    """Read a post's top comments (about 15-20 per page, 1 credit per page) and classify buy/ask/more intent."""
    try:
        return sociavault.fetch_post_comments(ctx.workspace_id, pid, pages=max(1, min(pages, 5)))
    except sociavault.SociaVaultError as e:
        raise HTTPException(e.status if e.status in (401, 403, 404) else 400, str(e))


@app.get("/api/next-actions")
def next_actions(ctx: Ctx = Depends(current)):
    """The dashboard queue: what needs the user today."""
    from . import nextactions
    return {"items": nextactions.build(ctx.workspace_id), "pace": nextactions.pace(ctx.workspace_id)}


@app.get("/api/posts/{pid}/candidates")
def post_candidates(pid: int, ctx: Ctx = Depends(current)):
    """Synced videos that could be the video this card became (so the link need not be pasted)."""
    from . import results
    _get_post(ctx.workspace_id, pid)
    return {"candidates": results.candidates(ctx.workspace_id, pid)}


class LinkIn(BaseModel):
    post_id: int


@app.post("/api/posts/{pid}/link")
def post_link(pid: int, b: LinkIn, ctx: Ctx = Depends(current)):
    from . import results
    _get_post(ctx.workspace_id, pid)
    try:
        return results.link(ctx.workspace_id, pid, b.post_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/posts/{pid}/variants")
def post_variants(pid: int, ctx: Ctx = Depends(current)):
    """Make three one-change variants (a / b / c) of a Tweak card."""
    from . import followups
    try:
        return followups.make_variants(ctx.workspace_id, pid)
    except followups.FollowupError as e:
        raise HTTPException(404 if "not found" in str(e).lower() else 400, str(e))


@app.post("/api/posts/{pid}/analyse")
def post_analyse(pid: int, ctx: Ctx = Depends(current)):
    """Mark a Posted card analysed and record how it did against the hook it came from."""
    from . import results
    ws = ctx.workspace_id
    card = _get_post(ws, pid)
    if card.get("external_id") is not None:
        raise HTTPException(400, "Only cards you made can be analysed")
    if not card.get("result_post_id"):
        raise HTTPException(400, "Add the posted video link and wait for the sync to find it first")
    perf = logic.performance(ws)
    results.attach(ws, [card], perf)
    res = card.get("result") or {}
    verdict = res.get("verdict") or ""
    db.execute("UPDATE posts SET status='analysed', verdict=%s, analysed_at=now() WHERE id=%s AND workspace_id=%s",
               (verdict, pid, ws))
    if verdict:
        activation.mark(ws, "first_verdict")
    return {"ok": True, "verdict": verdict, "result": res}


@app.get("/api/credits")
def credits_get(ctx: Ctx = Depends(current)):
    """This month's SociaVault credit spend, when the accounts last synced, and whether auto-sync is on."""
    out = sociavault.credit_summary(ctx.workspace_id)
    out["budget"] = int(logic.get_settings(ctx.workspace_id).get("credit_budget") or 0)
    return out


@app.post("/api/followups/top")
def followups_top(limit: int = 5, account_id: Optional[int] = None, ctx: Ctx = Depends(current)):
    """Send the strongest settled Scale hooks that are not on Posts yet (the "Send top 5 Scale" button)."""
    from . import followups
    return followups.send_top(ctx.workspace_id, limit, account_id)


@app.post("/api/posts/{pid}/followup")
def post_followup(pid: int, account_id: Optional[int] = None, ctx: Ctx = Depends(current)):
    """Send a Scale hook to Posts as Scripted, or a Tweak hook as an Idea (the user's "Post" button)."""
    from . import followups
    try:
        return followups.create_for(ctx.workspace_id, pid, account_id)
    except followups.FollowupError as e:
        raise HTTPException(400 if "found" not in str(e) else 404, str(e))


@app.get("/api/posts/{pid}")
def post_get(pid: int, ctx: Ctx = Depends(current)):
    ws = ctx.workspace_id
    out = _get_post(ws, pid)
    perf = logic.performance(ws)
    logic.apply_evidence([out], perf, logic.get_settings(ws))
    out["perf"] = perf.get(pid)
    from . import results
    results.attach(ws, [out], perf)
    return out


@app.get("/api/posts/{pid}/comments")
def post_comments(pid: int, kind: str = "intent", limit: int = 50, ctx: Ctx = Depends(current)):
    """Stored comments for a post. kind: intent (buy+ask), buy, ask, more, all."""
    ws = ctx.workspace_id
    if not db.one("SELECT 1 FROM posts WHERE id=%s AND workspace_id=%s", (pid, ws)):
        raise HTTPException(404, "Post not found")
    where = {"intent": "AND kind IN ('buy','ask')", "buy": "AND kind='buy'", "ask": "AND kind='ask'",
             "more": "AND kind='more'", "all": ""}.get(kind, "AND kind IN ('buy','ask')")
    rows = db.query(
        f"SELECT comment_id, body, likes, replies, posted_at, kind, score, matched FROM post_comments "
        f"WHERE post_id=%s AND workspace_id=%s {where} ORDER BY score DESC, likes DESC NULLS LAST LIMIT %s",
        (pid, ws, max(1, min(limit, 200))))
    summary = db.one("SELECT comments_fetched, buy_comments, ask_comments, more_comments, comments_fetched_at "
                     "FROM posts WHERE id=%s AND workspace_id=%s", (pid, ws))
    return {"comments": rows, "summary": summary}


@app.post("/api/comments/winners")
def comments_for_winners(limit: int = 5, pages: int = 1, ctx: Ctx = Depends(current)):
    """Read the comments of this workspace's best recent posts that have not been read in the last week.
    The only automatic credit spend on comments, and only when someone asks for it."""
    ws = ctx.workspace_id
    limit, pages = max(1, min(limit, 10)), max(1, min(pages, 3))
    perf = logic.performance(ws)
    ranked = sorted((pid for pid, p in perf.items() if p["label"] in ("WINNER", "HOT_START")),
                    key=lambda pid: -(perf[pid]["multiple"] or perf[pid]["early_multiple"] or 0))
    fresh = {r["id"] for r in db.query(
        "SELECT id FROM posts WHERE workspace_id=%s AND comments_fetched_at > now() - interval '7 days'", (ws,))}
    todo = [pid for pid in ranked if pid not in fresh][:limit]
    done, credits, errors = [], 0, []
    for pid in todo:
        try:
            r = sociavault.fetch_post_comments(ws, pid, pages=pages)
            credits += r["credits_est"]
            done.append({"post_id": pid, **{k: r[k] for k in ("fetched", "buy", "ask", "more", "intent")}})
        except sociavault.SociaVaultError as e:
            errors.append({"post_id": pid, "error": str(e)})
            if e.status in (401, 403):
                break
    return {"read": done, "credits_est": credits, "errors": errors, "skipped_already_read": len(ranked) - len(todo)}


# ---------- early velocity ----------

@app.get("/api/velocity")
def velocity(days: int = 14, limit: int = 30, ctx: Ctx = Depends(current)):
    """Recent posts by how fast they started, against this account's usual first-day views. Hot starts first."""
    ws = ctx.workspace_id
    days = max(1, min(days, 60))
    perf = logic.performance(ws, days=days + 2)
    since = logic.today(ws) - timedelta(days=days)
    rows = db.query(POST_SELECT + "WHERE p.workspace_id=%s AND p.post_date >= %s AND p.id = ANY(%s)",
                    (ws, since, list(perf.keys()) or [0]))
    st = logic.get_settings(ws)
    out = logic.apply_evidence(
        [{**_post_out(r, st), "perf": perf[r["id"]]} for r in rows if perf[r["id"]]["early_views"] is not None], perf, st)
    out.sort(key=lambda x: (x["perf"]["label"] != "HOT_START", -(x["perf"]["early_multiple"] or 0)))
    return out[:max(1, min(limit, 100))]


@app.get("/api/winners")
def winners(days: int = 60, limit: int = 20, account_id: Optional[int] = None, label: str = "WINNER",
            ctx: Ctx = Depends(current)):
    """Posts ranked by how far they beat their own account's normal (not by raw views)."""
    ws = ctx.workspace_id
    days = max(1, min(days, 365))
    perf = logic.performance(ws, days=days)
    st_w = logic.get_settings(ws)
    since = logic.today(ws) - timedelta(days=days)
    rows = db.query(
        POST_SELECT + "WHERE p.workspace_id = %s AND p.post_date >= %s AND p.id = ANY(%s) ",
        (ws, since, list(perf.keys()) or [0]))
    out = []
    for r in rows:
        pf = perf[r["id"]]
        if account_id and r["account_id"] != account_id:
            continue
        if label and label.upper() != "ALL" and pf["label"] != label.upper():
            continue
        out.append({**_post_out(r, st_w), "perf": pf})
    logic.apply_evidence(out, perf, st_w)
    out.sort(key=lambda x: (-(x["perf"]["multiple"] or 0), -(x["perf"]["intent_per_1k"] or 0)))
    return out[:max(1, min(limit, 200))]


@app.post("/api/hooks/score")
def hooks_score(ctx: Ctx = Depends(current)):
    """Score every judged hook from its measured results now (also runs after each sync). Free: no AI call."""
    ws = ctx.workspace_id
    perf = logic.performance(ws)
    scored = logic.apply_auto_scores(ws, perf)
    judged = sum(1 for pf in perf.values() if logic.engine_scores(pf))
    return {"scored": scored, "judged": judged, "waiting": sum(1 for pf in perf.values() if not logic.engine_scores(pf))}


@app.post("/api/posts")
def post_create(b: PostIn, ctx: Ctx = Depends(current)):
    return _get_post(ctx.workspace_id, _insert_post(ctx.workspace_id, b))


class BulkPosts(BaseModel):
    posts: list[PostIn] = Field(max_length=500)


@app.post("/api/posts/bulk")
def posts_bulk(b: BulkPosts, ctx: Ctx = Depends(current)):
    for p in b.posts:   # validate first so a bad row doesn't leave a half-saved batch
        p.status = _normalize_status(p.status)
        _own_account(ctx.workspace_id, p.account_id)
    return {"created": [_insert_post(ctx.workspace_id, p) for p in b.posts]}


def _promote_tweak(ws: int, pid: int) -> None:
    """A Tweak card (an Idea made from a Tweak hook) that has been taken past Idea is now a real matching video of
    that hook: it is linked to the hook (so it gets the Matching video section and counts on the scorecard) and its
    T label becomes the next C label. It stays a "tweak" in what its brief asks for. Idempotent; never demotes."""
    db.execute(
        """UPDATE posts SET cloned_from = followup_of, followup_decision = 'MUTATE',
               variant_label = 'C' || ((SELECT COUNT(*) FROM posts s WHERE s.workspace_id = posts.workspace_id
                                          AND s.cloned_from = posts.followup_of) + 1)
           WHERE id=%s AND workspace_id=%s AND external_id IS NULL AND followup_of IS NOT NULL
             AND cloned_from IS NULL AND status <> 'idea'""", (pid, ws))


@app.patch("/api/posts/{pid}")
def post_update(pid: int, b: PostPatch, ctx: Ctx = Depends(current)):
    ws = ctx.workspace_id
    d = b.model_dump(exclude_unset=True)
    if "status" in d:
        d["status"] = _normalize_status(d["status"])
    if d.get("account_id") is not None:
        _own_account(ws, d["account_id"])
    if {"v", "s", "c"} & d.keys():
        d["score_by"] = "manual"
    if isinstance(d.get("url"), str):
        d["url"] = d["url"].strip()
        d["result_post_id"] = None                 # a new address means a new match
    _update("posts", pid, ws, d, POST_FIELDS | {"score_by", "result_post_id"})
    if d.get("status") and d["status"] != "idea":
        _promote_tweak(ws, pid)
    if d.get("url"):
        from . import results
        results.reconcile(ws)
    if {"v", "s", "c"} & d.keys():
        row = db.one("SELECT v,s,c,score_notes,score_reasoning,score_confidence FROM posts WHERE id=%s AND workspace_id=%s", (pid, ws))
        if row and row["v"] is not None and row["s"] is not None and row["c"] is not None:
            logic.record_score_snapshot(
                ws, pid, row["v"], row["s"], row["c"],
                row.get("score_confidence"), row.get("score_reasoning") or "",
                row.get("score_notes") or "", "manual", blind=True)
    return _get_post(ws, pid)


@app.delete("/api/posts/{pid}")
def post_delete(pid: int, ctx: Ctx = Depends(current)):
    out = _delete("posts", pid, ctx.workspace_id)
    media.purge_post_files(ctx.workspace_id, pid)          # an uploaded video goes with its post
    return out




@app.get("/api/pipeline")
def pipeline_board(account_id: Optional[int] = None, ctx: Ctx = Depends(current)):
    """Kanban board: posts grouped by pipeline stage (linked to post rows)."""
    ws = ctx.workspace_id
    st = logic.get_settings(ws)
    # posts pulled in by the sync live on Hooks; the board is the user's own work, moved between columns by hand
    where, params = ["p.workspace_id = %s", "p.external_id IS NULL"], [ws]
    if account_id:
        where.append("p.account_id = %s"); params.append(account_id)
    rows = db.query(
        POST_SELECT + "WHERE " + " AND ".join(where) + " ORDER BY p.post_date DESC, p.id DESC LIMIT 800",
        params,
    )
    from . import results
    if results.reconcile(ws):                       # cards whose posted video the sync has since pulled in
        rows = db.query(
            POST_SELECT + "WHERE " + " AND ".join(where) + " ORDER BY p.post_date DESC, p.id DESC LIMIT 800", params)
    columns = {s: [] for s in PIPELINE_ORDER}
    for r in rows:
        status = STATUS_ALIASES.get(r.get("status") or "", r.get("status") or "idea")
        if status not in columns:
            status = "idea"
        columns[status].append(_post_out(r, st))
    results.attach(ws, [c for col in columns.values() for c in col])
    return {
        "stages": PIPELINE_ORDER,
        "columns": columns,
        "counts": {s: len(columns[s]) for s in PIPELINE_ORDER},
    }


@app.patch("/api/posts/{pid}/stage")
def post_move_stage(pid: int, stage: str = "", ctx: Ctx = Depends(current)):
    """Move a post card to a pipeline stage (same row, status update)."""
    ws = ctx.workspace_id
    stage = _normalize_status(stage)
    row = db.one(
        "UPDATE posts SET status=%s WHERE id=%s AND workspace_id=%s RETURNING id",
        (stage, pid, ws),
    )
    if not row:
        raise HTTPException(404, "Post not found")
    if stage != "idea":
        _promote_tweak(ws, pid)
    return _get_post(ws, pid)


# ---------- score history / threshold simulation / A-B experiments ----------

@app.get("/api/posts/{pid}/scores")
def post_score_history(pid: int, ctx: Ctx = Depends(current)):
    if not db.one("SELECT 1 FROM posts WHERE id=%s AND workspace_id=%s", (pid, ctx.workspace_id)):
        raise HTTPException(404, "Post not found")
    rows = logic.score_history(ctx.workspace_id, pid)
    for r in rows:
        if r.get("created_at") and hasattr(r["created_at"], "isoformat"):
            r["created_at"] = r["created_at"].isoformat()
    return rows


@app.get("/api/decisions/suggest")
def decisions_suggest(ctx: Ctx = Depends(current)):
    """Scale/Tweak lines fitted to how this workspace's hook scores relate to how its posts actually did."""
    return logic.suggest_thresholds(ctx.workspace_id)


@app.get("/api/decisions/preview")
def decisions_preview(scale_vsc: Optional[int] = None, mutate_vsc: Optional[int] = None,
                      ctx: Ctx = Depends(current)):
    """What would SCALE/MUTATE/KILL be if VSC thresholds changed?"""
    return logic.decision_preview(ctx.workspace_id, scale_vsc, mutate_vsc)


class ExperimentIn(BaseModel):
    name: str = Field("", max_length=120)
    account_id: Optional[int] = None
    body: str = Field("", max_length=1000)
    notes: str = Field("", max_length=1000)
    post_date: Optional[date] = None
    variants: list = Field(default_factory=list)  # [{label, hook, caption?}]


@app.get("/api/experiments")
def experiments_list(ctx: Ctx = Depends(current)):
    ws = ctx.workspace_id
    exps = db.query(
        """SELECT e.*, a.handle,
            (SELECT COUNT(*) FROM posts p WHERE p.experiment_id = e.id) AS variant_count
           FROM experiments e LEFT JOIN accounts a ON a.id = e.account_id
           WHERE e.workspace_id=%s ORDER BY e.created_at DESC LIMIT 100""",
        (ws,),
    )
    st = logic.get_settings(ws)
    out = []
    for e in exps:
        if e.get("created_at") and hasattr(e["created_at"], "isoformat"):
            e["created_at"] = e["created_at"].isoformat()
        variants = db.query(
            POST_SELECT + "WHERE p.experiment_id=%s AND p.workspace_id=%s ORDER BY p.variant_label, p.id",
            (e["id"], ws),
        )
        e["variants"] = [_post_out(v, st) for v in variants]
        out.append(e)
    return out


@app.post("/api/experiments")
def experiment_create(b: ExperimentIn, ctx: Ctx = Depends(current)):
    """Create an A/B hook experiment: same body, different first-line hooks (variants)."""
    ws = ctx.workspace_id
    _own_account(ws, b.account_id)
    variants = b.variants or []
    if len(variants) < 2:
        raise HTTPException(400, "Add at least 2 variants (different hooks / first 3 seconds)")
    if len(variants) > 6:
        raise HTTPException(400, "Max 6 variants")
    name = (b.name or "").strip() or f"Hook test {logic.today(ws).isoformat()}"
    post_date = b.post_date or logic.today(ws)
    exp = db.one(
        """INSERT INTO experiments (workspace_id, account_id, name, body, notes)
           VALUES (%s,%s,%s,%s,%s) RETURNING *""",
        (ws, b.account_id, name, b.body or "", b.notes or ""),
    )
    parent_id = None
    created = []
    for i, var in enumerate(variants):
        if not isinstance(var, dict):
            raise HTTPException(400, "Each variant must be an object with hook + label")
        hook = (var.get("hook") or "").strip()
        if not hook:
            raise HTTPException(400, "Each variant needs a hook")
        label = (var.get("label") or chr(65 + i)).strip()[:40]  # A, B, C…
        caption = (var.get("caption") or "")[:2200]
        row = db.one(
            """INSERT INTO posts
               (workspace_id, account_id, post_date, status, hook, body, caption,
                parent_id, variant_label, experiment_id)
               VALUES (%s,%s,%s,'scripted',%s,%s,%s,%s,%s,%s) RETURNING id""",
            (ws, b.account_id, post_date, hook, b.body or "", caption,
             parent_id, label, exp["id"]),
        )
        if parent_id is None:
            parent_id = row["id"]
            # first variant is the parent reference for the rest
            db.execute("UPDATE posts SET parent_id=%s WHERE id=%s AND workspace_id=%s",
                       (parent_id, row["id"], ws))
        created.append(row["id"])
    # point later variants at first
    if parent_id and len(created) > 1:
        db.execute(
            "UPDATE posts SET parent_id=%s WHERE experiment_id=%s AND id<>%s AND workspace_id=%s",
            (parent_id, exp["id"], parent_id, ws),
        )
    return {"id": exp["id"], "name": name, "variant_ids": created,
            "variants": [_get_post(ws, i) for i in created]}


@app.get("/api/experiments/{eid}")
def experiment_get(eid: int, ctx: Ctx = Depends(current)):
    ws = ctx.workspace_id
    e = db.one("SELECT * FROM experiments WHERE id=%s AND workspace_id=%s", (eid, ws))
    if not e:
        raise HTTPException(404, "Experiment not found")
    st = logic.get_settings(ws)
    variants = db.query(
        POST_SELECT + "WHERE p.experiment_id=%s AND p.workspace_id=%s ORDER BY p.variant_label, p.id",
        (eid, ws),
    )
    e["variants"] = [_post_out(v, st) for v in variants]
    if e.get("created_at") and hasattr(e["created_at"], "isoformat"):
        e["created_at"] = e["created_at"].isoformat()
    # simple winner by views among posted
    posted = [v for v in e["variants"] if v.get("status") == "posted"]
    e["leader"] = max(posted, key=lambda x: x.get("views") or 0) if posted else None
    return e


@app.delete("/api/experiments/{eid}")
def experiment_delete(eid: int, ctx: Ctx = Depends(current)):
    ws = ctx.workspace_id
    db.execute("UPDATE posts SET experiment_id=NULL, parent_id=NULL, variant_label='' WHERE experiment_id=%s AND workspace_id=%s",
               (eid, ws))
    if not db.execute("DELETE FROM experiments WHERE id=%s AND workspace_id=%s", (eid, ws)):
        raise HTTPException(404, "Not found")
    return {"ok": True}


# ---------- rules / dashboard ----------

@app.get("/api/settings")
def settings_get(ctx: Ctx = Depends(current)):
    return logic.get_settings(ctx.workspace_id)


@app.put("/api/settings")
def settings_put(values: dict, ctx: Ctx = Depends(admin)):
    for k, v in values.items():
        if k in logic.DEFAULT_SETTINGS and (not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 10**9):
            raise HTTPException(400, f"{k} must be a non-negative integer")
    if {"hold_low_pct", "hold_high_pct", "winner_pct"} & values.keys():
        cur = logic.get_settings(ctx.workspace_id)
        lo = int(values.get("hold_low_pct", cur["hold_low_pct"]))
        hi = int(values.get("hold_high_pct", cur["hold_high_pct"]))
        win = int(values.get("winner_pct", cur["winner_pct"]))
        if not (50 <= lo < 100 <= hi < win):
            raise HTTPException(400, "Hold must run from between 50% and 99% of normal up to a value from 100% that is "
                                     "below the winner threshold")
    return logic.save_settings(ctx.workspace_id, values)


@app.get("/api/decisions/calibration")
def decisions_calibration(ctx: Ctx = Depends(current)):
    """How often a hook called Scale around day 3 is still beating its account's normal around day 30."""
    return logic.calibration(ctx.workspace_id)


@app.get("/api/dashboard")
def dashboard(ctx: Ctx = Depends(current)):
    return logic.dashboard(ctx.workspace_id)


# ---------- AI configuration (per workspace, admin only) ----------

KEY_RE = re.compile(r"^\S{1,400}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9._:/@\-]{1,100}$")


def _saved_ai(ws: int) -> dict:
    return db.one("SELECT api_key_enc, key_hint, model, provider, base_url FROM workspace_ai WHERE workspace_id=%s",
                  (ws,)) or {"api_key_enc": "", "key_hint": "", "model": "", "provider": ai.DEFAULT_PROVIDER, "base_url": ""}


def _providers_meta() -> dict:
    return {k: {"label": v["label"], "default_model": v["default_model"], "suggestions": v["suggestions"],
                "needs_key": v["needs_key"], "needs_url": v["needs_url"]} for k, v in ai.PROVIDERS.items()}


@app.get("/api/ai/status")
def ai_status(ctx: Ctx = Depends(current)):
    cfg = ai.resolve(ctx.workspace_id)
    return {"available": cfg.available, "model": cfg.model, "source": cfg.source, "provider": cfg.provider}


@app.get("/api/ai/config")
def ai_config(ctx: Ctx = Depends(admin)):
    cfg = ai.resolve(ctx.workspace_id)
    row = _saved_ai(ctx.workspace_id)
    env_key = ai.PROVIDERS[cfg.provider]["env_key"]
    return {
        "provider": cfg.provider, "base_url": row["base_url"], "source": cfg.source, "available": cfg.available,
        "has_workspace_key": bool(row["api_key_enc"]), "key_hint": row["key_hint"],
        "has_server_key": bool(env_key and os.environ.get(env_key)),
        "model": row["model"], "effective_model": cfg.model, "providers": _providers_meta(),
    }


class AIConfigIn(BaseModel):
    provider: Optional[str] = None     # anthropic | openai | compatible
    base_url: Optional[str] = None     # compatible only
    api_key: Optional[str] = None      # omitted/blank = keep the saved key
    model: Optional[str] = None        # blank = provider default
    clear_key: bool = False


def _clean_provider(p: Optional[str], fallback: str) -> str:
    p = (p or fallback).strip()
    if p not in ai.PROVIDERS:
        raise HTTPException(400, "Unknown provider")
    return p


def _clean_key(k: Optional[str], provider: str) -> str:
    k = (k or "").strip()
    if k and not KEY_RE.match(k):
        raise HTTPException(400, "API keys can't contain spaces")
    if k and provider != "compatible" and len(k) < 20:
        raise HTTPException(400, "That doesn't look like a complete API key")
    return k


def _clean_model(m: Optional[str]) -> str:
    m = (m or "").strip()
    if m and not MODEL_RE.match(m):
        raise HTTPException(400, "Model names may only contain letters, digits and . _ : / @ -")
    return m


def _clean_url(provider: str, url: Optional[str]) -> str:
    if provider != "compatible":
        return ""
    try:
        return ai.check_base_url(url or "")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.put("/api/ai/config")
def ai_config_put(b: AIConfigIn, ctx: Ctx = Depends(admin)):
    ws = ctx.workspace_id
    old = _saved_ai(ws)
    provider = _clean_provider(b.provider, old["provider"])
    base_url = _clean_url(provider, b.base_url if b.base_url is not None else old["base_url"])
    key = _clean_key(b.api_key, provider)
    # A saved key belongs to one provider/endpoint. Never let it be sent somewhere else.
    moved = provider != old["provider"] or base_url != old["base_url"]
    db.execute("INSERT INTO workspace_ai(workspace_id) VALUES (%s) ON CONFLICT DO NOTHING", (ws,))
    db.execute("UPDATE workspace_ai SET provider=%s, base_url=%s, updated_at=now() WHERE workspace_id=%s",
               (provider, base_url, ws))
    if b.clear_key or (moved and not key):
        db.execute("UPDATE workspace_ai SET api_key_enc='', key_hint='' WHERE workspace_id=%s", (ws,))
    if key and not b.clear_key:
        hint = key[:7] + "…" + key[-4:] if len(key) > 12 else "…" + key[-3:]
        db.execute("UPDATE workspace_ai SET api_key_enc=%s, key_hint=%s WHERE workspace_id=%s",
                   (crypto.encrypt(key), hint, ws))
    if b.model is not None:
        db.execute("UPDATE workspace_ai SET model=%s WHERE workspace_id=%s", (_clean_model(b.model), ws))
    elif moved and provider != old["provider"]:
        db.execute("UPDATE workspace_ai SET model='' WHERE workspace_id=%s", (ws,))   # models are provider-specific
    return ai_config(ctx)


class AITestIn(BaseModel):
    provider: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None      # test values before saving them
    model: Optional[str] = None


@app.post("/api/ai/test")
def ai_test(b: AITestIn, ctx: Ctx = Depends(admin)):
    ws = ctx.workspace_id
    saved = _saved_ai(ws)
    provider = _clean_provider(b.provider, saved["provider"])
    base_url = _clean_url(provider, b.base_url if b.base_url is not None else saved["base_url"])
    key = _clean_key(b.api_key, provider)
    same_target = provider == saved["provider"] and base_url == saved["base_url"]
    if not key and same_target:
        key = ai.resolve(ws).api_key                        # saved or server key, same destination only
    elif not key and provider != "compatible":
        key = os.environ.get(ai.PROVIDERS[provider]["env_key"], "")
    spec = ai.PROVIDERS[provider]
    model = _clean_model(b.model) or (saved["model"] if same_target else "") or spec["default_model"]
    cfg = ai.Config(key, model, "workspace", provider, base_url)
    try:
        reply = ai.test_connection(cfg)
    except ai.AIUnavailable:
        raise HTTPException(400, "Fill in the " + ("endpoint URL and model name" if provider == "compatible" else "API key") + " first")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"The provider rejected the request: {_scrub(e, key)}")
    return {"ok": True, "model": cfg.model, "provider": provider, "reply": reply[:40]}


# ---------- AI features ----------

@app.post("/api/ai/extract-views")
async def ai_extract_views(text: str = Form(""), files: list[UploadFile] = File(default=[]),
                           ctx: Ctx = Depends(current)):
    images = []
    for f in files[:6]:
        data = await f.read()
        if f.content_type not in ALLOWED_IMAGES:
            raise HTTPException(400, f"{f.filename}: only PNG, JPEG, WebP or GIF images")
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, f"{f.filename}: larger than 8 MB")
        images.append((f.content_type, data))
    if not images and not text.strip():
        raise HTTPException(400, "Paste some text or attach a screenshot")
    accounts_ = db.query("SELECT id, handle FROM accounts WHERE workspace_id=%s ORDER BY handle", (ctx.workspace_id,))
    cfg = ai.resolve(ctx.workspace_id)
    return _ai_metered(ctx.workspace_id, ai.extract_views, cfg, text[:8000], images, accounts_, logic.today(ctx.workspace_id))


class HooksIn(BaseModel):
    account_id: int
    n: int = Field(5, ge=1, le=12)
    brief: str = Field("", max_length=500)


@app.post("/api/ai/hooks")
def ai_hooks(b: HooksIn, ctx: Ctx = Depends(current)):
    ws = ctx.workspace_id
    acc = db.one("SELECT * FROM accounts WHERE id=%s AND workspace_id=%s", (b.account_id, ws))
    if acc is None:
        raise HTTPException(404, "Account not found")
    winners, losers = logic.winner_examples(ws, account_id=acc["id"])
    inspirations = db.query(
        """SELECT hook_text, title, notes, platform, source_url FROM inspiration
           WHERE workspace_id=%s AND use_in_ai AND (hook_text<>'' OR title<>'')
           ORDER BY created_at DESC LIMIT 12""",
        (ws,),
    )
    return {"ideas": _ai_metered(ws, ai.generate_hooks, ai.resolve(ws), acc, winners, losers, b.n, b.brief, inspirations)}


class PlanIn(BaseModel):
    start: Optional[date] = None
    days: int = Field(7, ge=1, le=14)


@app.post("/api/ai/plan-week")
def ai_plan(b: PlanIn, ctx: Ctx = Depends(current)):
    ws = ctx.workspace_id
    start = b.start or logic.today(ws)      # plan forward from today, never into days that are already gone
    accs = db.query("SELECT * FROM accounts WHERE workspace_id=%s ORDER BY handle", (ws,))
    winners, _losers = logic.winner_examples(ws)
    raw = _ai_metered(ws, ai.plan_week, ai.resolve(ws), start, accs, winners, b.days)
    out = []
    for item in raw:
        acc = ai.match_account(item.get("account", ""), accs)
        try:
            day = date.fromisoformat(str(item.get("day", ""))[:10])
        except ValueError:
            continue
        if acc and start <= day < start + timedelta(days=b.days):
            out.append({"account_id": acc["id"], "handle": acc["handle"], "post_date": day.isoformat(),
                        "status": "scripted", **{k: str(item.get(k, ""))[:500] for k in ("ai_prompt", "hook", "body", "caption")}})
    return {"posts": out}


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


class AssistantIn(BaseModel):
    messages: list[dict] = Field(min_length=1, max_length=40)
    page: dict = Field(default_factory=dict)


@app.post("/api/assistant/chat")
def assistant_chat(b: AssistantIn, ctx: Ctx = Depends(current)):
    """The floating guide. Not counted against the workspace's AI allowance (help should never be rationed away),
    but each person is capped per day so a loop can't run up the platform's bill."""
    from . import assistant
    if not assistant.allow(ctx.user_id):
        raise HTTPException(429, "You have asked a lot of questions today. Please try again tomorrow, or contact support.")
    try:
        text = _ai_guard(assistant.reply, ai.resolve(ctx.workspace_id), ctx.workspace_id, ctx.user_id, b.messages, b.page)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not text:
        raise HTTPException(502, "The guide could not answer just now. Please try again.")
    return {"answer": text}


@app.post("/api/ai/ask")
def ai_ask(b: AskIn, ctx: Ctx = Depends(current)):
    ws = ctx.workspace_id
    this_week = logic.week_grid(ws, logic.monday(logic.today(ws)))
    recent = posts(limit=80, ctx=ctx)
    snapshot = {
        "today": logic.today(ws).isoformat(),
        "rules": logic.get_settings(ws),
        "dashboard": logic.dashboard(ws),
        "this_week_by_account": [
            {"handle": r["handle"], "phone": r["phone_label"], "niche": r["niche"], "status": r["status"],
             "daily_views": [c["views"] for c in r["cells"]], "weekly_total": r["weekly_total"],
             "decision": r["decision"], "reason": r["reason"]} for r in this_week["rows"]],
        "recent_posts": [
            {k: p[k] for k in ("post_date", "handle", "status", "hook", "views", "comments", "intent", "vsc", "decision")}
            for p in recent],
    }
    return {"answer": _ai_metered(ws, ai.ask, ai.resolve(ws), b.question, snapshot)}







# ---------- demo sample data ----------

@app.post("/api/workspace/seed-demo")
def seed_demo(force: bool = False, ctx: Ctx = Depends(admin)):
    """Load sample accounts, views, and scored posts so new users can explore."""
    try:
        return demo.seed_demo(ctx.workspace_id, force=force)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/workspace/clear-demo")
def clear_demo(ctx: Ctx = Depends(admin)):
    """Remove all phones/accounts/views/posts in this workspace (for resetting sample data)."""
    return demo.clear_demo(ctx.workspace_id)

# ---------- social publish add-on ----------

class PublishConfigIn(BaseModel):
    enabled: bool = False
    provider: str = "upload_post"
    api_key: Optional[str] = None
    clear_key: bool = False
    config: Optional[dict] = None


class PublishIn(BaseModel):
    platforms: Optional[list[str]] = None
    schedule_at: Optional[str] = None  # ISO datetime if scheduling
    schedule_local: Optional[str] = None  # wall-clock time in the workspace's zone, e.g. 2026-10-21T19:00
    dry_run: bool = False


@app.get("/api/addons/publish")
def publish_config_get(ctx: Ctx = Depends(current)):
    return publish.get_config(ctx.workspace_id)


@app.put("/api/addons/publish")
def publish_config_put(b: PublishConfigIn, ctx: Ctx = Depends(admin)):
    try:
        return publish.save_config(
            ctx.workspace_id,
            enabled=b.enabled,
            provider=b.provider,
            api_key=b.api_key,
            clear_key=b.clear_key,
            config=b.config,
        )
    except publish.PublishError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/publish/managed/accounts/{account_id}")
def publish_managed_disconnect(account_id: str, ctx: Ctx = Depends(admin)):
    """Disconnect a connected TikTok / Instagram / YouTube account from this workspace."""
    from . import latehub
    try:
        return latehub.disconnect(ctx.workspace_id, account_id)
    except latehub.LateHubError as e:
        raise HTTPException(404 if e.status == 404 else 400, str(e))


class ConnectIn(BaseModel):
    platform: str
    return_to: Optional[str] = None    # where to land after connecting, e.g. "#/pipeline" (an in-app route only)


@app.get("/api/publish/managed")
def publish_managed(ctx: Ctx = Depends(current)):
    """HookTrack's own publishing connection: is it available, and which accounts has this workspace connected."""
    from . import latehub
    if not latehub.available():
        return {"available": False, "accounts": []}
    waiting = db.one("SELECT COUNT(*) AS n FROM publish_jobs WHERE workspace_id=%s AND status IN ('queued','scheduled')",
                     (ctx.workspace_id,))["n"]
    try:
        return {"available": True, "accounts": latehub.accounts(ctx.workspace_id), "scheduled": int(waiting)}
    except latehub.LateHubError as e:
        return {"available": True, "accounts": [], "error": str(e), "scheduled": int(waiting)}


@app.post("/api/publish/managed/connect")
def publish_managed_connect(b: ConnectIn, request: Request, ctx: Ctx = Depends(admin)):
    """Where to send the user to connect a TikTok / Instagram / YouTube account to this workspace."""
    from . import latehub
    base = (os.environ.get("HOOKTRACK_PUBLIC_URL") or str(request.base_url)).rstrip("/")
    try:
        billing.require_connected(ctx.workspace_id, len(latehub.accounts(ctx.workspace_id)))
        back = b.return_to if (b.return_to and re.fullmatch(r"#/[a-z]{1,20}", b.return_to)) else "#/settings"
        return {"auth_url": latehub.connect_url(ctx.workspace_id, b.platform.lower(), f"{base}/{back}")}
    except latehub.LateHubError as e:
        raise HTTPException(e.status if e.status in (403, 404) else 400, str(e))


@app.post("/api/addons/publish/test")
def publish_config_test(ctx: Ctx = Depends(admin)):
    try:
        return publish.test_connection(ctx.workspace_id)
    except publish.PublishError as e:
        raise HTTPException(e.status or 400, str(e))


@app.post("/api/posts/{pid}/publish")
def post_publish(pid: int, b: PublishIn = PublishIn(), ctx: Ctx = Depends(current)):
    """Explicit publish action — never automatic."""
    try:
        return publish.publish_post(
            ctx.workspace_id, pid,
            platforms=b.platforms,
            schedule_at=b.schedule_at,
            schedule_local=b.schedule_local,
            dry_run=b.dry_run,
        )
    except publish.PublishError as e:
        raise HTTPException(e.status or 400, str(e))


class ResolveIn(BaseModel):
    action: str
    external_id: str = ""


@app.get("/api/publish/attention")
def publish_attention(ctx: Ctx = Depends(current)):
    """Publishes that were sent but never confirmed (server stopped mid-publish)."""
    return publish.list_attention(ctx.workspace_id)


@app.post("/api/publish/jobs/{jid}/resolve")
def publish_job_resolve(jid: int, b: ResolveIn, ctx: Ctx = Depends(current)):
    try:
        return publish.resolve_unconfirmed(ctx.workspace_id, jid, b.action, b.external_id)
    except publish.PublishError as e:
        raise HTTPException(e.status or 400, str(e))


@app.get("/api/posts/{pid}/publish")
def post_publish_status(pid: int, ctx: Ctx = Depends(current)):
    job = publish.get_post_job(ctx.workspace_id, pid)
    return job or {"status": "none"}


@app.get("/api/publish/scheduled")
def publish_scheduled_list(ctx: Ctx = Depends(current)):
    """Upcoming HookTrack-queued or provider-scheduled publish jobs."""
    return publish.list_scheduled(ctx.workspace_id)


class RescheduleIn(BaseModel):
    schedule_at: Optional[str] = None
    schedule_local: Optional[str] = None


@app.post("/api/posts/{pid}/reschedule")
def post_reschedule(pid: int, b: RescheduleIn, ctx: Ctx = Depends(current)):
    """Move a queued publish to a new time (calendar drag, Scheduled page)."""
    try:
        return publish.reschedule(ctx.workspace_id, pid, schedule_at=b.schedule_at, schedule_local=b.schedule_local)
    except publish.PublishError as e:
        raise HTTPException(e.status or 400, str(e))


@app.get("/api/publish/suggest-times")
def publish_suggest_times(account_id: Optional[int] = None, n: int = 4, ctx: Ctx = Depends(current)):
    """Next free publish slots for an account, in the workspace's time zone."""
    slots = publish.suggest_slots(ctx.workspace_id, account_id, max(1, min(12, n)))
    return {"timezone": logic.workspace_tz(ctx.workspace_id), "slots": slots}


@app.delete("/api/publish/scheduled/{jid}")
def publish_scheduled_cancel(jid: int, ctx: Ctx = Depends(current)):
    try:
        return publish.cancel_schedule(ctx.workspace_id, jid)
    except publish.PublishError as e:
        raise HTTPException(400, str(e))


# ---------- inspiration / competitor library ----------

def _insp_out(r: dict) -> dict:
    if r.get("created_at") and hasattr(r["created_at"], "isoformat"):
        r = dict(r)
        r["created_at"] = r["created_at"].isoformat()
    if r.get("image_path"):
        r["image_url"] = "/uploads/" + r["image_path"].lstrip("/")
    else:
        r["image_url"] = None
    return r


@app.get("/api/inspiration")
def inspiration_list(ctx: Ctx = Depends(current)):
    rows = db.query(
        "SELECT * FROM inspiration WHERE workspace_id=%s ORDER BY created_at DESC LIMIT 200",
        (ctx.workspace_id,),
    )
    return [_insp_out(r) for r in rows]


@app.post("/api/inspiration")
async def inspiration_create(
    title: str = Form(""),
    source_url: str = Form(""),
    platform: str = Form(""),
    hook_text: str = Form(""),
    notes: str = Form(""),
    tags: str = Form(""),
    use_in_ai: str = Form("true"),
    file: UploadFile | None = File(None),
    ctx: Ctx = Depends(current),
):
    ws = ctx.workspace_id
    image_path = ""
    if file and file.filename:
        data = await file.read()
        if file.content_type not in ALLOWED_IMAGES:
            raise HTTPException(400, "Screenshot must be PNG, JPEG, WebP or GIF")
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "Image larger than 8 MB")
        ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}.get(
            file.content_type, ".bin")
        folder = UPLOADS / str(ws)
        folder.mkdir(parents=True, exist_ok=True)
        import secrets
        name = secrets.token_hex(12) + ext
        (folder / name).write_bytes(data)
        image_path = f"{ws}/{name}"
    if not (hook_text or title or source_url or image_path):
        raise HTTPException(400, "Add a hook text, title, URL, or screenshot")
    row = db.one(
        """INSERT INTO inspiration
           (workspace_id, title, source_url, platform, hook_text, notes, image_path, tags, use_in_ai)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
        (ws, title[:200], source_url[:500], platform[:40], hook_text[:1000], notes[:2000],
         image_path, tags[:200], use_in_ai.lower() not in ("0", "false", "no", "")),
    )
    return _insp_out(row)


class LookupIn(BaseModel):
    url: str = Field(..., max_length=500)


@app.post("/api/inspiration/lookup")
def inspiration_lookup(b: LookupIn, ctx: Ctx = Depends(current)):
    """Read one public video from its link so an inspiration item can be filled in (about 1 credit)."""
    from datetime import datetime, timezone
    u = b.url.strip()
    if not re.match(r"^https?://", u, re.I):
        raise HTTPException(400, "Paste the full link of a TikTok, Instagram or YouTube video")
    host = (urlparse(u).hostname or "").lower()
    plat = ("tiktok" if host.endswith("tiktok.com") else "instagram" if host.endswith("instagram.com")
            else "youtube" if host.endswith(("youtube.com", "youtu.be")) else "")
    if not plat:
        raise HTTPException(400, "Use a TikTok, Instagram or YouTube video link")
    key = sociavault.resolve_key(ctx.workspace_id)
    if not key:
        raise HTTPException(400, "Reading a link is not set up on this server yet")
    try:
        sociavault.credit_gate(ctx.workspace_id, 1)      # plan allowance, email confirmed, platform balance
    except sociavault.SociaVaultError as e:
        raise HTTPException(e.status or 400, str(e))
    try:
        info = sociavault.video_info(plat, u, key)
    except sociavault.SociaVaultError as e:
        raise HTTPException(e.status if e.status in (401, 403, 404) else 400, str(e))
    sociavault.record_credits(ctx.workspace_id, "lookup", 1)
    posted = ""
    if info.get("posted_ts"):
        posted = datetime.fromtimestamp(info["posted_ts"], timezone.utc).date().isoformat()
    elif info.get("posted_iso"):
        posted = str(info["posted_iso"])[:10]
    return {**info, "posted": posted, "source_url": u, "credits_est": 1}


class UseInspirationIn(BaseModel):
    account_id: Optional[int] = None


@app.post("/api/inspiration/{iid}/use")
def inspiration_use(iid: int, b: UseInspirationIn, ctx: Ctx = Depends(current)):
    """Make an Idea card from a saved inspiration item."""
    from . import inspire
    try:
        return inspire.use(ctx.workspace_id, iid, b.account_id)
    except inspire.InspireError as e:
        raise HTTPException(404 if "not found" in str(e).lower() else 400, str(e))


class AttachInspirationIn(BaseModel):
    inspiration_id: int


@app.post("/api/posts/{pid}/inspiration")
def post_inspiration_attach(pid: int, b: AttachInspirationIn, ctx: Ctx = Depends(current)):
    from . import inspire
    try:
        inspire.attach(ctx.workspace_id, pid, b.inspiration_id)
    except inspire.InspireError as e:
        raise HTTPException(404, str(e))
    return {"inspiration": inspire.for_post(ctx.workspace_id, pid)}


@app.delete("/api/posts/{pid}/inspiration/{iid}")
def post_inspiration_detach(pid: int, iid: int, ctx: Ctx = Depends(current)):
    from . import inspire
    inspire.detach(ctx.workspace_id, pid, iid)
    return {"inspiration": inspire.for_post(ctx.workspace_id, pid)}


@app.patch("/api/inspiration/{iid}")
def inspiration_update(iid: int, body: dict, ctx: Ctx = Depends(current)):
    ws = ctx.workspace_id
    allowed = {"title", "source_url", "platform", "hook_text", "notes", "tags", "use_in_ai"}
    data = {k: body[k] for k in allowed if k in body}
    if not data:
        raise HTTPException(400, "Nothing to update")
    if "use_in_ai" in data:
        data["use_in_ai"] = bool(data["use_in_ai"])
    sets = ", ".join(f"{k}=%s" for k in data)
    row = db.one(
        f"UPDATE inspiration SET {sets} WHERE id=%s AND workspace_id=%s RETURNING *",
        (*data.values(), iid, ws),
    )
    if not row:
        raise HTTPException(404, "Not found")
    return _insp_out(row)


@app.delete("/api/inspiration/{iid}")
def inspiration_delete(iid: int, ctx: Ctx = Depends(current)):
    ws = ctx.workspace_id
    row = db.one("SELECT image_path FROM inspiration WHERE id=%s AND workspace_id=%s", (iid, ws))
    if not row:
        raise HTTPException(404, "Not found")
    if row.get("image_path"):
        path = UPLOADS / row["image_path"]
        if path.is_file():
            path.unlink(missing_ok=True)
    db.execute("DELETE FROM inspiration WHERE id=%s AND workspace_id=%s", (iid, ws))
    return {"ok": True}


# ---------- SociaVault connector ----------

class SociaVaultKeyIn(BaseModel):
    api_key: Optional[str] = None
    clear_key: bool = False


class SyncIn(BaseModel):
    days_back: int = Field(14, ge=1, le=90)


@app.get("/api/connectors/sociavault")
def sv_config_get(ctx: Ctx = Depends(current)):
    return sociavault.key_status(ctx.workspace_id)


@app.put("/api/connectors/sociavault")
def sv_config_put(b: SociaVaultKeyIn, ctx: Ctx = Depends(admin)):
    return sociavault.save_key(ctx.workspace_id, b.api_key or "", clear=b.clear_key)


@app.post("/api/connectors/sociavault/test")
def sv_test(b: SociaVaultKeyIn, ctx: Ctx = Depends(admin)):
    key = (b.api_key or "").strip()
    if not key:
        key = sociavault.resolve_key(ctx.workspace_id)
    if not key:
        raise HTTPException(400, "Enter an API key first (or save one)")
    try:
        reply = sociavault.test_connection(key)
    except sociavault.SociaVaultError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "reply": reply}


@app.post("/api/accounts/{aid}/sync")
def account_sync(aid: int, days_back: int = 14, mode: str = "full",
                 ctx: Ctx = Depends(current)):
    if not db.one("SELECT 1 FROM accounts WHERE id=%s AND workspace_id=%s", (aid, ctx.workspace_id)):
        raise HTTPException(404, "Account not found")
    days_back = max(1, min(90, days_back))
    mode = mode if mode in ("light", "full") else "full"
    try:
        return sociavault.sync_account(ctx.workspace_id, aid, days_back, mode=mode)
    except sociavault.SyncSkipped as e:
        raise HTTPException(409, str(e))
    except sociavault.SociaVaultError as e:
        code = e.status if e.status in (401, 403, 404) else 400
        raise HTTPException(code or 400, str(e))


@app.post("/api/sync/all")
def sync_all_accounts(days_back: int = 14, mode: str = "full",
                      ctx: Ctx = Depends(current)):
    days_back = max(1, min(90, days_back))
    mode = mode if mode in ("light", "full") else "full"
    try:
        return sociavault.sync_all(ctx.workspace_id, days_back, mode=mode)
    except sociavault.SociaVaultError as e:
        raise HTTPException(400, str(e))


@app.get("/api/sync/schedule")
def sync_schedule_status(ctx: Ctx = Depends(admin)):
    """Scheduler config + last run (admins)."""
    return sociavault.schedule_status()


@app.post("/api/sync/scheduled")
def sync_scheduled_run(request: Request, force: bool = False):
    """Run the cross-workspace light sync now.

    Intended for external cron, or admins. If HOOKTRACK_CRON_SECRET is set,
    pass it as header X-HookTrack-Cron-Secret. Otherwise requires an admin session.
    """
    secret = (os.environ.get("HOOKTRACK_CRON_SECRET") or "").strip()
    header = (request.headers.get("x-hooktrack-cron-secret") or "").strip()
    if secret:
        if header != secret:
            raise HTTPException(401, "Invalid cron secret")
    else:
        # fall back to admin cookie session
        try:
            admin(current(request))
        except HTTPException:
            raise HTTPException(401, "Admin session or HOOKTRACK_CRON_SECRET required")
    return sociavault.run_scheduled_sync(force=force)


# ---------- import ----------

@app.post("/api/import/xlsx")
async def import_xlsx(file: UploadFile = File(...), week_start: Optional[date] = Form(None),
                      ctx: Ctx = Depends(current)):
    data = await file.read()
    if len(data) > 5 * MAX_UPLOAD:
        raise HTTPException(413, "File too large")
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "Upload an .xlsx file")
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(data)
    try:
        return import_workbook(ctx.workspace_id, tmp.name, week_start)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Could not read workbook: {e}")
    finally:
        Path(tmp.name).unlink(missing_ok=True)


@app.get("/healthz")
def healthz():
    db.one("SELECT 1")
    return {"ok": True}


@app.get("/")
def index(request: Request):
    """The home page. Someone with a session cookie gets the app; everyone else (search engines included) gets the
    public landing page, so the site's front door is a real, indexable page. App links with a #/ route are handed
    to /app by a small script on the landing page."""
    if request.cookies.get(auth.COOKIE):
        return FileResponse(STATIC / "index.html")
    return HTMLResponse(seo.render("landing.html", request), headers={"Vary": "Cookie", "Cache-Control": "public, max-age=300"})


@app.get("/app")
def app_shell():
    """The app at a stable address (sign-in and sign-up links go here). Never indexed."""
    return FileResponse(STATIC / "index.html", headers={"X-Robots-Tag": "noindex, nofollow"})


@app.get("/welcome")
def welcome(request: Request):
    """The landing page at its old address, which the app redirects strangers to. Its canonical address is /."""
    return HTMLResponse(seo.render("landing.html", request), headers={"Cache-Control": "public, max-age=300"})


@app.get("/terms")
def terms_page(request: Request):
    return HTMLResponse(seo.render("terms.html", request), headers={"Cache-Control": "public, max-age=3600"})


@app.get("/privacy")
def privacy_page(request: Request):
    return HTMLResponse(seo.render("privacy.html", request), headers={"Cache-Control": "public, max-age=3600"})


@app.get("/robots.txt")
def robots(request: Request):
    return PlainTextResponse(seo.robots_txt(request), headers={"Cache-Control": "public, max-age=3600"})


@app.get("/sitemap.xml")
def sitemap(request: Request):
    return Response(seo.sitemap_xml(request), media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/public/legal")
def public_legal():
    return JSONResponse({**legal.get(), "terms_version": legal.TERMS_VERSION},
                        headers={"Cache-Control": "public, max-age=60"})


@app.get("/api/public/pricing")
def public_pricing():
    return JSONResponse(billing.public_pricing(), headers={"Cache-Control": "public, max-age=60"})

UPLOADS.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOADS), name="uploads")
app.mount("/", StaticFiles(directory=STATIC), name="static")
