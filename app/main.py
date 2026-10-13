import os
import re
import tempfile
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ai, auth, crypto, db, logic
from .auth import Ctx, admin, current
from .import_xlsx import import_workbook

STATIC = Path(__file__).parent.parent / "static"
ALLOWED_IMAGES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MAX_UPLOAD = 8 * 1024 * 1024


@asynccontextmanager
async def lifespan(_app):
    db.init_schema()
    yield


app = FastAPI(title="HookTrack", lifespan=lifespan)
app.include_router(auth.router)


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


def _scrub(e: Exception, secret: str = "") -> str:
    text = str(e).replace(secret, "…") if secret else str(e)
    return re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "sk-…", text)[:300]


def _post_out(p: dict, st: dict) -> dict:
    p["vsc"] = logic.vsc_total(p.get("v"), p.get("s"), p.get("c"))
    p["decision"] = logic.post_decision(p["vsc"], st)
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


class AccountPatch(BaseModel):
    handle: Optional[str] = Field(None, min_length=1, max_length=80)
    phone_id: Optional[int] = None
    platform: Optional[str] = Field(None, max_length=40)
    niche: Optional[str] = Field(None, max_length=120)
    status: Optional[str] = None
    times_per_day: Optional[int] = Field(None, ge=0, le=50)
    content_hook: Optional[str] = Field(None, max_length=200)
    model: Optional[str] = Field(None, max_length=80)


ACCOUNT_FIELDS = {"handle", "phone_id", "platform", "niche", "status", "times_per_day", "content_hook", "model"}


@app.get("/api/accounts")
def accounts(ctx: Ctx = Depends(current)):
    return db.query(
        "SELECT a.*, p.label AS phone_label, p.color AS phone_color FROM accounts a "
        "LEFT JOIN phones p ON p.id = a.phone_id WHERE a.workspace_id = %s "
        "ORDER BY p.position NULLS LAST, a.handle", (ctx.workspace_id,))


@app.post("/api/accounts")
def account_create(b: AccountIn, ctx: Ctx = Depends(current)):
    ws = ctx.workspace_id
    if b.status not in STATUSES:
        raise HTTPException(400, "Invalid status")
    _own_phone(ws, b.phone_id)
    if db.one("SELECT 1 FROM accounts WHERE workspace_id=%s AND handle=%s", (ws, b.handle)):
        raise HTTPException(409, "That handle already exists")
    cols = list(ACCOUNT_FIELDS)
    d = b.model_dump()
    return db.one(
        f"INSERT INTO accounts(workspace_id, {','.join(cols)}) VALUES (%s, {','.join(['%s'] * len(cols))}) RETURNING *",
        [ws] + [d[c] for c in cols])


@app.patch("/api/accounts/{aid}")
def account_update(aid: int, b: AccountPatch, ctx: Ctx = Depends(current)):
    d = b.model_dump(exclude_unset=True)
    if "status" in d and d["status"] not in STATUSES:
        raise HTTPException(400, "Invalid status")
    if d.get("phone_id") is not None:
        _own_phone(ctx.workspace_id, d["phone_id"])
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
    return logic.week_grid(ctx.workspace_id, start or logic.monday(date.today()), days)


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

POST_STATUSES = {"idea", "planned", "posted"}
POST_FIELDS = {"account_id", "post_date", "status", "ai_prompt", "hook", "body", "caption",
               "views", "comments", "intent", "v", "s", "c", "score_notes"}


class PostIn(BaseModel):
    account_id: Optional[int] = None
    post_date: date
    status: str = "planned"
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
    score_notes: Optional[str] = Field(None, max_length=1000)


def _insert_post(ws: int, b: PostIn) -> int:
    if b.status not in POST_STATUSES:
        raise HTTPException(400, "Invalid status")
    _own_account(ws, b.account_id)
    scored = None not in (b.v, b.s, b.c)
    row = db.one(
        "INSERT INTO posts(workspace_id, account_id, post_date, status, ai_prompt, hook, body, caption, views, "
        "comments, intent, v, s, c, score_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (ws, b.account_id, b.post_date, b.status, b.ai_prompt, b.hook, b.body, b.caption, b.views,
         b.comments, b.intent, b.v, b.s, b.c, "manual" if scored else ""))
    return row["id"]


@app.get("/api/posts")
def posts(start: Optional[date] = None, end: Optional[date] = None, account_id: Optional[int] = None,
          status: Optional[str] = None, limit: int = 500, ctx: Ctx = Depends(current)):
    ws = ctx.workspace_id
    where, params = ["p.workspace_id = %s"], [ws]
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
    return [_post_out(r, st) for r in db.query(sql, (*params, min(limit, 2000)))]


@app.post("/api/posts")
def post_create(b: PostIn, ctx: Ctx = Depends(current)):
    return _get_post(ctx.workspace_id, _insert_post(ctx.workspace_id, b))


class BulkPosts(BaseModel):
    posts: list[PostIn] = Field(max_length=500)


