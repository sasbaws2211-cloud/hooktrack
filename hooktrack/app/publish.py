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
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import secrets

import httpx

from . import crypto, db

log = logging.getLogger("hooktrack.publish")

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


def _media_for(post: dict, conf: dict, when=None, stored: str = "") -> str:
    """Where the provider downloads the video from. An uploaded file is handed over as a short-lived signed link;
    that needs a public address, and if there is none we say so rather than send a link nobody can open."""
    if post.get("video_file"):
        from . import media
        ttl = media.SIGNED_TTL_S
        if when is not None:
            ttl = max(ttl, int((when - datetime.now(timezone.utc)).total_seconds()) + 24 * 3600)
        try:
            return media.signed_url(post["workspace_id"], post["id"], ttl=ttl)
        except ValueError as e:
            raise PublishError(str(e))
    return (stored or post.get("video_url") or "").strip() or (conf.get("default_media_url") or "").strip()


def get_config(workspace_id: int) -> dict:
    from . import devmock
    out = _get_config(workspace_id)
    from . import billing, latehub
    out["managed_available"] = latehub.available()
    out["held"] = billing.publishing_held(workspace_id)
    out["held_message"] = billing.TRIAL_PUBLISH_MSG if out["held"] else ""
    out["providers"] = devmock.visible(PROVIDERS)
    if out.get("provider") == "mock" and not devmock.allowed():
        out.update({"enabled": False, "provider": "upload_post", "mock_disabled": True, "note": devmock.MOCK_NOTE})
    if out["managed_available"]:
        # HookTrack's own publishing connection is the default for every workspace: no key, no provider to choose.
        # A key a workspace saved earlier is left in place, but it is not used while the platform connection exists.
        cfg = {k: v for k, v in (out.get("config") or {}).items() if k not in ("account_ids", "base_url")}
        cfg["managed"] = True
        out.update({"enabled": True, "managed": True, "provider": "late", "config": cfg, "has_secret": False,
                    "key_hint": "", "source": "platform"})
    return out


