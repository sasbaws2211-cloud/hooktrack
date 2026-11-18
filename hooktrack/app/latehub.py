"""HookTrack's own Late (Zernio) account, shared by customers who do not bring their own key.

The platform admin sets one key. Each workspace gets its own Late *profile*; the accounts a customer connects live in
that profile only, and every post is sent to accounts listed under it, never to an id supplied by the customer.
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

from . import crypto, db
from .sociavault import platform_delete, platform_get, platform_set

KEY_ENC, KEY_HINT = "late_api_key_enc", "late_key_hint"
PLATFORMS = ("tiktok", "instagram", "youtube", "twitter", "linkedin", "facebook")


class LateHubError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def base_url() -> str:
    return (os.environ.get("HOOKTRACK_LATE_BASE_URL") or "https://zernio.com/api/v1").rstrip("/")


def platform_key() -> str:
    enc = platform_get(KEY_ENC)
    if isinstance(enc, str) and enc:
        try:
            return (crypto.decrypt(enc) or "").strip()
        except Exception:
            return ""
    return (os.environ.get("HOOKTRACK_LATE_API_KEY") or "").strip()


def key_status() -> dict:
    hint = platform_get(KEY_HINT) or ""
    has_db = bool(platform_get(KEY_ENC))
    has_env = bool((os.environ.get("HOOKTRACK_LATE_API_KEY") or "").strip())
    return {"configured": has_db or has_env, "source": "platform" if has_db else ("env" if has_env else "none"),
            "key_hint": hint if has_db else ("from env" if has_env else "")}


def save_key(api_key: str, clear: bool = False) -> dict:
    if clear or not (api_key or "").strip():
        platform_delete(KEY_ENC)
        platform_delete(KEY_HINT)
        return key_status()
    k = api_key.strip()
    platform_set(KEY_ENC, crypto.encrypt(k))
    platform_set(KEY_HINT, (k[:4] + "..." + k[-4:]) if len(k) > 8 else "****")
    return key_status()


def available() -> bool:
    return bool(platform_key())


def _headers() -> dict:
    return {"Authorization": f"Bearer {platform_key()}", "Content-Type": "application/json",
            "User-Agent": "HookTrack-Publish/1.0"}


def _call(method: str, path: str, *, idempotency_key: str = "", timeout: float = 30.0, **kw) -> dict:
    if not available():
        raise LateHubError("Publishing is not set up on this server yet.")
    headers = _headers()
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.request(method, f"{base_url()}{path}", headers=headers, **kw)
    except httpx.HTTPError as error:
        raise LateHubError(f"Could not reach Zernio: {error}") from error
    if r.status_code >= 400:
        raise LateHubError(f"Late error {r.status_code}: {r.text[:200]}", r.status_code)
    try:
        return r.json() if r.content else {}
    except ValueError as error:
        raise LateHubError("Zernio returned an invalid response.") from error


def ensure_profile(ws: int) -> str:
    """This workspace's Late profile id, created on first use."""
    row = db.one("SELECT profile_id FROM workspace_late WHERE workspace_id=%s", (ws,))
    if row:
        return row["profile_id"]
    try:
        data = _call("POST", "/profiles", json={"name": f"hooktrack-{ws}"})
        pid = str((data.get("profile") or {}).get("_id") or "")
    except LateHubError as e:
        if e.status == 409:                      # created before but never recorded: reuse it
            data = _call("GET", "/profiles", params={"name": f"hooktrack-{ws}"})
            items = data.get("profiles") if isinstance(data, dict) else data
            pid = str(((items or [{}])[0]).get("_id") or "")
        elif e.status == 403:
            raise LateHubError("The publishing plan has no room for another customer profile.", 403)
        else:
            raise
    if not pid:
        raise LateHubError("Late did not return a profile id")
    db.execute("INSERT INTO workspace_late (workspace_id, profile_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (ws, pid))
    return pid


def _profile_accounts(ws: int) -> list[dict]:
    """All active social accounts under this workspace's Zernio profile."""
    pid = ensure_profile(ws)
    data = _call("GET", "/accounts", params={"profileId": pid})
    items = data.get("accounts") if isinstance(data, dict) else data
    gone = {r["account_id"] for r in db.query(
        "SELECT account_id FROM late_disconnected WHERE workspace_id=%s AND at > now() - interval '3 hours'", (ws,))}
    out = []
    for a in items or []:
        if not isinstance(a, dict):
            continue
        prof = a.get("profileId")
        prof_id = str(prof.get("_id") if isinstance(prof, dict) else prof or "")
        if prof_id != pid or a.get("isActive") is False:      # never trust the filter alone
            continue
        parent = a.get("parentAccountId")
        parent_id = str(parent.get("_id") if isinstance(parent, dict) else parent or "")
        out.append({"id": str(a.get("_id") or a.get("id") or ""), "platform": str(a.get("platform") or "").lower(),
                    "username": a.get("username") or "", "display_name": a.get("displayName") or "",
                    "parent_account_id": parent_id})
    return [a for a in out if a["id"] and a["id"] not in gone]


def accounts(ws: int) -> list[dict]:
    """The publishing accounts connected under this workspace's profile only."""
    return [a for a in _profile_accounts(ws)
            if a["platform"] not in ("metaads", "tiktokads", "googleads", "linkedinads")]


def ads_connections(ws: int) -> list[dict]:
    """The supported Ads connections under this workspace's profile only."""
    return [a for a in _profile_accounts(ws)
            if a["platform"] in ("metaads", "tiktokads", "googleads", "linkedinads")]


def ad_accounts(connection_id: str) -> list[dict]:
    """List only ad accounts usable by one verified ads connection."""
    data = _call("GET", "/ads/accounts", params={"accountId": connection_id, "inScopeOnly": "true"})
    items = data.get("adAccounts") or data.get("accounts") or data.get("data") or []
    if not isinstance(items, list):
        raise LateHubError("Zernio returned an invalid ad-account list.")
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        account_id = str(item.get("adAccountId") or item.get("id") or item.get("_id") or "")
        if not account_id:
            continue
        out.append({
            "id": account_id,
            "name": str(item.get("name") or item.get("displayName") or account_id)[:160],
            "currency": str(item.get("currency") or ""),
            "selected": item.get("selected") is not False,
        })
    return out


def connect_ads_url(ws: int, platform: str, redirect_url: str, posting_account_id: str) -> dict:
    """Start a workspace-scoped Ads authorization."""
    if platform not in ("meta", "tiktok", "google", "linkedin"):
        raise LateHubError("Choose Meta, TikTok, Google or LinkedIn Ads.")
    if platform == "google":
        data = _call("GET", "/connect/googleads/ads", params={
            "profileId": ensure_profile(ws),
            "redirect_url": redirect_url,
        })
        auth_url = data.get("authUrl") or ""
        if not data.get("alreadyConnected") and (not isinstance(auth_url, str) or not auth_url.startswith("https://")):
            raise LateHubError("Zernio did not return a valid Ads authorization link.")
        return {"auth_url": auth_url, "already_connected": bool(data.get("alreadyConnected"))}
    posting = {a["id"]: a for a in accounts(ws)}
    selected = posting.get(posting_account_id)
    if not selected:
        raise LateHubError("Choose a publishing account connected to this workspace.", 404)
    allowed = {
        "meta": ("facebook", "instagram"),
        "tiktok": ("tiktok",),
        "linkedin": ("linkedin",),
    }[platform]
    if selected["platform"] not in allowed:
        raise LateHubError("The selected publishing account does not match this ads platform.", 400)
    provider_platform = selected["platform"] if platform == "meta" else platform
    data = _call("GET", f"/connect/{provider_platform}/ads", params={
        "profileId": ensure_profile(ws),
        "accountId": posting_account_id,
        "redirect_url": redirect_url,
    })
    auth_url = data.get("authUrl") or ""
    if not data.get("alreadyConnected") and (not isinstance(auth_url, str) or not auth_url.startswith("https://")):
        raise LateHubError("Zernio did not return a valid Ads authorization link.")
    return {"auth_url": auth_url, "already_connected": bool(data.get("alreadyConnected"))}


def boost_post(body: dict, idempotency_key: str) -> dict:
    return _call("POST", "/ads/boost", json=body, idempotency_key=idempotency_key, timeout=300.0)


def create_standalone_ad(body: dict, idempotency_key: str) -> dict:
    return _call("POST", "/ads/create", json=body, idempotency_key=idempotency_key, timeout=750.0)


def update_ad_campaign_status(campaign_id: str, status: str, platform: str = "meta") -> dict:
    provider_platform = "facebook" if platform == "meta" else platform
    return _call("PUT", f"/ads/campaigns/{campaign_id}/status",
                 json={"status": status, "platform": provider_platform}, timeout=60.0)


def update_ad_set_status(ad_set_id: str, status: str, platform: str) -> dict:
    return _call("PUT", f"/ads/ad-sets/{ad_set_id}/status",
                 json={"status": status, "platform": platform}, timeout=60.0)


def update_ad_status(ad_id: str, status: str, platform: str) -> dict:
    return _call("PUT", f"/ads/{ad_id}/status",
                 json={"status": status, "platform": platform}, timeout=60.0)


def ad_campaign_tree(params: dict) -> dict:
    return _call("GET", "/ads/tree", params=params, timeout=90.0)


def ad_targeting_search(params: dict) -> dict:
    return _call("GET", "/ads/targeting/search", params=params, timeout=30.0)


def ad_tracking_tags(account_id: str, ad_account_id: str) -> list[dict]:
    data = _call("GET", f"/accounts/{account_id}/tracking-tags", params={"adAccountId": ad_account_id})
    tags = data.get("tags") if isinstance(data, dict) else None
    if not isinstance(tags, list):
        raise LateHubError("Zernio returned an invalid Meta pixel list.")
    return [tag for tag in tags if isinstance(tag, dict) and tag.get("kind") == "pixel"]


def disconnect(ws: int, account_id: str) -> dict:
    """Disconnect one of this workspace's accounts. Only an account listed under its own profile can be removed."""
    mine = {a["id"]: a for a in accounts(ws)}
    if account_id not in mine:
        raise LateHubError("That account is not connected to this workspace", 404)
    try:
        _call("DELETE", f"/accounts/{account_id}")
    except LateHubError as e:
        if e.status != 404:                       # 404 = already disconnected: the same end state
            raise
    a = mine[account_id]
    db.execute("INSERT INTO late_disconnected (workspace_id, account_id, platform) VALUES (%s,%s,%s) "
               "ON CONFLICT (workspace_id, account_id) DO UPDATE SET at = now(), platform = EXCLUDED.platform",
               (ws, account_id, a["platform"]))
    return {"ok": True, "platform": a["platform"], "username": a["username"]}


def connect_url(ws: int, platform: str, redirect_url: str) -> str:
    if platform not in PLATFORMS:
        raise LateHubError("Choose TikTok, Instagram, YouTube, X, LinkedIn or Facebook")
    pid = ensure_profile(ws)
    # connecting again (maybe the very account just disconnected): stop hiding that platform's old ones
    db.execute("DELETE FROM late_disconnected WHERE workspace_id=%s AND platform=%s", (ws, platform))
    data = _call("GET", f"/connect/{platform}", params={"profileId": pid, "redirect_url": redirect_url})
    url = data.get("authUrl") or ""
    if not url.startswith("https://"):
        raise LateHubError("Late did not return a sign-in address")
    return url
