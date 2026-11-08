"""AppSumo integration: the signed webhook, the OAuth redirect a buyer lands on, and the licence-key redeem box.

Env: APPSUMO_API_KEY (signs webhooks), APPSUMO_CLIENT_ID / APPSUMO_CLIENT_SECRET / APPSUMO_REDIRECT_URL (OAuth),
APPSUMO_BUY_URL (the product page on AppSumo).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from . import auth, crypto, db, lifetime
from .auth import Ctx

log = logging.getLogger("hooktrack.appsumo")
router = APIRouter(tags=["appsumo"])
MAX_SKEW_S = 15 * 60
PENDING_COOKIE = "ht_appsumo_license"
TOKEN_URL = "https://appsumo.com/openid/token/"
LICENSE_URL = "https://appsumo.com/openid/license_key/"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def status() -> dict:
    return {"api_key": bool(_env("APPSUMO_API_KEY")),
            "oauth": bool(_env("APPSUMO_CLIENT_ID") and _env("APPSUMO_CLIENT_SECRET")),
            "redirect_url": _env("APPSUMO_REDIRECT_URL"), "buy_url": _env("APPSUMO_BUY_URL")}


def sign(api_key: str, timestamp: str, body: bytes) -> str:
    return hmac.new(api_key.encode(), timestamp.encode() + body, hashlib.sha256).hexdigest()


def verify(api_key: str, timestamp: str, signature: str, body: bytes, now: Optional[float] = None) -> bool:
    if not (api_key and timestamp and signature):
        return False
    try:
        ts = float(timestamp)
    except ValueError:
        return False
    if ts > 1e12:                                   # milliseconds
        ts /= 1000
    if abs((now if now is not None else time.time()) - ts) > MAX_SKEW_S:
        return False
    return hmac.compare_digest(sign(api_key, timestamp, body), signature.strip().lower())


@router.post("/api/appsumo/webhook")
async def webhook(request: Request):
    key = _env("APPSUMO_API_KEY")
    if not key:
        raise HTTPException(503, "AppSumo is not configured")
    raw = await request.body()
    if not verify(key, request.headers.get("X-Appsumo-Timestamp", ""), request.headers.get("X-Appsumo-Signature", ""), raw):
        raise HTTPException(401, "Bad signature")
    try:
        payload = json.loads(raw or b"{}")
        if not isinstance(payload, dict):
            raise ValueError
    except ValueError:
        raise HTTPException(400, "Body is not JSON")
    import asyncio
    outcome = await asyncio.to_thread(lifetime.handle_event, payload)
    log.info("appsumo %s %s -> %s", payload.get("event"), str(payload.get("license_key"))[:8], outcome)
    return {"event": payload.get("event"), "success": True}


# ---------- the buyer lands here after activating on AppSumo ----------

def _exchange(code: str) -> str:
    """OAuth code -> access token -> licence key."""
    try:
        with httpx.Client(timeout=20) as c:
            t = c.post(TOKEN_URL, data={"grant_type": "authorization_code", "code": code,
                                        "client_id": _env("APPSUMO_CLIENT_ID"), "client_secret": _env("APPSUMO_CLIENT_SECRET"),
                                        "redirect_uri": _env("APPSUMO_REDIRECT_URL")})
            t.raise_for_status()
            token = t.json().get("access_token")
            r = c.get(LICENSE_URL, params={"access_token": token})
            r.raise_for_status()
            lic = r.json().get("license_key")
    except (httpx.HTTPError, ValueError) as e:
        log.warning("appsumo oauth exchange failed: %s", e)
        raise HTTPException(502, "Could not confirm your AppSumo licence. Paste the key on the Billing page instead.")
    if not lic:
        raise HTTPException(502, "AppSumo did not return a licence key")
    return str(lic)


def _pending(request: Request) -> str:
    raw = request.cookies.get(PENDING_COOKIE)
    if not raw:
        return ""
    try:
        data = json.loads(crypto.decrypt(raw))
        return data["k"] if data.get("exp", 0) > time.time() else ""
    except Exception:
        return ""


@router.get("/appsumo/callback")
def callback(request: Request, code: str = ""):
    if not code:
        return JSONResponse({"ok": True})                 # AppSumo pings the URL with no code to check it is alive
    lic = _exchange(code)
    ctx = auth.session_ctx(request)
    if ctx is not None and ctx.workspace_id and ctx.role in ("owner", "admin"):
        try:
            lifetime.claim(ctx.workspace_id, lic)
            return RedirectResponse("/app#/billing", status_code=303)
        except HTTPException:
            pass                                          # fall through: keep it for the Billing page
    resp = RedirectResponse("/app#/signup" if ctx is None else "/app#/billing", status_code=303)
    resp.set_cookie(PENDING_COOKIE, crypto.encrypt(json.dumps({"k": lic, "exp": time.time() + 3600})),
                    max_age=3600, httponly=True, samesite="lax", secure=request.url.scheme == "https", path="/")
    return resp


class RedeemIn(BaseModel):
    license_key: Optional[str] = None


@router.post("/api/billing/lifetime/redeem")
def redeem(b: RedeemIn, request: Request, ctx: Ctx = Depends(auth.admin)):
    key = (b.license_key or "").strip() or _pending(request)
    if not key:
        raise HTTPException(400, "Paste your AppSumo licence key")
    out = lifetime.claim(ctx.workspace_id, key)
    return {"ok": True, **out}
