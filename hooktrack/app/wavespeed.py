"""WaveSpeedAI image and video generation for the Studio editor."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import date
from urllib.parse import urlparse
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from . import billing, db, media
from .auth import Ctx, current

log = logging.getLogger("hooktrack.wavespeed")
router = APIRouter(prefix="/api/wavespeed", tags=["wavespeed"])

BASE_URL = "https://api.wavespeed.ai/api/v3"
TIMEOUT = 45.0
IMAGE_MODELS = {
    "wavespeed-ai/flux-dev",
    "wavespeed-ai/flux-schnell",
}
VIDEO_MODELS = {
    "wavespeed-ai/wan-2.1/t2v-480p",
    "wavespeed-ai/wan-2.1/i2v-480p",
}
PREDICTION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class WaveSpeedError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def api_key() -> str:
    return (os.environ.get("WAVESPEED_API_KEY") or os.environ.get("HOOKTRACK_WAVESPEED_API_KEY") or "").strip()


def status(workspace_id: int) -> dict:
    return {"configured": bool(api_key()), "ai_quota": billing.ai_quota(workspace_id)}


def _headers() -> dict:
    key = api_key()
    if not key:
        raise WaveSpeedError(
            "WaveSpeedAI is not configured. Set WAVESPEED_API_KEY on the server and restart the app.", 503)
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _unwrap(payload: dict) -> dict:
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    return data if isinstance(data, dict) else {}


def _provider_error(response: httpx.Response) -> WaveSpeedError:
    try:
        body = response.json()
    except ValueError:
        body = {}
    message = body.get("message") or body.get("error") if isinstance(body, dict) else ""
    if not message:
        message = response.text[:300] or "No error details were returned."
    return WaveSpeedError(f"WaveSpeedAI request failed ({response.status_code}): {str(message)[:300]}",
                          response.status_code)


def _output_urls(outputs: object) -> list[str]:
    if not isinstance(outputs, list):
        return []
    urls = []
    for output in outputs:
        parsed = urlparse(output) if isinstance(output, str) else None
        if (parsed and parsed.scheme == "https" and parsed.hostname
                and not parsed.username and not parsed.password):
            urls.append(output)
    return urls


def submit_generation(workspace_id: int, post_id: Optional[int], asset: dict,
                      reserved_month: Optional[date] = None) -> dict:
    asset_type = asset.get("type")
    if asset_type not in ("image", "video"):
        raise WaveSpeedError("WaveSpeedAI Studio generation supports images and videos.")
    prompt = asset.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
        raise WaveSpeedError("Enter a prompt of 1–4,000 characters.")
    model = asset.get("model") or (
        "wavespeed-ai/flux-dev" if asset_type == "image" else "wavespeed-ai/wan-2.1/t2v-480p")
    allowed = IMAGE_MODELS if asset_type == "image" else VIDEO_MODELS
    if model not in allowed:
        raise WaveSpeedError("Choose a supported WaveSpeedAI model for this asset type.")
    body = {"prompt": prompt.strip()}
    if asset_type == "image":
        num_images = asset.get("num_images", 1)
        if isinstance(num_images, bool) or not isinstance(num_images, int) or num_images not in (1, 2, 3, 4):
            raise WaveSpeedError("Choose between 1 and 4 image variations.", 400)
        body.update({"size": "1024*1024", "num_images": num_images, "output_format": "jpeg"})
    else:
        duration = asset.get("duration", 5)
        if isinstance(duration, bool) or not isinstance(duration, int) or duration not in (5, 10):
            raise WaveSpeedError("Video duration must be 5 or 10 seconds.", 400)
        size = asset.get("size", "832*480")
        if size not in ("832*480", "480*832"):
            raise WaveSpeedError("Choose landscape (16:9) or portrait (9:16) video.", 400)
        negative_prompt = asset.get("negative_prompt", "")
        if not isinstance(negative_prompt, str) or len(negative_prompt) > 1000:
            raise WaveSpeedError("Negative prompt must be 1,000 characters or fewer.", 400)
        body.update({"size": size, "duration": duration})
        if negative_prompt.strip():
            body["negative_prompt"] = negative_prompt.strip()
        if model == "wavespeed-ai/wan-2.1/i2v-480p":
            image_url = asset.get("image_url")
            parsed_image_url = urlparse(image_url) if isinstance(image_url, str) else None
            if (not parsed_image_url or parsed_image_url.scheme != "https" or not parsed_image_url.hostname
                    or parsed_image_url.username or parsed_image_url.password):
                raise WaveSpeedError("Upload a source image before generating an image-to-video clip.", 400)
            body["image"] = image_url

    if reserved_month is None:
        reserved_month = billing.reserve_ai(workspace_id)
    try:
        response = httpx.post(f"{BASE_URL}/{model}", json=body, headers=_headers(), timeout=TIMEOUT)
    except WaveSpeedError:
        if reserved_month is not None:
            billing.release_ai(workspace_id, reserved_month)
        raise
    except httpx.HTTPError as error:
        raise WaveSpeedError(f"Could not reach WaveSpeedAI: {error}")
    if response.status_code >= 400:
        if reserved_month is not None:
            billing.release_ai(workspace_id, reserved_month)
        raise _provider_error(response)
    task = _unwrap(response.json() or {})
    prediction_id = task.get("id")
    if not isinstance(prediction_id, (str, int)) or not str(prediction_id):
        raise WaveSpeedError("WaveSpeedAI did not return a prediction id.")
    prediction_id = str(prediction_id)
    if not PREDICTION_ID_RE.fullmatch(prediction_id):
        raise WaveSpeedError("WaveSpeedAI returned an invalid prediction id.")
    provider_status = str(task.get("status") or "created").lower()
    output_urls = _output_urls(task.get("outputs"))
    output_url = output_urls[0] if output_urls else ""
    row = db.one(
        """INSERT INTO wavespeed_generations
               (workspace_id, post_id, prediction_id, model, asset_type, prompt, status, output_url, error)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
        (workspace_id, post_id, prediction_id, model, asset_type, prompt.strip(), provider_status,
         output_url, str(task.get("error") or task.get("message") or "")[:500]))
    if reserved_month is None:
        billing.record_ai(workspace_id)
    return {**row, "output_urls": output_urls}


