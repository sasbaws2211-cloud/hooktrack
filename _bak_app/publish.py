"""Optional social-publish add-on (BYOK).

Providers:
  - upload_post: https://www.upload-post.com  (best price / simple)
  - ayrshare:    https://www.ayrshare.com
  - late:        https://getlate.dev
  - mock:        no external call (dev)

Posts caption + optional video/image URL to connected social accounts.
Requires explicit user action — never auto-publishes.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from . import crypto, db

PROVIDERS = {
    "upload_post": {
        "label": "Upload-Post",
        "needs_key": True,
        "hint": "API key from upload-post.com. Post video/image + caption to connected profiles.",
    },
    "ayrshare": {
        "label": "Ayrshare",
        "needs_key": True,
        "hint": "API key from ayrshare.com. Multi-network publish via Profile Key.",
    },
    "late": {
        "label": "Late",
        "needs_key": True,
        "hint": "API key from getlate.dev. Schedule/publish to connected accounts.",
    },
    "mock": {
        "label": "Mock (dev)",
        "needs_key": False,
        "hint": "Marks the post as published with a fake external id — no network call.",
    },
}

# Map HookTrack account.platform → provider platform ids
PLATFORM_MAP = {
    "tiktok": {"upload_post": "tiktok", "ayrshare": "tiktok", "late": "tiktok"},
    "instagram": {"upload_post": "instagram", "ayrshare": "instagram", "late": "instagram"},
    "youtube": {"upload_post": "youtube", "ayrshare": "youtube", "late": "youtube"},
    "x": {"upload_post": "x", "ayrshare": "twitter", "late": "x"},
    "twitter": {"upload_post": "x", "ayrshare": "twitter", "late": "x"},
    "facebook": {"upload_post": "facebook", "ayrshare": "facebook", "late": "facebook"},
    "linkedin": {"upload_post": "linkedin", "ayrshare": "linkedin", "late": "linkedin"},
    "threads": {"upload_post": "threads", "ayrshare": "threads", "late": "threads"},
}


class PublishError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def get_config(workspace_id: int) -> dict:
    row = db.one(
        "SELECT * FROM workspace_addons WHERE workspace_id=%s AND addon_id='publish'",
        (workspace_id,),
    )
    if not row:
        return {
            "enabled": False,
            "provider": "upload_post",
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
        "provider": row.get("provider") or "upload_post",
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
        raise PublishError(f"Unknown provider '{provider}'")
    existing = db.one(
        "SELECT secrets_enc, key_hint, config_json FROM workspace_addons WHERE workspace_id=%s AND addon_id='publish'",
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

    db.execute(
        """INSERT INTO workspace_addons
           (workspace_id, addon_id, enabled, provider, config_json, secrets_enc, key_hint)
           VALUES (%s,'publish',%s,%s,%s,%s,%s)
           ON CONFLICT (workspace_id, addon_id) DO UPDATE SET
             enabled=EXCLUDED.enabled, provider=EXCLUDED.provider,
             config_json=EXCLUDED.config_json, secrets_enc=EXCLUDED.secrets_enc,
             key_hint=EXCLUDED.key_hint, updated_at=now()""",
        (workspace_id, enabled, provider, db.Json(cfg), secrets_enc, key_hint),
    )
    return get_config(workspace_id)


def _resolve_secret(workspace_id: int) -> str:
    row = db.one(
        "SELECT secrets_enc FROM workspace_addons WHERE workspace_id=%s AND addon_id='publish'",
        (workspace_id,),
    )
    if row and row.get("secrets_enc"):
        return crypto.decrypt(row["secrets_enc"]) or ""
    return (os.environ.get("HOOKTRACK_PUBLISH_API_KEY") or "").strip()


def _platform_key(platform: str, provider: str) -> Optional[str]:
    p = (platform or "tiktok").strip().lower()
    # normalize
    for k, v in list(PLATFORM_MAP.items()):
        if p == k or p.startswith(k) or k in p:
            return v.get(provider, k if provider == "mock" else None)
    return PLATFORM_MAP.get("tiktok", {}).get(provider, "tiktok" if provider == "mock" else None)


def publish_post(
    workspace_id: int,
    post_id: int,
    *,
    platforms: Optional[list[str]] = None,
    schedule_at: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """Publish a HookTrack post via the configured provider. Explicit action only."""
    cfg = get_config(workspace_id)
    if not cfg["enabled"] and not dry_run:
        raise PublishError("Social publish add-on is not enabled")
    post = db.one("SELECT * FROM posts WHERE id=%s AND workspace_id=%s", (post_id, workspace_id))
    if not post:
        raise PublishError("Post not found")

    account = None
    if post.get("account_id"):
        account = db.one(
            "SELECT * FROM accounts WHERE id=%s AND workspace_id=%s",
            (post["account_id"], workspace_id),
        )

    if not dry_run:
        open_job = db.one(
            "SELECT id FROM publish_jobs WHERE workspace_id=%s AND post_id=%s AND status='unconfirmed' LIMIT 1",
            (workspace_id, post_id),
        )
        if open_job:
            raise PublishError(
                "An earlier publish of this post was sent but never confirmed, so it may already be live. "
                "Open Scheduled and say whether it was posted first.")

    caption = (post.get("caption") or post.get("hook") or "").strip()
    if not caption and not post.get("video_url"):
        raise PublishError("Post needs a caption or video_url before publishing")

    provider = cfg["provider"]
    conf = cfg.get("config") or {}
    secret = _resolve_secret(workspace_id)

    # Default platforms from account or config default list
    if not platforms:
        if account and account.get("platform"):
            pk = _platform_key(account["platform"], provider)
            platforms = [pk] if pk else []
        else:
            platforms = list(conf.get("default_platforms") or ["tiktok"])

    platforms = [p for p in platforms if p]
    if not platforms:
        raise PublishError("No platforms selected")

    media_url = (post.get("video_url") or "").strip() or (conf.get("default_media_url") or "").strip()
    payload = {
        "post_id": post_id,
        "caption": caption,
        "hook": post.get("hook") or "",
        "body": post.get("body") or "",
        "media_url": media_url,
        "platforms": platforms,
        "account_handle": (account or {}).get("handle") or "",
        "schedule_at": schedule_at,
    }

    # Parse schedule_at → aware UTC datetime or None
    when = _parse_schedule(schedule_at)
    mode = (conf.get("schedule_mode") or "local").lower()  # local | provider

    # Future schedule handled by HookTrack queue (default) unless mode=provider
    if when and when > datetime.now(timezone.utc) and mode != "provider":
        job = db.one(
            """INSERT INTO publish_jobs
               (workspace_id, post_id, provider, status, request_json, scheduled_for)
               VALUES (%s,%s,%s,'queued',%s,%s) RETURNING *""",
            (workspace_id, post_id, provider, db.Json(payload), when),
        )
        db.execute(
            """UPDATE posts SET scheduled_for=%s, publish_platforms=%s
               WHERE id=%s AND workspace_id=%s""",
            (when, ",".join(platforms)[:200], post_id, workspace_id),
        )
        return _job_out(job)

    job = db.one(
        """INSERT INTO publish_jobs
           (workspace_id, post_id, provider, status, request_json, scheduled_for)
           VALUES (%s,%s,%s,'pending',%s,%s) RETURNING *""",
        (workspace_id, post_id, provider, db.Json(payload), when),
    )

    if dry_run or provider == "mock":
        result = {
            "status": "scheduled" if when and mode == "provider" else "done",
            "external_id": f"mock-{job['id']}",
            "raw": {"mock": True, "platforms": platforms, "schedule_at": schedule_at},
        }
    else:
        try:
            # For provider-mode future schedule, still pass schedule_at into API
            _mark_dispatching(job["id"], workspace_id)
            result = _dispatch(provider, secret, conf, payload, account)
        except PublishError as e:
            _fail_job(job["id"], workspace_id, str(e))
            raise
        except Exception as e:
            _fail_job(job["id"], workspace_id, str(e)[:300])
            raise PublishError(str(e)[:300])

    return _apply_result(job["id"], workspace_id, post_id, result, platforms)


def _parse_schedule(schedule_at: Optional[str]):
    if not schedule_at or not str(schedule_at).strip():
        return None
    raw = str(schedule_at).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        raise PublishError("Invalid schedule_at (use ISO datetime, e.g. 2026-10-10T15:00:00Z)")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fail_job(job_id: int, workspace_id: int, error: str):
    db.execute(
        """UPDATE publish_jobs SET status='failed', error=%s, updated_at=now()
           WHERE id=%s AND workspace_id=%s""",
        (error[:500], job_id, workspace_id),
    )


def _apply_result(job_id: int, workspace_id: int, post_id: int, result: dict, platforms: list) -> dict:
    status = result.get("status") or "done"
    external_id = str(result.get("external_id") or "")[:200]
    error = result.get("error") or ""
    raw = result.get("raw") or {}

    if status == "done":
        db.execute(
            """UPDATE publish_jobs SET status='done', external_id=%s, result_json=%s, error='',
               updated_at=now() WHERE id=%s AND workspace_id=%s""",
            (external_id, db.Json(raw), job_id, workspace_id),
        )
        db.execute(
            """UPDATE posts SET
                 status='posted',
                 external_post_id=%s,
                 published_at=COALESCE(published_at, now()),
                 publish_platforms=%s,
                 scheduled_for=NULL
               WHERE id=%s AND workspace_id=%s""",
            (external_id, ",".join(platforms)[:200], post_id, workspace_id),
        )
    elif status == "scheduled":
        db.execute(
            """UPDATE publish_jobs SET status='scheduled', external_id=%s, result_json=%s,
               updated_at=now() WHERE id=%s AND workspace_id=%s""",
            (external_id, db.Json(raw), job_id, workspace_id),
        )
        db.execute(
            """UPDATE posts SET external_post_id=%s, publish_platforms=%s,
                 scheduled_for=COALESCE(scheduled_for, now())
               WHERE id=%s AND workspace_id=%s""",
            (external_id, ",".join(platforms)[:200], post_id, workspace_id),
        )
    else:
        db.execute(
            """UPDATE publish_jobs SET status='failed', external_id=%s, error=%s, result_json=%s,
               updated_at=now() WHERE id=%s AND workspace_id=%s""",
            (external_id, (error or "Publish failed")[:500], db.Json(raw), job_id, workspace_id),
        )

    return get_job(workspace_id, job_id)


def _dispatch(provider: str, secret: str, conf: dict, payload: dict, account: Optional[dict]) -> dict:
    if not secret and provider != "mock":
        raise PublishError(f"{PROVIDERS[provider]['label']} API key required")

    caption = payload["caption"]
    media_url = payload.get("media_url") or ""
    platforms = payload["platforms"]
    schedule_at = payload.get("schedule_at")

    if provider == "upload_post":
        # Docs: POST https://api.upload-post.com/api/upload with form or JSON
        # Common pattern: Authorization Bearer, platform list, title/description, media
        body: dict[str, Any] = {
            "title": (payload.get("hook") or caption)[:100],
            "description": caption,
            "caption": caption,
            "platforms": platforms,
        }
        if media_url:
            body["media_url"] = media_url
            body["video"] = media_url
        # User profile / user id from config if multi-tenant
        if conf.get("user"):
            body["user"] = conf["user"]
        if conf.get("profile"):
            body["profile"] = conf["profile"]
        if schedule_at:
            body["scheduled_date"] = schedule_at
            body["schedule_date"] = schedule_at
        # Optional per-platform account mapping from config
        if conf.get("platform_accounts"):
            body["platform_accounts"] = conf["platform_accounts"]
        headers = {
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "User-Agent": "HookTrack-Publish/1.0",
        }
        # Some Upload-Post setups use X-API-Key
        headers["X-API-Key"] = secret
        base = (conf.get("base_url") or "https://api.upload-post.com").rstrip("/")
        with httpx.Client(timeout=120.0) as client:
            r = client.post(f"{base}/api/upload", json=body, headers=headers)
            if r.status_code == 404:
                r = client.post(f"{base}/api/posts", json=body, headers=headers)
        if r.status_code >= 400:
            raise PublishError(f"Upload-Post error {r.status_code}: {r.text[:240]}", r.status_code)
        data = r.json() if r.content else {}
        eid = str(data.get("id") or data.get("post_id") or data.get("request_id") or "")
        return {"status": "scheduled" if schedule_at else "done", "external_id": eid, "raw": data}

    if provider == "ayrshare":
        # POST https://api.ayrshare.com/api/post
        body = {
            "post": caption,
            "platforms": platforms,
        }
        if media_url:
            body["mediaUrls"] = [media_url]
        if schedule_at:
            body["scheduleDate"] = schedule_at
        # Profile key for multi-user: config profile_key or header
        headers = {
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "User-Agent": "HookTrack-Publish/1.0",
        }
        if conf.get("profile_key"):
            headers["Profile-Key"] = str(conf["profile_key"])
        with httpx.Client(timeout=120.0) as client:
            r = client.post("https://api.ayrshare.com/api/post", json=body, headers=headers)
        if r.status_code >= 400:
            raise PublishError(f"Ayrshare error {r.status_code}: {r.text[:240]}", r.status_code)
        data = r.json() if r.content else {}
        eid = str(data.get("id") or data.get("postIds") or data.get("refId") or "")
        if isinstance(data.get("postIds"), dict):
            eid = ",".join(str(v) for v in data["postIds"].values())[:200]
        return {"status": "scheduled" if schedule_at else "done", "external_id": eid, "raw": data}

    if provider == "late":
        body = {
            "content": caption,
            "platforms": platforms,
        }
        if media_url:
            body["media_urls"] = [media_url]
            body["media"] = [{"url": media_url, "type": "video"}]
        if schedule_at:
            body["scheduled_at"] = schedule_at
            body["status"] = "scheduled"
        else:
            body["status"] = "published"
        if conf.get("account_ids"):
            body["account_ids"] = conf["account_ids"]
        headers = {
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "User-Agent": "HookTrack-Publish/1.0",
        }
        base = (conf.get("base_url") or "https://getlate.dev/api/v1").rstrip("/")
        with httpx.Client(timeout=120.0) as client:
            r = client.post(f"{base}/posts", json=body, headers=headers)
            if r.status_code == 404:
                r = client.post(f"{base}/post", json=body, headers=headers)
        if r.status_code >= 400:
            raise PublishError(f"Late error {r.status_code}: {r.text[:240]}", r.status_code)
        data = r.json() if r.content else {}
        eid = str(data.get("id") or (data.get("post") or {}).get("id") or "")
        return {"status": "scheduled" if schedule_at else "done", "external_id": eid, "raw": data}

    raise PublishError(f"Provider '{provider}' not implemented")


def get_job(workspace_id: int, job_id: int) -> dict:
    row = db.one(
        "SELECT * FROM publish_jobs WHERE id=%s AND workspace_id=%s",
        (job_id, workspace_id),
    )
    if not row:
        raise PublishError("Job not found")
    return _job_out(row)


def get_post_job(workspace_id: int, post_id: int) -> Optional[dict]:
    row = db.one(
        """SELECT * FROM publish_jobs WHERE workspace_id=%s AND post_id=%s
           ORDER BY id DESC LIMIT 1""",
        (workspace_id, post_id),
    )
    return _job_out(row) if row else None


def _job_out(row: dict) -> dict:
    out = dict(row)
    for k in ("created_at", "updated_at"):
        if out.get(k) and hasattr(out[k], "isoformat"):
            out[k] = out[k].isoformat()
    for k in ("request_json", "result_json"):
        if out.get(k) is not None and not isinstance(out[k], dict):
            try:
                out[k] = json.loads(out[k]) if isinstance(out[k], str) else out[k]
            except Exception:
                pass
    return out


def test_connection(workspace_id: int) -> dict:
    cfg = get_config(workspace_id)
    provider = cfg["provider"]
    if provider == "mock":
        return {"ok": True, "message": "Mock publisher ready"}
    secret = _resolve_secret(workspace_id)
    if PROVIDERS.get(provider, {}).get("needs_key") and not secret:
        raise PublishError("API key required")
    conf = cfg.get("config") or {}

    if provider == "ayrshare":
        with httpx.Client(timeout=20.0) as client:
            r = client.get(
                "https://api.ayrshare.com/api/user",
                headers={"Authorization": f"Bearer {secret}"},
            )
            if r.status_code == 401:
                raise PublishError("Ayrshare rejected the API key", 401)
            if r.status_code >= 400:
                # Some keys still work for post but /user differs — accept non-401
                pass
        return {"ok": True, "message": "Ayrshare API key accepted"}

    if provider == "upload_post":
        base = (conf.get("base_url") or "https://api.upload-post.com").rstrip("/")
        with httpx.Client(timeout=20.0) as client:
            r = client.get(
                f"{base}/api/upload",
                headers={"Authorization": f"Bearer {secret}", "X-API-Key": secret},
            )
            # GET may 405; only fail hard on 401
            if r.status_code == 401:
                raise PublishError("Upload-Post rejected the API key", 401)
        return {"ok": True, "message": "Upload-Post API key accepted"}

    if provider == "late":
        base = (conf.get("base_url") or "https://getlate.dev/api/v1").rstrip("/")
        with httpx.Client(timeout=20.0) as client:
            r = client.get(
                f"{base}/accounts",
                headers={"Authorization": f"Bearer {secret}"},
            )
            if r.status_code == 401:
                raise PublishError("Late rejected the API key", 401)
        return {"ok": True, "message": "Late API key accepted"}

    return {"ok": True, "message": f"Provider {provider} configured"}


def list_scheduled(workspace_id: int, limit: int = 100) -> list:
    rows = db.query(
        """SELECT j.*, p.hook, p.caption, p.video_url, p.status AS post_status, a.handle
           FROM publish_jobs j
           JOIN posts p ON p.id = j.post_id
           LEFT JOIN accounts a ON a.id = p.account_id
           WHERE j.workspace_id=%s AND j.status IN ('queued', 'scheduled')
           ORDER BY COALESCE(j.scheduled_for, j.created_at) ASC
           LIMIT %s""",
        (workspace_id, limit),
    )
    return [_job_out(r) for r in rows]


def cancel_schedule(workspace_id: int, job_id: int) -> dict:
    row = db.one(
        """UPDATE publish_jobs SET status='failed', error='cancelled', updated_at=now()
           WHERE id=%s AND workspace_id=%s AND status IN ('queued','scheduled','pending')
           RETURNING *""",
        (job_id, workspace_id),
    )
    if not row:
        raise PublishError("Scheduled job not found or already finished")
    db.execute(
        "UPDATE posts SET scheduled_for=NULL WHERE id=%s AND workspace_id=%s",
        (row["post_id"], workspace_id),
    )
    return _job_out(row)



STALE_MINUTES = float(os.environ.get("HOOKTRACK_PUBLISH_STALE_MINUTES", "10"))   # provider timeout is 120s


def _mark_dispatching(job_id: int, workspace_id: int) -> None:
    """Called right before the request leaves for the provider. From here on a crash is ambiguous."""
    db.execute(
        "UPDATE publish_jobs SET dispatched_at=now(), updated_at=now() WHERE id=%s AND workspace_id=%s",
        (job_id, workspace_id),
    )


def recover_stuck_jobs(stale_minutes: Optional[float] = None) -> dict:
    """Sweep jobs left 'pending' by a crash or restart. Never re-sends something that may have gone out.

    - sent to the provider, outcome unknown -> 'unconfirmed' (a person decides; auto-retry could double-post)
    - never sent, user-scheduled            -> back to 'queued'
    - never sent, immediate publish         -> 'failed' (the click that started it is long gone)
    """
    mins = STALE_MINUTES if stale_minutes is None else stale_minutes
    cutoff = f"{float(mins)} minutes"
    unconfirmed = db.query(
        """UPDATE publish_jobs SET status='unconfirmed', updated_at=now(),
               error='Sent to the provider but the result was never recorded (the server stopped). It may or may not be live.'
           WHERE status='pending' AND dispatched_at IS NOT NULL AND updated_at < now() - %s::interval
           RETURNING id""", (cutoff,))
    requeued = db.query(
        """UPDATE publish_jobs SET status='queued', updated_at=now()
           WHERE status='pending' AND dispatched_at IS NULL AND scheduled_for IS NOT NULL
             AND updated_at < now() - %s::interval RETURNING id""", (cutoff,))
    failed = db.query(
        """UPDATE publish_jobs SET status='failed', updated_at=now(),
               error='Interrupted before anything was sent, so nothing was posted. Publish again if you still want it.'
           WHERE status='pending' AND dispatched_at IS NULL AND scheduled_for IS NULL
             AND updated_at < now() - %s::interval RETURNING id""", (cutoff,))
    return {"unconfirmed": len(unconfirmed), "requeued": len(requeued), "failed": len(failed)}


def list_attention(workspace_id: int, limit: int = 50) -> list:
    rows = db.query(
        """SELECT j.*, p.hook, p.caption, a.handle
           FROM publish_jobs j JOIN posts p ON p.id = j.post_id
           LEFT JOIN accounts a ON a.id = p.account_id
           WHERE j.workspace_id=%s AND j.status='unconfirmed'
           ORDER BY j.dispatched_at DESC NULLS LAST LIMIT %s""",
        (workspace_id, limit),
    )
    out = []
    for r in rows:
        o = _job_out(r)
        if o.get("dispatched_at") and hasattr(o["dispatched_at"], "isoformat"):
            o["dispatched_at"] = o["dispatched_at"].isoformat()
        if o.get("scheduled_for") and hasattr(o["scheduled_for"], "isoformat"):
            o["scheduled_for"] = o["scheduled_for"].isoformat()
        out.append(o)
    return out


def resolve_unconfirmed(workspace_id: int, job_id: int, action: str, external_id: str = "") -> dict:
    """A person says what happened to an unconfirmed job: action = 'posted' | 'not_posted'."""
    if action not in ("posted", "not_posted"):
        raise PublishError("action must be 'posted' or 'not_posted'")
    row = db.one(
        "SELECT * FROM publish_jobs WHERE id=%s AND workspace_id=%s AND status='unconfirmed'",
        (job_id, workspace_id),
    )
    if not row:
        raise PublishError("Job not found or already resolved", 404)
    if action == "posted":
        req = row.get("request_json") or {}
        if isinstance(req, str):
            req = json.loads(req)
        return _apply_result(job_id, workspace_id, row["post_id"],
                             {"status": "done", "external_id": external_id.strip()[:200],
                              "raw": {"confirmed_by": "user"}},
                             req.get("platforms") or [])
    db.execute(
        """UPDATE publish_jobs SET status='failed', error='Confirmed not posted', updated_at=now()
           WHERE id=%s AND workspace_id=%s""", (job_id, workspace_id))
    db.execute("UPDATE posts SET scheduled_for=NULL WHERE id=%s AND workspace_id=%s",
               (row["post_id"], workspace_id))
    return get_job(workspace_id, job_id)


def process_due_schedules(limit: int = 20) -> int:
    """Run locally queued publish jobs whose scheduled_for is due."""
    now = datetime.now(timezone.utc)
    rows = db.query(
        """SELECT id, workspace_id, post_id, request_json, provider
           FROM publish_jobs
           WHERE status='queued' AND scheduled_for IS NOT NULL AND scheduled_for <= %s
           ORDER BY scheduled_for ASC
           LIMIT %s""",
        (now, limit),
    )
    n = 0
    for r in rows:
        # Claim the job
        claimed = db.one(
            """UPDATE publish_jobs SET status='pending', updated_at=now()
               WHERE id=%s AND status='queued' RETURNING id""",
            (r["id"],),
        )
        if not claimed:
            continue
        try:
            req = r.get("request_json") or {}
            if isinstance(req, str):
                req = json.loads(req)
            platforms = req.get("platforms") or ["tiktok"]
            # Re-enter publish path without schedule (publish now)
            cfg = get_config(r["workspace_id"])
            if not cfg["enabled"]:
                _fail_job(r["id"], r["workspace_id"], "Publish add-on disabled")
                continue
            post = db.one(
                "SELECT * FROM posts WHERE id=%s AND workspace_id=%s",
                (r["post_id"], r["workspace_id"]),
            )
            if not post:
                _fail_job(r["id"], r["workspace_id"], "Post deleted")
                continue
            account = None
            if post.get("account_id"):
                account = db.one(
                    "SELECT * FROM accounts WHERE id=%s AND workspace_id=%s",
                    (post["account_id"], r["workspace_id"]),
                )
            secret = _resolve_secret(r["workspace_id"])
            conf = cfg.get("config") or {}
            provider = r["provider"] or cfg["provider"]
            payload = {
                "post_id": r["post_id"],
                "caption": req.get("caption") or post.get("caption") or post.get("hook") or "",
                "hook": post.get("hook") or "",
                "body": post.get("body") or "",
                "media_url": req.get("media_url") or post.get("video_url") or "",
                "platforms": platforms,
                "account_handle": (account or {}).get("handle") or "",
                "schedule_at": None,
            }
            if provider == "mock":
                result = {
                    "status": "done",
                    "external_id": f"mock-due-{r['id']}",
                    "raw": {"mock": True, "due": True},
                }
            else:
                _mark_dispatching(r["id"], r["workspace_id"])
                result = _dispatch(provider, secret, conf, payload, account)
            _apply_result(r["id"], r["workspace_id"], r["post_id"], result, platforms)
            n += 1
        except Exception as e:
            _fail_job(r["id"], r["workspace_id"], str(e)[:300])
    return n