@app.post("/api/posts/bulk")
def posts_bulk(b: BulkPosts, ctx: Ctx = Depends(current)):
    for p in b.posts:   # validate first so a bad row doesn't leave a half-saved batch
        if p.status not in POST_STATUSES:
            raise HTTPException(400, "Invalid status")
        _own_account(ctx.workspace_id, p.account_id)
    return {"created": [_insert_post(ctx.workspace_id, p) for p in b.posts]}


@app.patch("/api/posts/{pid}")
def post_update(pid: int, b: PostPatch, ctx: Ctx = Depends(current)):
    ws = ctx.workspace_id
    d = b.model_dump(exclude_unset=True)
    if "status" in d and d["status"] not in POST_STATUSES:
        raise HTTPException(400, "Invalid status")
    if d.get("account_id") is not None:
        _own_account(ws, d["account_id"])
    if {"v", "s", "c"} & d.keys():
        d["score_by"] = "manual"
    _update("posts", pid, ws, d, POST_FIELDS | {"score_by"})
    return _get_post(ws, pid)


@app.delete("/api/posts/{pid}")
def post_delete(pid: int, ctx: Ctx = Depends(current)):
    return _delete("posts", pid, ctx.workspace_id)


# ---------- rules / dashboard ----------

@app.get("/api/settings")
def settings_get(ctx: Ctx = Depends(current)):
    return logic.get_settings(ctx.workspace_id)


@app.put("/api/settings")
def settings_put(values: dict, ctx: Ctx = Depends(admin)):
    for k, v in values.items():
        if k in logic.DEFAULT_SETTINGS and (not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 10**9):
            raise HTTPException(400, f"{k} must be a non-negative integer")
    return logic.save_settings(ctx.workspace_id, values)


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
    return _ai_guard(ai.extract_views, cfg, text[:8000], images, accounts_, date.today())


@app.post("/api/ai/score/{pid}")
def ai_score(pid: int, apply: bool = True, ctx: Ctx = Depends(current)):
    ws = ctx.workspace_id
    p = _get_post(ws, pid)
    acc = db.one("SELECT * FROM accounts WHERE id=%s AND workspace_id=%s", (p["account_id"], ws)) if p["account_id"] else None
    examples = db.query(
        "SELECT hook, views, comments, intent FROM posts WHERE workspace_id=%s AND status='posted' AND hook<>'' "
        "AND id<>%s ORDER BY post_date DESC LIMIT 12", (ws, pid))
    res = _ai_guard(ai.score_post, ai.resolve(ws), p, acc, examples)
    if apply:
        db.execute("UPDATE posts SET v=%s, s=%s, c=%s, score_notes=%s, score_by='ai' WHERE id=%s AND workspace_id=%s",
                   (res["v"], res["s"], res["c"], res["notes"], pid, ws))
        return _get_post(ws, pid)
    return res


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
    winners = db.query("SELECT hook, views FROM posts WHERE workspace_id=%s AND status='posted' AND hook<>'' "
                       "ORDER BY views DESC LIMIT 8", (ws,))
    losers = db.query("SELECT hook, views FROM posts WHERE workspace_id=%s AND status='posted' AND hook<>'' "
                      "ORDER BY views ASC LIMIT 5", (ws,))
    return {"ideas": _ai_guard(ai.generate_hooks, ai.resolve(ws), acc, winners, losers, b.n, b.brief)}


class PlanIn(BaseModel):
    start: Optional[date] = None
    days: int = Field(7, ge=1, le=14)


@app.post("/api/ai/plan-week")
def ai_plan(b: PlanIn, ctx: Ctx = Depends(current)):
    ws = ctx.workspace_id
    start = b.start or logic.monday(date.today())
    accs = db.query("SELECT * FROM accounts WHERE workspace_id=%s ORDER BY handle", (ws,))
    winners = db.query(
        "SELECT p.hook, p.views, a.handle FROM posts p LEFT JOIN accounts a ON a.id=p.account_id "
        "WHERE p.workspace_id=%s AND p.status='posted' AND p.hook<>'' ORDER BY p.views DESC LIMIT 8", (ws,))
    raw = _ai_guard(ai.plan_week, ai.resolve(ws), start, accs, winners, b.days)
    out = []
    for item in raw:
        acc = ai.match_account(item.get("account", ""), accs)
        try:
            day = date.fromisoformat(str(item.get("day", ""))[:10])
        except ValueError:
            continue
        if acc and start <= day < start + timedelta(days=b.days):
            out.append({"account_id": acc["id"], "handle": acc["handle"], "post_date": day.isoformat(),
                        "status": "planned", **{k: str(item.get(k, ""))[:500] for k in ("ai_prompt", "hook", "body", "caption")}})
    return {"posts": out}


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


@app.post("/api/ai/ask")
def ai_ask(b: AskIn, ctx: Ctx = Depends(current)):
    ws = ctx.workspace_id
    this_week = logic.week_grid(ws, logic.monday(date.today()))
    recent = posts(limit=80, ctx=ctx)
    snapshot = {
        "today": date.today().isoformat(),
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
    return {"answer": _ai_guard(ai.ask, ai.resolve(ws), b.question, snapshot)}


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
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/", StaticFiles(directory=STATIC), name="static")
