"""HookTrack's own Shotstack account, for the in-app video/photo editor (Studio). A platform-level credential
shared by every workspace and metered like AI/SociaVault usage, not a per-workspace BYOK add-on: a user never
needs their own Shotstack account to use the editor.

The editor itself (Shotstack Studio) runs entirely in the browser and talks to Shotstack directly for everything
except the final cloud render - this module is only the server side of that one step: submit an already-built
Edit JSON for rendering, check a render's status, and verify a render's completion webhook.

Plain REST via httpx, the same pattern every other provider in publish.py uses - not the official Python SDK.
That SDK's Edit model requires fully-typed nested objects (Edit(timeline=Timeline(...), ...)); the Edit JSON
we submit is already built by Studio in the browser, so round-tripping it through typed Python objects just to
serialize it straight back to JSON adds risk (the asset field is a union type the SDK doesn't document
dict-construction for) for no benefit.
"""
from __future__ import annotations

import copy
import logging
import os
import re
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import crypto, db
from .auth import Ctx, current
from .sociavault import platform_delete, platform_get, platform_set

log = logging.getLogger("hooktrack.shotstack")
router = APIRouter(prefix="/api/shotstack", tags=["shotstack"])

# Studio builds asset URLs as same-origin, cookie-authenticated routes - the browser can fetch those
# (Phase 4/6's free export relies on exactly that), but Shotstack's cloud render servers have no cookie
# of ours and, in dev, no path to our loopback address at all either way. Before a cloud render these get
# swapped for their signed, unauthenticated equivalents (the same ones publish.py already uses for other
# providers) - matched by path alone, so it doesn't matter what origin the browser happened to use.
_SELF_VIDEO_RE = re.compile(r"/api/posts/(\d+)/video/file(?:\.\w+)?(?:\?.*)?$")
_SELF_IMAGE_RE = re.compile(r"/api/posts/(\d+)/images/(\d+)/file(?:\.\w+)?(?:\?.*)?$")
_SELF_AUDIO_RE = re.compile(r"/api/posts/(\d+)/studio-audio/(\d+)/file(?:\.\w+)?(?:\?.*)?$")


def _rewrite_src(workspace_id: int, src: str) -> str:
    if not isinstance(src, str):
        return src
    from . import media
    m = _SELF_VIDEO_RE.search(src)
    if m:
        return media.signed_url(workspace_id, int(m.group(1)))
    m = _SELF_IMAGE_RE.search(src)
    if m:
        return media.signed_image_url_by_id(workspace_id, int(m.group(1)), int(m.group(2)))
    m = _SELF_AUDIO_RE.search(src)
    if m:
        return media.signed_audio_url_by_id(workspace_id, int(m.group(1)), int(m.group(2)))
    return src                                                   # not one of ours - leave untouched


def _rewrite_edit_for_cloud(workspace_id: int, edit_json: dict) -> dict:
    out = copy.deepcopy(edit_json)
    for track in (out.get("timeline") or {}).get("tracks") or []:
        for clip in track.get("clips") or []:
            asset = clip.get("asset") or {}
            if isinstance(asset.get("src"), str):
                try:
                    asset["src"] = _rewrite_src(workspace_id, asset["src"])
                except ValueError as e:
                    raise ShotstackError(str(e))
    return out

KEY_ENC, KEY_HINT, ENV_KEY = "shotstack_api_key_enc", "shotstack_key_hint", "shotstack_env"
# Confirmed against Shotstack's own docs (request-api-keys page): sandbox and production are different hosts,
# with different keys from the same dashboard - never the same key against both.
HOSTS = {"stage": "https://api.shotstack.io/edit/stage", "v1": "https://api.shotstack.io/edit/v1"}
TIMEOUT = 30.0


class ShotstackError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def environment() -> str:
    e = platform_get(ENV_KEY) or "stage"
    return e if e in HOSTS else "stage"


def base_url() -> str:
    return HOSTS[environment()]