def _get_config(workspace_id: int) -> dict:
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
        "managed": bool(cfg.get("managed")) and (row.get("provider") == "late"),
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
    from . import devmock
    if provider == "mock" and not devmock.allowed():
        raise PublishError("The mock provider is for development only and is switched off on this server.")
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
    schedule_local: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """Publish a HookTrack post via the configured provider. Explicit action only.

    schedule_local is a wall-clock time ("2026-10-21T19:00") in the WORKSPACE's time zone; schedule_at is an
    absolute ISO time. Either one schedules; the local form is what the editor sends, so a user in another
    browser time zone still gets the time they meant.
    """
    if schedule_local and not schedule_at:
        schedule_at = local_to_utc_iso(workspace_id, schedule_local)
    cfg = get_config(workspace_id)
    if not cfg["enabled"] and not dry_run:
        raise PublishError("Social publish add-on is not enabled")
    from . import billing
    if not dry_run and billing.publishing_held(workspace_id):
        raise PublishError(billing.TRIAL_PUBLISH_MSG)
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
    if not caption and not post.get("video_url") and not post.get("video_file"):
        raise PublishError("Post needs a caption or a video before publishing")

    provider = cfg["provider"]
    conf = dict(cfg.get("config") or {})
    secret = _resolve_secret(workspace_id)
    if provider == "late" and conf.get("managed"):
        # HookTrack's own Late account: its key, this workspace's profile, and no account ids from the customer
        from . import latehub
        if not latehub.available():
            raise PublishError("Publishing is not set up on this server yet.")
        secret = latehub.platform_key()
        conf.pop("account_ids", None)
        conf.pop("base_url", None)
        conf["_base"] = latehub.base_url()
        try:
            conf["_managed_accounts"] = latehub.accounts(workspace_id)
        except latehub.LateHubError as e:
            raise PublishError(str(e))
        from . import billing
        allowed = billing.limit_for(workspace_id, "connected")
        have = len(conf["_managed_accounts"])
        if allowed != -1 and have > allowed:
            raise PublishError(f"Your plan includes {allowed} connected publishing account{'' if allowed == 1 else 's'} "
                               f"and {have} are connected. Renew the add-on under Billing, or disconnect one.")

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

    when_hint = None
    try:
        when_hint = _parse_schedule(schedule_at)
    except PublishError:
        pass
    if "_managed_accounts" in conf and not dry_run:
        # refuse now, not hours later when a queued job comes due: a platform with no connected account cannot publish
        ids = _managed_ids(conf["_managed_accounts"], platforms, (account or {}).get("handle") or "")
        missing = [p for p in platforms if p not in ids]
        if missing:
            names = {"tiktok": "TikTok", "instagram": "Instagram", "youtube": "YouTube"}
            raise PublishError("Connect your " + ", ".join(names.get(m, m) for m in missing) +
                               " account first (Connect in the publish window, or Settings, Publishing).", 409)
    media_url = _media_for(post, conf, when_hint)
    if provider != "mock" and not media_url and any(x.lower() in ("tiktok", "instagram", "youtube") for x in platforms):
        raise PublishError("Attach a video first: this card has no video file or video link to publish.")
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
        stored = {**payload, "media_url": "" if post.get("video_file") else payload["media_url"]}
        job = db.one(
            """INSERT INTO publish_jobs
               (workspace_id, post_id, provider, status, request_json, scheduled_for)
               VALUES (%s,%s,%s,'queued',%s,%s) RETURNING *""",
            (workspace_id, post_id, provider, db.Json(stored), when),
        )
        db.execute(
            """UPDATE posts SET scheduled_for=%s, publish_platforms=%s
               WHERE id=%s AND workspace_id=%s""",
            (when, ",".join(platforms)[:200], post_id, workspace_id),
        )
        return _job_out(job)

    stored = {**payload, "media_url": "" if post.get("video_file") else payload["media_url"]}
    job = db.one(
        """INSERT INTO publish_jobs
           (workspace_id, post_id, provider, status, request_json, scheduled_for)
           VALUES (%s,%s,%s,'pending',%s,%s) RETURNING *""",
        (workspace_id, post_id, provider, db.Json(stored), when),
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
    from . import cache
    cache.bump(workspace_id)
    db.execute(
        """UPDATE publish_jobs SET status='failed', error=%s, updated_at=now()
           WHERE id=%s AND workspace_id=%s""",
        (error[:500], job_id, workspace_id),
    )


_PLATFORM_HOSTS = ("tiktok.com", "instagram.com", "youtube.com", "youtu.be")


def _find_post_url(obj, depth: int = 0) -> str:
    """The first public address of a post inside a provider's answer (their shapes all differ), or ''."""
    if depth > 6:
        return ""
    if isinstance(obj, str):
        o = obj.strip()
        if o.startswith("https://") and any(h in o.split("/")[2] for h in _PLATFORM_HOSTS if len(o.split("/")) > 2):
            return o[:500]
        return ""
    if isinstance(obj, dict):
        for v in obj.values():
            u = _find_post_url(v, depth + 1)
            if u:
                return u
    if isinstance(obj, list):
        for v in obj:
            u = _find_post_url(v, depth + 1)
            if u:
                return u
    return ""


def _apply_result(job_id: int, workspace_id: int, post_id: int, result: dict, platforms: list) -> dict:
    from . import cache
    cache.bump(workspace_id)
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
                 external_post_id=%s,
                 published_at=COALESCE(published_at, now()),
                 publish_platforms=%s,
                 scheduled_for=NULL
               WHERE id=%s AND workspace_id=%s""",
            (external_id, ",".join(platforms)[:200], post_id, workspace_id),
        )
        from . import activation
        activation.mark(workspace_id, "first_publish")
        url = _find_post_url(raw)                     # so its real results can be matched without pasting the link
        if url:
            db.execute("UPDATE posts SET url=%s WHERE id=%s AND workspace_id=%s AND url=''", (url, post_id, workspace_id))
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


def _managed_ids(accts: list, platforms: list, handle: str) -> dict:
    """{platform: accountId} from the workspace's own connected accounts: the matching username, else the first."""
    h = handle.lstrip("@").lower()
    out: dict = {}
    for p in platforms:
        same = [a for a in accts if a["platform"] == p.lower()]
        pick = next((a for a in same if h and a["username"].lstrip("@").lower() == h), None) or (same[0] if same else None)
        if pick:
            out[p] = pick["id"]
    return out


def _late_account_ids(client, base: str, headers: dict, conf: dict, platforms: list, handle: str) -> dict:
    """{platform: accountId} for the connected profiles. conf['account_ids'] (a {platform: id} object) wins;
    otherwise the connected account whose username matches the handle, else the first one of that platform."""
    forced = conf.get("account_ids")
    out: dict = dict(forced) if isinstance(forced, dict) else {}
    need = [p for p in platforms if p not in out]
    if not need:
        return out
    r = client.get(f"{base}/accounts", headers=headers)
    if r.status_code >= 400:
        raise PublishError(f"Late error {r.status_code} listing connected accounts: {r.text[:200]}", r.status_code)
    data = r.json() if r.content else {}
    items = data.get("accounts") if isinstance(data, dict) else data
    h = handle.lstrip("@").lower()
    for p in need:
        same = [a for a in (items or []) if isinstance(a, dict) and str(a.get("platform", "")).lower() == p.lower()]
        pick = next((a for a in same if h and str(a.get("username") or a.get("name") or "").lstrip("@").lower() == h), None)             or (same[0] if same else None)
        if pick and (pick.get("_id") or pick.get("id")):
            out[p] = str(pick.get("_id") or pick.get("id"))
    return out


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
        # Late (now Zernio): platforms are objects {platform, accountId}; the accountId comes from the connected profiles.
        headers = {
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "User-Agent": "HookTrack-Publish/1.0",
        }
        base = (conf.get("_base") or conf.get("base_url") or "https://getlate.dev/api/v1").rstrip("/")
        with httpx.Client(timeout=120.0) as client:
            if "_managed_accounts" in conf:
                ids = _managed_ids(conf["_managed_accounts"], platforms, (account or {}).get("handle") or "")
            else:
                ids = _late_account_ids(client, base, headers, conf, platforms, (account or {}).get("handle") or "")
            missing = [p for p in platforms if p not in ids]
            if missing:
                raise PublishError("No connected " + ", ".join(missing) +
                                   " account. Connect it under Settings, Publishing, then try again.")
            body: dict = {
                "content": caption,
                "platforms": [{"platform": p, "accountId": ids[p]} for p in platforms],
            }
            if media_url:
                body["mediaItems"] = [{"type": "video", "url": media_url}]
            if schedule_at:
                body["scheduledFor"] = schedule_at
                body["timezone"] = "UTC"
            else:
                body["publishNow"] = True
            r = client.post(f"{base}/posts", json=body,
                            headers={**headers, "Idempotency-Key": f"ht-{payload.get('post_id')}-{secrets.token_hex(6)}"})
        if r.status_code >= 400:
            raise PublishError(f"Late error {r.status_code}: {r.text[:240]}", r.status_code)
        data = r.json() if r.content else {}
        post = data.get("post") if isinstance(data.get("post"), dict) else data
        eid = str(post.get("_id") or post.get("id") or data.get("id") or "")
        failed = [x for x in (post.get("platforms") or []) if isinstance(x, dict) and str(x.get("status")).lower() == "failed"]
        if failed:
            why = failed[0].get("error") or failed[0].get("errorMessage") or "the platform refused the post"
            return {"status": "failed", "external_id": eid, "error": f"Late: {why}"[:300], "raw": data}
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
    if provider == "late" and cfg.get("managed"):
        from . import latehub
        try:
            n = len(latehub.accounts(workspace_id))
        except latehub.LateHubError as e:
            raise PublishError(str(e), e.status)
        return {"ok": True, "message": f"HookTrack's publishing connection works: {n} account{'' if n == 1 else 's'} connected"}
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


# ---------- time zone, rescheduling and suggested slots ----------
DEFAULT_SLOTS = ["09:00", "13:00", "19:00"]     # local times a post is offered at when the workspace has none of its own
SLOT_GAP_HOURS = 2                               # an account is never offered a slot this close to one it already has
SUPPORTED_PLATFORMS = ["tiktok", "instagram", "youtube"]


def _tz(workspace_id: int):
    from zoneinfo import ZoneInfo
    from . import logic
    return ZoneInfo(logic.workspace_tz(workspace_id))


def _slot_times(workspace_id: int) -> list:
    conf = (_get_config(workspace_id).get("config") or {})     # one query: the slot list needs no billing or provider checks
    times = []
    for t in (conf.get("slots") or DEFAULT_SLOTS):
        try:
            hh, mm = str(t).split(":")
            times.append((int(hh), int(mm)))
        except ValueError:
            continue
    return sorted(set(times)) or [(9, 0), (13, 0), (19, 0)]


def local_to_utc_iso(workspace_id: int, local: str) -> str:
    """A wall-clock time in the workspace's zone -> absolute UTC ISO. Refuses anything that is not a date and time."""
    try:
        naive = datetime.fromisoformat(str(local).strip())
    except ValueError:
        raise PublishError("Invalid time (use a date and time, e.g. 2026-10-21T19:00)")
    if naive.tzinfo is not None:
        return naive.astimezone(timezone.utc).isoformat()
    return naive.replace(tzinfo=_tz(workspace_id)).astimezone(timezone.utc).isoformat()


def reschedule(workspace_id: int, post_id: int, *, schedule_at: Optional[str] = None,
               schedule_local: Optional[str] = None) -> dict:
    """Move a post's still-queued publish to a new time. A job the provider already holds cannot be moved here."""
    if schedule_local and not schedule_at:
        schedule_at = local_to_utc_iso(workspace_id, schedule_local)
    when = _parse_schedule(schedule_at)
    if not when:
        raise PublishError("Pick a date and time")
    if when <= datetime.now(timezone.utc):
        raise PublishError("The new time must be in the future")
    row = db.one(
        """UPDATE publish_jobs SET scheduled_for=%s, updated_at=now()
           WHERE workspace_id=%s AND post_id=%s AND status='queued' RETURNING *""",
        (when, workspace_id, post_id))
    if not row:
        raise PublishError("This post has no queued publish to move. If it was scheduled at the provider, "
                           "cancel it on the Scheduled page and schedule it again.")
    db.execute("UPDATE posts SET scheduled_for=%s WHERE id=%s AND workspace_id=%s", (when, post_id, workspace_id))
    return _job_out(row)


def suggest_slots(workspace_id: int, account_id: Optional[int] = None, n: int = 4, days: int = 7) -> list:
    """The next free slots for an account: the workspace's slot times, skipping the past and anything within
    SLOT_GAP_HOURS of a publish that account already has queued. These are scheduling defaults, not a prediction."""
    from datetime import timedelta
    tz = _tz(workspace_id)
    times = _slot_times(workspace_id)
    taken = [r["scheduled_for"] for r in db.query(
        """SELECT j.scheduled_for FROM publish_jobs j JOIN posts p ON p.id = j.post_id
           WHERE j.workspace_id=%s AND j.status='queued' AND j.scheduled_for IS NOT NULL
             AND (%s::int IS NULL OR p.account_id = %s)""", (workspace_id, account_id, account_id))]
    now = datetime.now(timezone.utc)
    today = now.astimezone(tz).date()
    out = []
    for d in range(days + 1):
        day = today + timedelta(days=d)
        for hh, mm in times:
            local = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
            utc = local.astimezone(timezone.utc)
            if utc < now + timedelta(minutes=15):
                continue
            if any(abs((utc - t).total_seconds()) < SLOT_GAP_HOURS * 3600 for t in taken):
                continue
            out.append({"local": local.strftime("%Y-%m-%dT%H:%M"), "utc": utc.isoformat(),
                        "label": local.strftime("%a %d %b, %H:%M")})
            if len(out) >= n:
                return out
    return out


def list_scheduled(workspace_id: int, limit: int = 100) -> list:
    rows = db.query(
        """SELECT j.*, p.hook, p.caption, p.video_url, p.status AS post_status, p.account_id, a.handle
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
    from . import billing
    for r in rows:
        try:
            if billing.access_blocked(r["workspace_id"]):
                continue                    # stays queued: it goes out once the workspace is paid up again
        except Exception:
            pass
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
                "media_url": _media_for(post, conf, None, req.get("media_url") or ""),
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


# ---------- bringing the posted link back ----------
# A publish-now answer sometimes already holds the posted address (it is saved in _apply_result). A scheduled post
# only ever answers "scheduled", and TikTok finishes processing after the answer, so the address has to be fetched
# later: this asks Late about each post whose card still has no link, and fills it in once the platform has one.

def _late_conf(workspace_id: int):
    """(secret, base url) for asking Late about this workspace's posts, or None when it doesn't publish through Late."""
    cfg = get_config(workspace_id)
    if cfg.get("provider") != "late":
        return None
    conf = dict(cfg.get("config") or {})
    if conf.get("managed"):
        from . import latehub
        return (latehub.platform_key(), latehub.base_url()) if latehub.available() else None
    secret = _resolve_secret(workspace_id)
    return (secret, (conf.get("base_url") or "https://getlate.dev/api/v1").rstrip("/")) if secret else None


def _late_get_post(secret: str, base: str, post_id: str) -> dict:
    with httpx.Client(timeout=30.0) as client:
        r = client.get(f"{base}/posts/{post_id}", headers={"Authorization": f"Bearer {secret}",
                                                           "User-Agent": "HookTrack-Publish/1.0"})
    if r.status_code >= 400:
        raise PublishError(f"Late error {r.status_code}: {r.text[:160]}", r.status_code)
    data = r.json() if r.content else {}
    return data.get("post") if isinstance(data.get("post"), dict) else data


def sync_posted_links(limit: int = 20) -> dict:
    """Fill each card's Posted video link from Late once the platform has published it. Cheap and idempotent:
    scheduled posts are asked about every 5 minutes, just-published ones every minute for the first two days."""
    out = {"checked": 0, "linked": 0, "published": 0, "failed": 0}
    rows = db.query(
        """SELECT j.id, j.workspace_id, j.post_id, j.external_id, j.status, j.request_json
           FROM publish_jobs j JOIN posts p ON p.id = j.post_id AND p.workspace_id = j.workspace_id
           WHERE j.provider = 'late' AND j.external_id <> '' AND p.url = ''
             AND ((j.status = 'scheduled' AND j.created_at > now() - interval '30 days'
                   AND j.updated_at < now() - interval '5 minutes')
               OR (j.status = 'done' AND j.created_at > now() - interval '2 days'
                   AND j.updated_at < now() - interval '1 minute'))
           ORDER BY j.updated_at LIMIT %s""", (limit,))
    for r in rows:
        if not db.one("UPDATE publish_jobs SET updated_at = now() WHERE id=%s AND updated_at < now() - interval '50 seconds' "
                      "RETURNING id", (r["id"],)):
            continue                                   # another worker has just looked at it
        try:
            conf = _late_conf(r["workspace_id"])
            if not conf:
                continue
            post = _late_get_post(conf[0], conf[1], r["external_id"])
        except Exception as e:
            log.info("could not read Late post %s: %s", r["external_id"], e)
            continue
        out["checked"] += 1
        req = r.get("request_json") or {}
        if isinstance(req, str):
            try:
                req = json.loads(req)
            except Exception:
                req = {}
        wanted = [str(p).lower() for p in (req.get("platforms") or [])]
        plats = [x for x in (post.get("platforms") or []) if isinstance(x, dict)]
        plats.sort(key=lambda x: 0 if str(x.get("platform", "")).lower() in wanted else 1)
        url = ""
        for x in plats:
            url = _find_post_url(x.get("platformPostUrl") or "")
            if url:
                break
        status = str(post.get("status") or "").lower()
        ws, pid = r["workspace_id"], r["post_id"]
        if url:
            db.execute("UPDATE posts SET url=%s WHERE id=%s AND workspace_id=%s AND url=''", (url, pid, ws))
            out["linked"] += 1
        if status == "published" or url:
            if r["status"] == "scheduled":
                db.execute("UPDATE publish_jobs SET status='done', updated_at=now() WHERE id=%s", (r["id"],))
                db.execute("UPDATE posts SET published_at=COALESCE(published_at, now()), scheduled_for=NULL "
                           "WHERE id=%s AND workspace_id=%s", (pid, ws))
                out["published"] += 1
        elif status in ("failed", "partial"):
            why = next((x.get("error") or x.get("errorMessage") for x in plats if x.get("error") or x.get("errorMessage")),
                       "the platform refused the post")
            db.execute("UPDATE publish_jobs SET status='failed', error=%s, updated_at=now() WHERE id=%s",
                       (f"Late: {why}"[:300], r["id"]))
            out["failed"] += 1
    return out
