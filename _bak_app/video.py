"""Optional video-generator add-on (BYOK).

Providers:
  - runway: AI text-to-video (Runway ML gen3a_turbo / gen4)
  - luma: AI text-to-video (Luma Dream Machine)
  - replicate: AI via Replicate (configurable model, default Kling / similar)
  - custom_webhook: POST script payload to user URL
  - shotstack: timeline text-overlay video
  - creatomate: template render
  - mock: instant placeholder (dev)

Jobs are async: create → pending/running → done|failed. Poll via API or background loop.
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from . import crypto, db, leases

PROVIDERS = {
    "runway": {
        "label": "Runway (AI video)",
        "needs_key": True,
        "needs_url": False,
        "ai": True,
        "hint": "API key from dev.runwayml.com. Text-to-video from your hook/body script (gen3a_turbo by default).",
    },
    "luma": {
        "label": "Luma Dream Machine (AI video)",
        "needs_key": True,
        "needs_url": False,
        "ai": True,
        "hint": "API key from lumalabs.ai. Generates video from the post script prompt.",
    },
    "replicate": {
        "label": "Replicate (AI video)",
        "needs_key": True,
        "needs_url": False,
        "ai": True,
        "hint": "API token from replicate.com. Default model: kwaivgi/kling-v1.6-standard (override in config).",
    },
    "custom_webhook": {
        "label": "Custom webhook",
        "needs_key": False,
        "needs_url": True,
        "hint": "Your service receives POST {hook, body, caption, …} and returns {job_id, status} or {video_url}.",
    },
    "shotstack": {
        "label": "Shotstack",
        "needs_key": True,
        "needs_url": False,
        "hint": "API key from shotstack.io. Builds a simple text-overlay video from hook + body.",
    },
    "creatomate": {
        "label": "Creatomate",
        "needs_key": True,
        "needs_url": False,
        "hint": "API key + template ID in config. Maps hook/body/caption into template modifications.",
    },
    "mock": {
        "label": "Mock (dev)",
        "needs_key": False,
        "needs_url": False,
        "hint": "Returns a placeholder video URL immediately — no external API.",
    },
}


def _ai_prompt(payload: dict, conf: dict) -> str:
    """Build a short-form video generation prompt from the post script."""
    custom = (conf.get("prompt_template") or "").strip()
    hook = (payload.get("hook") or "").strip()
    body = (payload.get("body") or "").strip()
    caption = (payload.get("caption") or "").strip()
    acc = payload.get("account") or {}
    niche = (acc.get("niche") or "").strip()
    style = (conf.get("style") or "vertical 9:16 short-form social video, high energy, clean, mobile-first").strip()
    if custom:
        return (
            custom.replace("{hook}", hook)
            .replace("{body}", body)
            .replace("{caption}", caption)
            .replace("{niche}", niche)
            .replace("{style}", style)
        )[:2000]
    parts = []
    if niche:
        parts.append(f"Niche: {niche}.")
    if hook:
        parts.append(f"Opening hook on screen: {hook}.")
    if body:
        parts.append(f"Continue with: {body}.")
    if caption and not body:
        parts.append(f"Theme: {caption[:200]}.")
    parts.append(f"Style: {style}. No watermarks, no unreadable text, cinematic lighting.")
    return " ".join(parts)[:2000]


class VideoError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_config(workspace_id: int) -> dict:
    row = db.one(
        "SELECT * FROM workspace_addons WHERE workspace_id=%s AND addon_id='video'",
        (workspace_id,),
    )
    if not row:
        return {
            "enabled": False,
            "provider": "custom_webhook",
            "has_secret": False,
            "key_hint": "",
            "config": {},
            "providers": PROVIDERS,
            "source": "none",
        }
    cfg = row.get("config_json") or {}
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except json.JSONDecodeError:
            cfg = {}
    return {
        "enabled": bool(row.get("enabled")),
        "provider": row.get("provider") or "custom_webhook",
        "has_secret": bool(row.get("secrets_enc")),
        "key_hint": row.get("key_hint") or "",
        "config": cfg,
        "providers": PROVIDERS,
        "source": "workspace",
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


def save_config(
    workspace_id: int,
    *,
    enabled: bool,
    provider: str,
    api_key: Optional[str] = None,
    clear_key: bool = False,
    config: Optional[dict] = None,
) -> dict:
    if provider not in PROVIDERS:
        raise VideoError(f"Unknown provider '{provider}'")
    existing = db.one(
        "SELECT secrets_enc, key_hint, config_json FROM workspace_addons WHERE workspace_id=%s AND addon_id='video'",
        (workspace_id,),
    )
    secrets_enc = (existing or {}).get("secrets_enc") or ""
    key_hint = (existing or {}).get("key_hint") or ""
    if clear_key:
        secrets_enc, key_hint = "", ""
    elif api_key and api_key.strip():
        key = api_key.strip()
        secrets_enc = crypto.encrypt(key)
        key_hint = (key[:4] + "…" + key[-4:]) if len(key) > 8 else "••••"

    cfg = config if config is not None else ((existing or {}).get("config_json") or {})
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except json.JSONDecodeError:
            cfg = {}
    # sanitize URL if present
    if cfg.get("webhook_url"):
        cfg["webhook_url"] = _safe_url(str(cfg["webhook_url"]))
    if cfg.get("poll_url_template"):
        cfg["poll_url_template"] = str(cfg["poll_url_template"])[:500]

    db.execute(
        """INSERT INTO workspace_addons
           (workspace_id, addon_id, enabled, provider, config_json, secrets_enc, key_hint)
           VALUES (%s,'video',%s,%s,%s,%s,%s)
           ON CONFLICT (workspace_id, addon_id) DO UPDATE SET
             enabled=EXCLUDED.enabled, provider=EXCLUDED.provider,
             config_json=EXCLUDED.config_json, secrets_enc=EXCLUDED.secrets_enc,
             key_hint=EXCLUDED.key_hint, updated_at=now()""",
        (workspace_id, enabled, provider, db.Json(cfg), secrets_enc, key_hint),
    )
    return get_config(workspace_id)


def _resolve_secret(workspace_id: int) -> str:
    row = db.one(
        "SELECT secrets_enc FROM workspace_addons WHERE workspace_id=%s AND addon_id='video'",
        (workspace_id,),
    )
    if row and row.get("secrets_enc"):
        return crypto.decrypt(row["secrets_enc"]) or ""
    return (os.environ.get("HOOKTRACK_VIDEO_API_KEY") or "").strip()


def _safe_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise VideoError("URL must be http or https")
    host = (p.hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1") or host.startswith("10.") or host.startswith("192.168."):
        if os.environ.get("HOOKTRACK_ALLOW_PRIVATE_VIDEO_URLS") != "1":
            raise VideoError("Private/loopback webhook URLs are blocked (set HOOKTRACK_ALLOW_PRIVATE_VIDEO_URLS=1 to allow)")
    return url[:500]


def _script_payload(post: dict, account: Optional[dict]) -> dict:
    return {
        "post_id": post.get("id"),
        "hook": post.get("hook") or "",
        "body": post.get("body") or "",
        "caption": post.get("caption") or "",
        "ai_prompt": post.get("ai_prompt") or "",
        "post_date": str(post.get("post_date") or ""),
        "account": {
            "handle": (account or {}).get("handle") or "",
            "niche": (account or {}).get("niche") or "",
            "platform": (account or {}).get("platform") or "",
            "content_hook": (account or {}).get("content_hook") or "",
        },
    }


def start_job(workspace_id: int, post_id: int) -> dict:
    cfg = get_config(workspace_id)
    if not cfg["enabled"]:
        raise VideoError("Video generator add-on is not enabled")
    post = db.one("SELECT * FROM posts WHERE id=%s AND workspace_id=%s", (post_id, workspace_id))
    if not post:
        raise VideoError("Post not found")
    if not (post.get("hook") or post.get("body")):
        raise VideoError("Post needs a hook or body script before generating video")

    account = None
    if post.get("account_id"):
        account = db.one(
            "SELECT * FROM accounts WHERE id=%s AND workspace_id=%s",
            (post["account_id"], workspace_id),
        )

    provider = cfg["provider"]
    payload = _script_payload(post, account)
    job = db.one(
        """INSERT INTO video_jobs (workspace_id, post_id, provider, status, request_json)
           VALUES (%s,%s,%s,'pending',%s) RETURNING *""",
        (workspace_id, post_id, provider, db.Json(payload)),
    )
    db.execute(
        "UPDATE posts SET video_job_id=%s WHERE id=%s AND workspace_id=%s",
        (job["id"], post_id, workspace_id),
    )

    try:
        result = _dispatch(provider, workspace_id, cfg, payload, job["id"])
    except VideoError as e:
        _fail_job(job["id"], workspace_id, str(e))
        raise
    except Exception as e:
        _fail_job(job["id"], workspace_id, str(e)[:300])
        raise VideoError(str(e)[:300])

    return _apply_provider_result(job["id"], workspace_id, post_id, result)


def _fail_job(job_id: int, workspace_id: int, error: str):
    db.execute(
        """UPDATE video_jobs SET status='failed', error=%s, updated_at=now()
           WHERE id=%s AND workspace_id=%s""",
        (error[:500], job_id, workspace_id),
    )


def _apply_provider_result(job_id: int, workspace_id: int, post_id: int, result: dict) -> dict:
    status = result.get("status") or "running"
    external_id = result.get("external_id") or result.get("job_id") or ""
    video_url = result.get("video_url") or ""
    poll_url = result.get("poll_url") or ""
    error = result.get("error") or ""

    if status == "done" and video_url:
        db.execute(
            """UPDATE video_jobs SET status='done', external_id=%s, result_url=%s, poll_url=%s,
               error='', updated_at=now() WHERE id=%s AND workspace_id=%s""",
            (str(external_id)[:200], video_url[:1000], poll_url[:500], job_id, workspace_id),
        )
        db.execute(
            """UPDATE posts SET video_url=%s, status=CASE
                 WHEN status IN ('idea','scripted','planned') THEN 'filmed' ELSE status END
               WHERE id=%s AND workspace_id=%s""",
            (video_url[:1000], post_id, workspace_id),
        )
    elif status == "failed":
        db.execute(
            """UPDATE video_jobs SET status='failed', external_id=%s, error=%s, updated_at=now()
               WHERE id=%s AND workspace_id=%s""",
            (str(external_id)[:200], (error or "Provider failed")[:500], job_id, workspace_id),
        )
    else:
        db.execute(
            """UPDATE video_jobs SET status='running', external_id=%s, poll_url=%s, updated_at=now()
               WHERE id=%s AND workspace_id=%s""",
            (str(external_id)[:200], poll_url[:500], job_id, workspace_id),
        )
    return get_job(workspace_id, job_id)


def _dispatch(provider: str, workspace_id: int, cfg: dict, payload: dict, job_id: int) -> dict:
    secret = _resolve_secret(workspace_id)
    conf = cfg.get("config") or {}

    if provider == "mock":
        return {
            "status": "done",
            "video_url": f"https://example.com/mock-video/{job_id}.mp4",
            "external_id": f"mock-{job_id}",
        }

    if provider == "custom_webhook":
        url = _safe_url(conf.get("webhook_url") or "")
        if not url:
            raise VideoError("Configure webhook_url in the video add-on")
        headers = {"Content-Type": "application/json", "User-Agent": "HookTrack-Video/1.0"}
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
            headers["X-API-Key"] = secret
        body = {**payload, "job_id": job_id, "callback_hint": "poll via HookTrack job status"}
        with httpx.Client(timeout=60.0) as client:
            r = client.post(url, json=body, headers=headers)
        if r.status_code >= 400:
            raise VideoError(f"Webhook returned {r.status_code}: {r.text[:200]}", r.status_code)
        data = r.json() if r.content else {}
        if not isinstance(data, dict):
            data = {}
        # Accept several response shapes
        video_url = data.get("video_url") or data.get("url") or data.get("output")
        if video_url:
            return {"status": "done", "video_url": str(video_url), "external_id": str(data.get("id") or data.get("job_id") or "")}
        status = (data.get("status") or "running").lower()
        if status in ("done", "completed", "success"):
            if not video_url:
                raise VideoError("Webhook marked done but returned no video_url")
        poll = data.get("poll_url") or data.get("status_url") or ""
        return {
            "status": "running" if status not in ("failed", "error") else "failed",
            "external_id": str(data.get("job_id") or data.get("id") or ""),
            "poll_url": str(poll),
            "error": data.get("error") or "",
            "video_url": str(video_url or ""),
        }

    if provider == "shotstack":
        if not secret:
            raise VideoError("Shotstack API key required")
        env = (conf.get("shotstack_env") or "stage").lower()
        base = "https://api.shotstack.io/edit/v1" if env == "v1" or env == "prod" else "https://api.shotstack.io/stage/edit/v1"
        if env in ("v1", "prod", "production"):
            base = "https://api.shotstack.io/edit/v1"
        else:
            base = "https://api.shotstack.io/stage/edit/v1"
        hook = (payload.get("hook") or "Hook")[:80]
        body = (payload.get("body") or "")[:120]
        timeline = {
            "timeline": {
                "background": conf.get("background") or "#0f172a",
                "tracks": [
                    {
                        "clips": [
                            {
                                "asset": {
                                    "type": "title",
                                    "text": hook,
                                    "style": "minimal",
                                    "color": "#ffffff",
                                    "size": "large",
                                },
                                "start": 0,
                                "length": 3,
                                "transition": {"in": "fade", "out": "fade"},
                            }
                        ]
                    },
                    {
                        "clips": [
                            {
                                "asset": {
                                    "type": "title",
                                    "text": body or " ",
                                    "style": "subtitle",
                                    "color": "#e2e8f0",
                                    "size": "small",
                                },
                                "start": 2.5,
                                "length": 4,
                            }
                        ]
                    },
                ],
            },
            "output": {"format": "mp4", "resolution": conf.get("resolution") or "hd"},
        }
        with httpx.Client(timeout=60.0) as client:
            r = client.post(
                f"{base}/render",
                json=timeline,
                headers={"x-api-key": secret, "Content-Type": "application/json"},
            )
        if r.status_code >= 400:
            raise VideoError(f"Shotstack error {r.status_code}: {r.text[:200]}", r.status_code)
        data = r.json() if r.content else {}
        resp = data.get("response") or data
        rid = resp.get("id") or data.get("id")
        if not rid:
            raise VideoError("Shotstack did not return a render id")
        return {
            "status": "running",
            "external_id": str(rid),
            "poll_url": f"{base}/render/{rid}",
        }

    if provider == "creatomate":
        if not secret:
            raise VideoError("Creatomate API key required")
        template_id = (conf.get("template_id") or "").strip()
        if not template_id:
            raise VideoError("Set template_id in video add-on config")
        modifications: dict[str, Any] = {}
        # Common element names; users map in Creatomate template
        field_map = conf.get("field_map") or {
            "hook": "Hook",
            "body": "Body",
            "caption": "Caption",
        }
        for src, dest in field_map.items():
            if payload.get(src):
                modifications[str(dest)] = payload[src]
        body = {"template_id": template_id, "modifications": modifications}
        with httpx.Client(timeout=60.0) as client:
            r = client.post(
                "https://api.creatomate.com/v1/renders",
                json=body,
                headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"},
            )
        if r.status_code >= 400:
            raise VideoError(f"Creatomate error {r.status_code}: {r.text[:200]}", r.status_code)
        data = r.json() if r.content else {}
        # API may return list or object
        if isinstance(data, list) and data:
            data = data[0]
        rid = data.get("id")
        status = (data.get("status") or "planned").lower()
        url = data.get("url") or ""
        if status in ("succeeded", "done", "completed") and url:
            return {"status": "done", "video_url": url, "external_id": str(rid or "")}
        return {
            "status": "running",
            "external_id": str(rid or ""),
            "poll_url": f"https://api.creatomate.com/v1/renders/{rid}" if rid else "",
        }


    if provider == "runway":
        if not secret:
            raise VideoError("Runway API key required")
        prompt = _ai_prompt(payload, conf)
        model = (conf.get("model") or "gen3a_turbo").strip()
        duration = int(conf.get("duration") or 5)
        duration = max(5, min(10, duration))
        ratio = (conf.get("ratio") or "768:1280").strip()  # vertical default
        body = {
            "promptText": prompt,
            "model": model,
            "duration": duration,
            "ratio": ratio,
        }
        if conf.get("seed") is not None:
            try:
                body["seed"] = int(conf["seed"])
            except (TypeError, ValueError):
                pass
        with httpx.Client(timeout=90.0) as client:
            r = client.post(
                "https://api.dev.runwayml.com/v1/text_to_video",
                json=body,
                headers={
                    "Authorization": f"Bearer {secret}",
                    "Content-Type": "application/json",
                    "X-Runway-Version": conf.get("runway_version") or "2024-11-06",
                },
            )
        if r.status_code >= 400:
            raise VideoError(f"Runway error {r.status_code}: {r.text[:240]}", r.status_code)
        data = r.json() if r.content else {}
        tid = data.get("id")
        if not tid:
            raise VideoError("Runway did not return a task id")
        return {
            "status": "running",
            "external_id": str(tid),
            "poll_url": f"https://api.dev.runwayml.com/v1/tasks/{tid}",
        }

    if provider == "luma":
        if not secret:
            raise VideoError("Luma API key required")
        prompt = _ai_prompt(payload, conf)
        model = (conf.get("model") or "ray-2").strip()
        aspect = (conf.get("aspect_ratio") or "9:16").strip()
        body = {
            "prompt": prompt,
            "model": model,
            "aspect_ratio": aspect,
        }
        with httpx.Client(timeout=90.0) as client:
            r = client.post(
                "https://api.lumalabs.ai/dream-machine/v1/generations",
                json=body,
                headers={
                    "Authorization": f"Bearer {secret}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        if r.status_code >= 400:
            raise VideoError(f"Luma error {r.status_code}: {r.text[:240]}", r.status_code)
        data = r.json() if r.content else {}
        tid = data.get("id")
        if not tid:
            raise VideoError("Luma did not return a generation id")
        return {
            "status": "running",
            "external_id": str(tid),
            "poll_url": f"https://api.lumalabs.ai/dream-machine/v1/generations/{tid}",
        }

    if provider == "replicate":
        if not secret:
            raise VideoError("Replicate API token required")
        prompt = _ai_prompt(payload, conf)
        model = (conf.get("model") or "kwaivgi/kling-v1.6-standard").strip()
        # duration / aspect as model inputs when supported
        inp: dict[str, Any] = {"prompt": prompt}
        if conf.get("duration"):
            try:
                inp["duration"] = int(conf["duration"])
            except (TypeError, ValueError):
                pass
        if conf.get("aspect_ratio"):
            inp["aspect_ratio"] = str(conf["aspect_ratio"])
        # merge extra inputs
        extra = conf.get("input") or {}
        if isinstance(extra, dict):
            inp.update(extra)
        body = {"input": inp}
        # Prefer models endpoint (owner/name) or version
        if conf.get("version"):
            url = "https://api.replicate.com/v1/predictions"
            body["version"] = conf["version"]
        else:
            url = f"https://api.replicate.com/v1/models/{model}/predictions"
        with httpx.Client(timeout=90.0) as client:
            r = client.post(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {secret}",
                    "Content-Type": "application/json",
                    "Prefer": "wait=0",
                },
            )
        if r.status_code >= 400:
            raise VideoError(f"Replicate error {r.status_code}: {r.text[:240]}", r.status_code)
        data = r.json() if r.content else {}
        tid = data.get("id")
        get_url = (data.get("urls") or {}).get("get") or (f"https://api.replicate.com/v1/predictions/{tid}" if tid else "")
        if data.get("status") == "succeeded":
            out = data.get("output")
            video_url = out if isinstance(out, str) else (out[0] if isinstance(out, list) and out else "")
            if video_url:
                return {"status": "done", "video_url": str(video_url), "external_id": str(tid or "")}
        if not tid:
            raise VideoError("Replicate did not return a prediction id")
        return {
            "status": "running",
            "external_id": str(tid),
            "poll_url": str(get_url),
        }

    raise VideoError(f"Provider '{provider}' not implemented")


def get_job(workspace_id: int, job_id: int) -> dict:
    row = db.one(
        "SELECT * FROM video_jobs WHERE id=%s AND workspace_id=%s",
        (job_id, workspace_id),
    )
    if not row:
        raise VideoError("Job not found")
    return _job_out(row)


def get_post_job(workspace_id: int, post_id: int) -> Optional[dict]:
    row = db.one(
        """SELECT * FROM video_jobs WHERE workspace_id=%s AND post_id=%s
           ORDER BY id DESC LIMIT 1""",
        (workspace_id, post_id),
    )
    return _job_out(row) if row else None


def _job_out(row: dict) -> dict:
    out = dict(row)
    for k in ("created_at", "updated_at"):
        if out.get(k) and hasattr(out[k], "isoformat"):
            out[k] = out[k].isoformat()
    if out.get("request_json") is not None and not isinstance(out["request_json"], dict):
        try:
            out["request_json"] = json.loads(out["request_json"]) if isinstance(out["request_json"], str) else out["request_json"]
        except Exception:
            pass
    return out


def poll_job(workspace_id: int, job_id: int) -> dict:
    job = get_job(workspace_id, job_id)
    if job["status"] in ("done", "failed"):
        return job
    provider = job["provider"]
    secret = _resolve_secret(workspace_id)
    cfg = get_config(workspace_id)
    conf = cfg.get("config") or {}

    poll_url = job.get("poll_url") or ""
    external_id = job.get("external_id") or ""

    try:
        if provider == "shotstack" and (poll_url or external_id):
            url = poll_url or f"https://api.shotstack.io/stage/edit/v1/render/{external_id}"
            # Prefer prod if config says so
            env = (conf.get("shotstack_env") or "stage").lower()
            if not poll_url and env in ("v1", "prod", "production"):
                url = f"https://api.shotstack.io/edit/v1/render/{external_id}"
            with httpx.Client(timeout=30.0) as client:
                r = client.get(url, headers={"x-api-key": secret})
            if r.status_code >= 400:
                return job
            data = r.json() if r.content else {}
            resp = data.get("response") or data
            st = (resp.get("status") or "").lower()
            if st == "done":
                video_url = resp.get("url") or ""
                return _apply_provider_result(
                    job_id, workspace_id, job["post_id"],
                    {"status": "done", "video_url": video_url, "external_id": external_id},
                )
            if st in ("failed", "error"):
                return _apply_provider_result(
                    job_id, workspace_id, job["post_id"],
                    {"status": "failed", "error": resp.get("error") or "Shotstack render failed", "external_id": external_id},
                )

        if provider == "creatomate" and (poll_url or external_id):
            url = poll_url or f"https://api.creatomate.com/v1/renders/{external_id}"
            with httpx.Client(timeout=30.0) as client:
                r = client.get(url, headers={"Authorization": f"Bearer {secret}"})
            if r.status_code >= 400:
                return job
            data = r.json() if r.content else {}
            if isinstance(data, list) and data:
                data = data[0]
            st = (data.get("status") or "").lower()
            url_out = data.get("url") or ""
            if st in ("succeeded", "done", "completed") and url_out:
                return _apply_provider_result(
                    job_id, workspace_id, job["post_id"],
                    {"status": "done", "video_url": url_out, "external_id": external_id},
                )
            if st in ("failed", "error"):
                return _apply_provider_result(
                    job_id, workspace_id, job["post_id"],
                    {"status": "failed", "error": data.get("error_message") or "Creatomate failed", "external_id": external_id},
                )

        if provider == "custom_webhook" and poll_url:
            headers = {"User-Agent": "HookTrack-Video/1.0"}
            if secret:
                headers["Authorization"] = f"Bearer {secret}"
                headers["X-API-Key"] = secret
            with httpx.Client(timeout=30.0) as client:
                r = client.get(poll_url, headers=headers)
            if r.status_code >= 400:
                return job
            data = r.json() if r.content else {}
            if not isinstance(data, dict):
                return job
            video_url = data.get("video_url") or data.get("url") or ""
            st = (data.get("status") or "").lower()
            if video_url or st in ("done", "completed", "success"):
                if not video_url:
                    return job
                return _apply_provider_result(
                    job_id, workspace_id, job["post_id"],
                    {"status": "done", "video_url": video_url, "external_id": external_id},
                )
            if st in ("failed", "error"):
                return _apply_provider_result(
                    job_id, workspace_id, job["post_id"],
                    {"status": "failed", "error": data.get("error") or "Webhook job failed", "external_id": external_id},
                )

        if provider == "runway" and (poll_url or external_id):
            url = poll_url or f"https://api.dev.runwayml.com/v1/tasks/{external_id}"
            with httpx.Client(timeout=45.0) as client:
                r = client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {secret}",
                        "X-Runway-Version": conf.get("runway_version") or "2024-11-06",
                    },
                )
            if r.status_code >= 400:
                return job
            data = r.json() if r.content else {}
            st = (data.get("status") or "").upper()
            if st == "SUCCEEDED":
                out = data.get("output")
                video_url = ""
                if isinstance(out, list) and out:
                    video_url = str(out[0])
                elif isinstance(out, str):
                    video_url = out
                elif isinstance(out, dict):
                    video_url = str(out.get("url") or out.get("video") or "")
                if video_url:
                    return _apply_provider_result(
                        job_id, workspace_id, job["post_id"],
                        {"status": "done", "video_url": video_url, "external_id": external_id},
                    )
            if st in ("FAILED", "CANCELLED"):
                return _apply_provider_result(
                    job_id, workspace_id, job["post_id"],
                    {"status": "failed", "error": data.get("failure") or data.get("error") or "Runway failed",
                     "external_id": external_id},
                )

        if provider == "luma" and (poll_url or external_id):
            url = poll_url or f"https://api.lumalabs.ai/dream-machine/v1/generations/{external_id}"
            with httpx.Client(timeout=45.0) as client:
                r = client.get(
                    url,
                    headers={"Authorization": f"Bearer {secret}", "Accept": "application/json"},
                )
            if r.status_code >= 400:
                return job
            data = r.json() if r.content else {}
            st = (data.get("state") or data.get("status") or "").lower()
            assets = data.get("assets") or {}
            video_url = assets.get("video") or data.get("video") or ""
            if st in ("completed", "succeeded", "done") and video_url:
                return _apply_provider_result(
                    job_id, workspace_id, job["post_id"],
                    {"status": "done", "video_url": str(video_url), "external_id": external_id},
                )
            if st in ("failed", "error"):
                return _apply_provider_result(
                    job_id, workspace_id, job["post_id"],
                    {"status": "failed", "error": data.get("failure_reason") or data.get("error") or "Luma failed",
                     "external_id": external_id},
                )

        if provider == "replicate" and (poll_url or external_id):
            url = poll_url or f"https://api.replicate.com/v1/predictions/{external_id}"
            with httpx.Client(timeout=45.0) as client:
                r = client.get(url, headers={"Authorization": f"Bearer {secret}"})
            if r.status_code >= 400:
                return job
            data = r.json() if r.content else {}
            st = (data.get("status") or "").lower()
            if st == "succeeded":
                out = data.get("output")
                video_url = out if isinstance(out, str) else (out[0] if isinstance(out, list) and out else "")
                if video_url:
                    return _apply_provider_result(
                        job_id, workspace_id, job["post_id"],
                        {"status": "done", "video_url": str(video_url), "external_id": external_id},
                    )
            if st in ("failed", "canceled", "cancelled"):
                err = data.get("error") or "Replicate prediction failed"
                return _apply_provider_result(
                    job_id, workspace_id, job["post_id"],
                    {"status": "failed", "error": str(err)[:400], "external_id": external_id},
                )

    except Exception as e:
        # leave job running; surface soft error on next get
        db.execute(
            "UPDATE video_jobs SET error=%s, updated_at=now() WHERE id=%s AND workspace_id=%s",
            (f"poll: {str(e)[:200]}", job_id, workspace_id),
        )
        return get_job(workspace_id, job_id)

    return get_job(workspace_id, job_id)


def poll_running_jobs(limit: int = 20) -> int:
    """Background helper: poll a batch of running jobs across workspaces (one worker at a time)."""
    with leases.lease("video-poll", ttl=300) as got:
        if not got:
            return 0
        return _poll_batch(limit)


def _poll_batch(limit: int) -> int:
    rows = db.query(
        """SELECT id, workspace_id FROM video_jobs
           WHERE status IN ('pending','running')
           ORDER BY updated_at ASC LIMIT %s""",
        (limit,),
    )
    n = 0
    for r in rows:
        try:
            poll_job(r["workspace_id"], r["id"])
            n += 1
        except Exception:
            pass
    return n


def test_connection(workspace_id: int) -> dict:
    cfg = get_config(workspace_id)
    provider = cfg["provider"]
    if provider == "mock":
        return {"ok": True, "message": "Mock provider ready"}
    if provider == "custom_webhook":
        url = (cfg.get("config") or {}).get("webhook_url") or ""
        if not url:
            raise VideoError("Set webhook_url first")
        _safe_url(url)
        return {"ok": True, "message": f"Webhook URL accepted ({urlparse(url).hostname})"}
    secret = _resolve_secret(workspace_id)
    if PROVIDERS.get(provider, {}).get("needs_key") and not secret:
        raise VideoError("API key required")
    if provider == "shotstack":
        env = ((cfg.get("config") or {}).get("shotstack_env") or "stage").lower()
        base = "https://api.shotstack.io/edit/v1" if env in ("v1", "prod", "production") else "https://api.shotstack.io/stage/edit/v1"
        with httpx.Client(timeout=20.0) as client:
            # lightweight probe — templates list or render with invalid may 401
            r = client.get(f"{base.replace('/edit/v1','')}/edit/v1/templates", headers={"x-api-key": secret})
            # Shotstack may 404 on templates; accept any non-401
            if r.status_code == 401:
                raise VideoError("Shotstack rejected the API key", 401)
        return {"ok": True, "message": "Shotstack API key accepted"}
    if provider == "creatomate":
        with httpx.Client(timeout=20.0) as client:
            r = client.get(
                "https://api.creatomate.com/v1/templates",
                headers={"Authorization": f"Bearer {secret}"},
            )
            if r.status_code == 401:
                raise VideoError("Creatomate rejected the API key", 401)
        return {"ok": True, "message": "Creatomate API key accepted"}
    if provider == "runway":
        with httpx.Client(timeout=20.0) as client:
            r = client.get(
                "https://api.dev.runwayml.com/v1/organization",
                headers={
                    "Authorization": f"Bearer {secret}",
                    "X-Runway-Version": (cfg.get("config") or {}).get("runway_version") or "2024-11-06",
                },
            )
            if r.status_code == 401:
                raise VideoError("Runway rejected the API key", 401)
        return {"ok": True, "message": "Runway API key accepted"}
    if provider == "luma":
        with httpx.Client(timeout=20.0) as client:
            r = client.get(
                "https://api.lumalabs.ai/dream-machine/v1/generations",
                headers={"Authorization": f"Bearer {secret}", "Accept": "application/json"},
                params={"limit": 1},
            )
            if r.status_code == 401:
                raise VideoError("Luma rejected the API key", 401)
        return {"ok": True, "message": "Luma API key accepted"}
    if provider == "replicate":
        with httpx.Client(timeout=20.0) as client:
            r = client.get(
                "https://api.replicate.com/v1/account",
                headers={"Authorization": f"Bearer {secret}"},
            )
            if r.status_code == 401:
                raise VideoError("Replicate rejected the API token", 401)
        return {"ok": True, "message": "Replicate API token accepted"}
    return {"ok": True, "message": f"Provider {provider} configured"}