def platform_key() -> str:
    enc = platform_get(KEY_ENC)
    if isinstance(enc, str) and enc:
        try:
            return (crypto.decrypt(enc) or "").strip()
        except Exception:
            return ""
    return (os.environ.get("HOOKTRACK_SHOTSTACK_API_KEY") or "").strip()


def key_status() -> dict:
    hint = platform_get(KEY_HINT) or ""
    has_db = bool(platform_get(KEY_ENC))
    has_env = bool((os.environ.get("HOOKTRACK_SHOTSTACK_API_KEY") or "").strip())
    return {"configured": has_db or has_env, "environment": environment(),
            "source": "platform" if has_db else ("env" if has_env else "none"),
            "key_hint": hint if has_db else ("from env" if has_env else "")}


def save_key(api_key: str, env: str = "stage", clear: bool = False) -> dict:
    if clear or not (api_key or "").strip():
        platform_delete(KEY_ENC)
        platform_delete(KEY_HINT)
        return key_status()
    k = api_key.strip()
    platform_set(KEY_ENC, crypto.encrypt(k))
    platform_set(KEY_HINT, (k[:4] + "..." + k[-4:]) if len(k) > 8 else "****")
    platform_set(ENV_KEY, env if env in HOSTS else "stage")
    return key_status()


def available() -> bool:
    return bool(platform_key())


def _headers() -> dict:
    return {"x-api-key": platform_key(), "Content-Type": "application/json"}


def submit_render(workspace_id: int, edit_json: dict, post_id: Optional[int] = None,
                  callback_url: str = "") -> dict:
    """Submit an Edit JSON (already built by Studio in the browser) for cloud rendering. Returns our own
    shotstack_renders row, not Shotstack's raw response."""
    if not available():
        raise ShotstackError("Video editing is not set up on this server yet.")
    body = _rewrite_edit_for_cloud(workspace_id, edit_json)
    if callback_url:
        body["callback"] = callback_url
    with httpx.Client(timeout=TIMEOUT) as client:
        try:
            r = client.post(f"{base_url()}/render", json=body, headers=_headers())
        except httpx.HTTPError as e:
            raise ShotstackError(f"Could not reach Shotstack: {e}")
    if r.status_code >= 400:
        raise ShotstackError(f"Shotstack render request failed ({r.status_code}): {r.text[:300]}", r.status_code)
    data = (r.json() or {}).get("response") or {}
    render_id = data.get("id") or ""
    if not render_id:
        raise ShotstackError(f"Shotstack did not return a render id: {r.text[:300]}")
    row = db.one(
        """INSERT INTO shotstack_renders (workspace_id, post_id, render_id, environment, edit_json, status)
           VALUES (%s,%s,%s,%s,%s,'queued') RETURNING *""",
        (workspace_id, post_id, render_id, environment(), db.Json(body)))
    return row


def _fetch_status(render_id: str) -> dict:
    """Always talks to Shotstack directly - this is the one source of truth. A render's own webhook is never
    trusted on its own (Shotstack: "we do not currently provide signed payloads"), so both the webhook handler
    and polling end up calling this same function rather than reading the webhook body's claims."""
    if not available():
        raise ShotstackError("Video editing is not set up on this server yet.")
    with httpx.Client(timeout=TIMEOUT) as client:
        try:
            r = client.get(f"{base_url()}/render/{render_id}", headers=_headers())
        except httpx.HTTPError as e:
            raise ShotstackError(f"Could not reach Shotstack: {e}")
    if r.status_code >= 400:
        raise ShotstackError(f"Could not check render status ({r.status_code}): {r.text[:300]}", r.status_code)
    return (r.json() or {}).get("response") or {}


def sync_render(workspace_id: int, render_id: str) -> dict:
    """Re-fetch a render's real status from Shotstack and update our row. The only path that ever writes
    shotstack_renders.status - called from polling and from the (unsigned, so re-verified) webhook alike."""
    row = db.one("SELECT * FROM shotstack_renders WHERE render_id=%s AND workspace_id=%s", (render_id, workspace_id))
    if not row:
        raise ShotstackError("Unknown render id for this workspace", 404)
    data = _fetch_status(render_id)
    row = db.one(
        """UPDATE shotstack_renders SET status=%s, output_url=%s, error=%s, updated_at=now()
           WHERE render_id=%s AND workspace_id=%s RETURNING *""",
        (data.get("status") or row["status"], data.get("url") or "", data.get("error") or "",
         render_id, workspace_id))
    return row