async def _upload_image_to_wavespeed(filename: str, content_type: str, content: bytes) -> str:
    """Upload a private Studio image using WaveSpeed's short-lived direct-upload ticket."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            ticket_response = await client.post(
                f"{BASE_URL}/media/uploads",
                json={"filename": filename, "size": len(content), "content_type": content_type},
                headers=_headers(),
            )
            if ticket_response.status_code >= 400:
                raise _provider_error(ticket_response)
            ticket = _unwrap(ticket_response.json() or {})
            upload = ticket.get("upload")
            download_url = ticket.get("download_url")
            upload_url = upload.get("url") if isinstance(upload, dict) else None
            upload_headers = upload.get("headers") if isinstance(upload, dict) else None
            parsed_upload_url = urlparse(upload_url) if isinstance(upload_url, str) else None
            parsed_download_url = urlparse(download_url) if isinstance(download_url, str) else None
            if (not isinstance(upload, dict) or upload.get("method") != "PUT"
                    or not parsed_upload_url or parsed_upload_url.scheme != "https" or not parsed_upload_url.hostname
                    or parsed_upload_url.username or parsed_upload_url.password
                    or not parsed_download_url or parsed_download_url.scheme != "https" or not parsed_download_url.hostname
                    or parsed_download_url.username or parsed_download_url.password
                    or not isinstance(upload_headers, dict)
                    or not all(isinstance(k, str) and isinstance(v, str) for k, v in upload_headers.items())):
                raise WaveSpeedError("WaveSpeedAI returned an invalid image upload ticket.")
            safe_upload_headers = {key: value for key, value in upload_headers.items()
                                   if key.lower() != "authorization"}
            uploaded = await client.put(upload_url, content=content, headers=safe_upload_headers)
            if uploaded.status_code < 200 or uploaded.status_code >= 300:
                raise WaveSpeedError(f"WaveSpeedAI image upload failed ({uploaded.status_code}).",
                                     uploaded.status_code)
            return download_url
    except WaveSpeedError:
        raise
    except httpx.HTTPError:
        raise WaveSpeedError("Could not upload the source image to WaveSpeedAI.")


def _fetch_prediction(prediction_id: str) -> dict:
    if not PREDICTION_ID_RE.fullmatch(prediction_id):
        raise WaveSpeedError("Invalid WaveSpeedAI prediction id.", 400)
    try:
        response = httpx.get(
            f"{BASE_URL}/predictions/{prediction_id}/result", headers=_headers(), timeout=TIMEOUT)
    except WaveSpeedError:
        raise
    except httpx.HTTPError as error:
        raise WaveSpeedError(f"Could not check WaveSpeedAI generation: {error}")
    if response.status_code >= 400:
        raise _provider_error(response)
    return _unwrap(response.json() or {})


def sync_generation(workspace_id: int, prediction_id: str) -> dict:
    row = db.one(
        "SELECT * FROM wavespeed_generations WHERE prediction_id=%s AND workspace_id=%s",
        (prediction_id, workspace_id))
    if not row:
        raise WaveSpeedError("Unknown generation id for this workspace.", 404)
    task = _fetch_prediction(prediction_id)
    provider_status = str(task.get("status") or row["status"]).lower()
    output_urls = _output_urls(task.get("outputs"))
    output_url = output_urls[0] if output_urls else ""
    error = str(task.get("error") or task.get("message") or "")[:500]
    if provider_status == "completed" and not output_url:
        provider_status = "failed"
        error = error or "WaveSpeedAI completed the task without returning an output URL."
    updated = db.one(
        """UPDATE wavespeed_generations
              SET status=%s, output_url=%s, error=%s, updated_at=now()
            WHERE prediction_id=%s AND workspace_id=%s RETURNING *""",
        (provider_status, output_url, error, prediction_id, workspace_id))
    return {**updated, "output_urls": output_urls}


class GenerateIn(BaseModel):
    asset: dict = Field(..., description="{type: image|video, prompt, model?}")
    post_id: Optional[int] = None


@router.get("/status")
def wavespeed_status(ctx: Ctx = Depends(current)):
    return status(ctx.workspace_id)


@router.post("/generate")
def generate_asset(body: GenerateIn, ctx: Ctx = Depends(current)):
    if body.post_id is not None and not db.one(
            "SELECT 1 FROM posts WHERE id=%s AND workspace_id=%s", (body.post_id, ctx.workspace_id)):
        raise HTTPException(404, "Post not found")
    if body.asset.get("model") == "wavespeed-ai/wan-2.1/i2v-480p":
        raise HTTPException(400, "Image-to-video requires a source image upload through Studio.")
    try:
        return submit_generation(ctx.workspace_id, body.post_id, body.asset)
    except WaveSpeedError as error:
        raise HTTPException(error.status if error.status in (400, 401, 402, 403, 404, 413, 415, 422, 429, 503) else 502,
                            str(error))


@router.post("/generate-image-to-video")
async def generate_image_to_video(
        prompt: str = Form(..., min_length=1, max_length=4000),
        negative_prompt: str = Form("", max_length=1000),
        duration: int = Form(5),
        size: str = Form("832*480"),
        post_id: int = Form(...),
        file: UploadFile = File(...),
        ctx: Ctx = Depends(current)):
    if not db.one("SELECT 1 FROM posts WHERE id=%s AND workspace_id=%s", (post_id, ctx.workspace_id)):
        raise HTTPException(404, "Post not found")
    if not prompt.strip():
        raise HTTPException(400, "Enter a prompt of 1–4,000 characters.")
    if duration not in (5, 10):
        raise HTTPException(400, "Video duration must be 5 or 10 seconds.")
    if size not in ("832*480", "480*832"):
        raise HTTPException(400, "Choose landscape (16:9) or portrait (9:16) video.")
    reserved_month = billing.reserve_ai(ctx.workspace_id)
    limit = media.max_image_bytes()
    try:
        content = await file.read(limit + 1)
        if len(content) > limit:
            raise HTTPException(413, f"That image is too large. The limit is {limit // (1024 * 1024)} MB.")
        image_kind = media.sniff_image(content[:16])
        if not image_kind:
            raise HTTPException(415, "The source must be a JPEG, PNG, or WebP image.")
        content_type = media.IMAGE_TYPES[image_kind]
        filename = f"studio-source.{image_kind}"
        image_url = await _upload_image_to_wavespeed(filename, content_type, content)
    except Exception:
        if reserved_month is not None:
            billing.release_ai(ctx.workspace_id, reserved_month)
        raise
    try:
        return await asyncio.to_thread(
            submit_generation, ctx.workspace_id, post_id,
            {"type": "video", "prompt": prompt, "model": "wavespeed-ai/wan-2.1/i2v-480p",
             "image_url": image_url, "negative_prompt": negative_prompt,
             "duration": duration, "size": size}, reserved_month)
    except WaveSpeedError as error:
        raise HTTPException(error.status if error.status in (400, 401, 402, 403, 404, 422, 429, 503) else 502,
                            str(error))


@router.get("/generate/{prediction_id}")
def generate_status(prediction_id: str, ctx: Ctx = Depends(current)):
    try:
        return sync_generation(ctx.workspace_id, prediction_id)
    except WaveSpeedError as error:
        raise HTTPException(error.status or 502, str(error))