def _callback_url() -> str:
    from . import media
    base = media.public_base()
    return f"{base}/api/shotstack/webhook" if base and not media._is_private_host(base) else ""


# ---------- asset generation (Studio's own "Generate" toolbar - one prompt-bearing image/video/audio
# clip, not a full render; registered from the browser via edit.registerAssetGenerator) ----------

def _unwrap(body: dict) -> dict:
    """Shotstack wraps /render responses in {"response": {...}}; be tolerant either way for /generate."""
    return body.get("response", body) if isinstance(body, dict) else {}


def submit_generation(workspace_id: int, asset: dict, length: Optional[float] = None,
                      post_id: Optional[int] = None) -> dict:
    """POST /generate: generate one prompt-bearing asset. Shotstack answers either immediately (200, cached
    or fast models) or queued (202, poll /generate/{id}) - same GenerationResponse body either way."""
    if not available():
        raise ShotstackError("Video editing is not set up on this server yet.")
    body: dict = {"asset": asset}
    if length:
        body["length"] = length
    with httpx.Client(timeout=TIMEOUT) as client:
        try:
            r = client.post(f"{base_url()}/generate", json=body, headers=_headers())
        except httpx.HTTPError as e:
            raise ShotstackError(f"Could not reach Shotstack: {e}")
    if r.status_code == 402:
        raise ShotstackError("Your Shotstack account doesn't have enough generation credits for this.", 402)
    if r.status_code >= 400:
        raise ShotstackError(f"Shotstack generation request failed ({r.status_code}): {r.text[:300]}", r.status_code)
    data = _unwrap(r.json() or {})
    generation_id = data.get("id") or ""
    if not generation_id:
        raise ShotstackError(f"Shotstack did not return a generation id: {r.text[:300]}")
    row = db.one(
        """INSERT INTO shotstack_generations (workspace_id, post_id, generation_id, environment, asset_json,
                                               status, output_url, error)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
        (workspace_id, post_id, generation_id, environment(), db.Json(body),
         data.get("status") or "queued", data.get("url") or "", data.get("error") or ""))
    return row


def _fetch_generation_status(generation_id: str) -> dict:
    if not available():
        raise ShotstackError("Video editing is not set up on this server yet.")
    with httpx.Client(timeout=TIMEOUT) as client:
        try:
            r = client.get(f"{base_url()}/generate/{generation_id}", headers=_headers())
        except httpx.HTTPError as e:
            raise ShotstackError(f"Could not reach Shotstack: {e}")
    if r.status_code >= 400:
        raise ShotstackError(f"Could not check generation status ({r.status_code}): {r.text[:300]}", r.status_code)
    return _unwrap(r.json() or {})


def sync_generation(workspace_id: int, generation_id: str) -> dict:
    row = db.one("SELECT * FROM shotstack_generations WHERE generation_id=%s AND workspace_id=%s",
                 (generation_id, workspace_id))
    if not row:
        raise ShotstackError("Unknown generation id for this workspace", 404)
    data = _fetch_generation_status(generation_id)
    row = db.one(
        """UPDATE shotstack_generations SET status=%s, output_url=%s, error=%s, updated_at=now()
           WHERE generation_id=%s AND workspace_id=%s RETURNING *""",
        (data.get("status") or row["status"], data.get("url") or "", data.get("error") or "",
         generation_id, workspace_id))
    return row


def generation_quote(asset: dict, length: Optional[float] = None) -> dict:
    """POST /generate/quote: an estimate only - no credits spent, no job created."""
    if not available():
        raise ShotstackError("Video editing is not set up on this server yet.")
    body: dict = {"asset": asset}
    if length:
        body["length"] = length
    with httpx.Client(timeout=TIMEOUT) as client:
        try:
            r = client.post(f"{base_url()}/generate/quote", json=body, headers=_headers())
        except httpx.HTTPError as e:
            raise ShotstackError(f"Could not reach Shotstack: {e}")
    if r.status_code >= 400:
        raise ShotstackError(f"Could not get a quote ({r.status_code}): {r.text[:300]}", r.status_code)
    return _unwrap(r.json() or {})


# ---------- routes ----------

class RenderIn(BaseModel):
    edit: dict = Field(..., description="The Edit JSON produced by Studio in the browser")
    post_id: Optional[int] = None


@router.get("/status")
def shotstack_status(ctx: Ctx = Depends(current)):
    """Whether the editor is usable at all (for the frontend to show/hide the 'Edit in Studio' entry point)."""
    return key_status()


@router.post("/render")
def render_post(b: RenderIn, ctx: Ctx = Depends(current)):
    if b.post_id is not None and not db.one(
            "SELECT 1 FROM posts WHERE id=%s AND workspace_id=%s", (b.post_id, ctx.workspace_id)):
        raise HTTPException(404, "Post not found")
    try:
        row = submit_render(ctx.workspace_id, b.edit, post_id=b.post_id, callback_url=_callback_url())
    except ShotstackError as e:
        raise HTTPException(e.status if e.status in (400, 401, 403, 404) else 502, str(e))
    return row


@router.get("/render/{render_id}")
def render_status(render_id: str, ctx: Ctx = Depends(current)):
    try:
        return sync_render(ctx.workspace_id, render_id)
    except ShotstackError as e:
        raise HTTPException(e.status or 502, str(e))


class GenerateIn(BaseModel):
    asset: dict = Field(..., description="{type, prompt, model?, options?} - the prompt-bearing asset Studio's "
                                          "own 'Generate' toolbar built")
    length: Optional[float] = None
    post_id: Optional[int] = None


class GenerateQuoteIn(BaseModel):
    asset: dict
    length: Optional[float] = None


@router.post("/generate")
def generate_asset(b: GenerateIn, ctx: Ctx = Depends(current)):
    if b.post_id is not None and not db.one(
            "SELECT 1 FROM posts WHERE id=%s AND workspace_id=%s", (b.post_id, ctx.workspace_id)):
        raise HTTPException(404, "Post not found")
    try:
        row = submit_generation(ctx.workspace_id, b.asset, length=b.length, post_id=b.post_id)
    except ShotstackError as e:
        raise HTTPException(e.status if e.status in (400, 401, 402, 403, 404) else 502, str(e))
    return row


@router.get("/generate/{generation_id}")
def generate_status(generation_id: str, ctx: Ctx = Depends(current)):
    try:
        return sync_generation(ctx.workspace_id, generation_id)
    except ShotstackError as e:
        raise HTTPException(e.status or 502, str(e))


@router.post("/generate/quote")
def generate_quote(b: GenerateQuoteIn, ctx: Ctx = Depends(current)):
    try:
        return generation_quote(b.asset, length=b.length)
    except ShotstackError as e:
        raise HTTPException(e.status if e.status in (400, 401, 403, 404) else 502, str(e))


@router.post("/webhook")
async def shotstack_webhook(request: Request):
    """Shotstack does not sign these ("we do not currently provide signed payloads"), so the body is never
    trusted for its claims - only its render id is used, to look up which workspace owns it, then the real
    status is re-fetched straight from Shotstack's own API before anything is written."""
    try:
        event = await request.json()
    except ValueError:
        raise HTTPException(400, "Bad body")
    render_id = str(event.get("id") or "")
    if not render_id:
        raise HTTPException(400, "No render id in webhook body")
    row = db.one("SELECT workspace_id FROM shotstack_renders WHERE render_id=%s", (render_id,))
    if not row:
        return {"ok": True}  # unknown render (not ours, or already deleted): nothing to do, not an error
    try:
        sync_render(row["workspace_id"], render_id)
    except ShotstackError:
        log.exception("shotstack webhook: could not re-verify render %s", render_id)
    return {"ok": True}
