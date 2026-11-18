"""Workspace-scoped Meta and TikTok post boosting through Zernio."""
from __future__ import annotations

import logging
import hmac
import os
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import db, latehub, media, sociavault
from .auth import Ctx, admin, current

log = logging.getLogger("hooktrack.ads")
router = APIRouter(prefix="/api/ads", tags=["ads"])


class ConnectAdsIn(BaseModel):
    platform: Literal["meta", "tiktok"]
    posting_account_id: str = Field(min_length=1, max_length=200)


class BoostIn(BaseModel):
    post_id: int
    platform: Literal["meta", "tiktok"]
    ad_connection_id: str = Field(min_length=1, max_length=200)
    ad_account_id: str = Field(min_length=1, max_length=200)
    campaign_name: str = Field(min_length=1, max_length=100)
    daily_budget: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    duration_days: int = Field(ge=1, le=30)
    goal: Literal["video_views", "awareness", "engagement"] = "video_views"
    special_ad_categories: list[Literal[
        "HOUSING", "EMPLOYMENT", "CREDIT", "SOCIAL_ISSUES_ELECTIONS_POLITICS"
    ]] = Field(default_factory=list, max_length=4)


class InterestIn(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)


class CampaignDraftIn(BaseModel):
    platform: Literal["meta", "tiktok"] = "meta"
    ad_connection_id: str = Field(min_length=1, max_length=200)
    ad_account_id: str = Field(min_length=1, max_length=200)
    campaign_name: str = Field(min_length=1, max_length=100)
    objective: Literal["awareness", "traffic", "video_views", "conversions"] = "traffic"
    daily_budget: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    end_date: date
    creative_type: Literal["image", "video"]
    media_asset_id: int | None = Field(default=None, gt=0)
    post_id: int | None = Field(default=None, gt=0)
    headline: str = Field(default="", max_length=255)
    body: str = Field(min_length=1, max_length=5000)
    link_url: str = Field(min_length=1, max_length=2048)
    call_to_action: Literal[
        "LEARN_MORE", "SHOP_NOW", "SIGN_UP", "CONTACT_US", "DOWNLOAD", "GET_OFFER", "GET_QUOTE",
        "SUBSCRIBE", "WATCH_MORE", "ADD_TO_CART", "APPLY_NOW", "BOOK_NOW", "BUY_TICKETS",
        "DONATE", "GET_DIRECTIONS", "LISTEN_NOW", "ORDER_NOW", "PLAY_GAME", "REQUEST_TIME",
        "SEE_MENU", "START_ORDER", "INSTALL_MOBILE_APP", "USE_APP", "BUY_NOW",
    ] = "LEARN_MORE"
    countries: list[str] = Field(default_factory=lambda: ["US"], max_length=25)
    age_min: int = Field(default=18, ge=18, le=65)
    age_max: int = Field(default=65, ge=18, le=65)
    gender: Literal["all", "male", "female"] = "all"
    interests: list[InterestIn] = Field(default_factory=list, max_length=25)
    placement_platforms: list[Literal["facebook", "instagram", "threads", "messenger"]] = Field(
        default_factory=list, max_length=4)
    special_ad_categories: list[Literal[
        "HOUSING", "EMPLOYMENT", "CREDIT", "FINANCIAL_PRODUCTS_SERVICES",
        "ISSUES_ELECTIONS_POLITICS", "ONLINE_GAMBLING_AND_GAMING",
    ]] = Field(default_factory=list, max_length=6)
    pixel_id: str = Field(default="", max_length=100)
    conversion_event: str = Field(default="PURCHASE", max_length=100)
    url_tags: str = Field(default="", max_length=1000)


class CampaignStatusIn(BaseModel):
    status: Literal["active", "paused"]


def _handle(value: str, platform: str) -> str:
    normalized, _ = sociavault.normalize_handle(value, platform)
    return normalized.casefold()


def _matching_tiktok_account(post: dict, posting_accounts: list[dict]) -> dict | None:
    post_handle = _handle(post["handle"], "TikTok")
    return next(
        (account for account in posting_accounts
         if account["platform"] == "tiktok"
         and _handle(account.get("username") or "", "TikTok") == post_handle),
        None,
    )


def _post_for_boost(workspace_id: int, post_id: int, platform: str) -> dict:
    row = db.one(
        """SELECT p.id, p.status, p.external_id, p.content_type, p.hook, p.caption, p.views,
                  a.handle, a.platform AS account_platform
             FROM posts p JOIN accounts a ON a.id=p.account_id
            WHERE p.id=%s AND p.workspace_id=%s AND a.workspace_id=%s""",
        (post_id, workspace_id, workspace_id),
    )
    if not row:
        raise HTTPException(404, "Published post not found in this workspace.")
    if row["status"] not in ("posted", "analysed") or not row["external_id"]:
        raise HTTPException(400, "Only a published post with a platform post ID can be boosted.")
    post_platform = (row["account_platform"] or "").strip().casefold()
    if platform == "tiktok" and post_platform != "tiktok":
        raise HTTPException(400, "TikTok Ads can only boost a published TikTok post.")
    if platform == "meta" and post_platform not in ("facebook", "instagram"):
        raise HTTPException(400, "Meta Ads can only boost a published Facebook or Instagram post.")
    if platform == "tiktok" and row["content_type"] not in ("short_video", "long_video"):
        raise HTTPException(400, "TikTok Spark Ads require a published video post.")
    row["external_id"] = str(row["external_id"])
    return row


def _connections(workspace_id: int) -> list[dict]:
    try:
        return latehub.ads_connections(workspace_id)
    except latehub.LateHubError as error:
        raise HTTPException(502, str(error)) from error


def _connection(workspace_id: int, platform: str, account_id: str) -> dict:
    expected = "metaads" if platform == "meta" else "tiktokads"
    connection = next(
        (a for a in _connections(workspace_id) if a["platform"] == expected and a["id"] == account_id), None)
    if not connection:
        raise HTTPException(400, f"Connect a {platform.title()} Ads account to this workspace first.")
    return connection


def _selected_ad_account(
    workspace_id: int, connection_id: str, ad_account_id: str, platform: str = "meta",
) -> dict:
    connection = _connection(workspace_id, platform, connection_id)
    try:
        accounts = latehub.ad_accounts(connection["id"])
    except latehub.LateHubError as error:
        raise HTTPException(502, str(error)) from error
    account = next((item for item in accounts
                    if item["id"] == ad_account_id and item.get("selected")), None)
    if not account:
        raise HTTPException(400, f"Choose an ad account selected for this workspace's {platform.title()} Ads connection.")
    return connection


def _asset_url(workspace_id: int, asset_id: int) -> str:
    row = db.one(
        "SELECT file_path FROM ad_campaign_assets WHERE id=%s AND workspace_id=%s",
        (asset_id, workspace_id),
    )
    if not row or not media._abs(row["file_path"]):
        raise HTTPException(404, "Campaign media was not found. Upload it again before creating this campaign.")
    try:
        base = media._public_base_or_raise("campaign media")
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    exp = int(time.time()) + media.SIGNED_TTL_S
    sig = media._sign(workspace_id, asset_id, row["file_path"], exp)
    return f"{base}/api/ads/media/ads/{workspace_id}/{asset_id}/{exp}/{sig}"


def _post_media_url(workspace_id: int, post_id: int, creative_type: str, platform: str = "meta") -> str:
    allowed_platforms = ("facebook", "instagram") if platform == "meta" else ("tiktok",)
    post = db.one(
        """SELECT p.id, p.video_file, p.video_url FROM posts p
             JOIN accounts a ON a.id=p.account_id
            WHERE p.id=%s AND p.workspace_id=%s AND p.status IN ('posted', 'analysed')
              AND a.workspace_id=%s AND lower(a.platform)=ANY(%s)""",
        (post_id, workspace_id, workspace_id, list(allowed_platforms)),
    )
    if not post:
        raise HTTPException(404, f"That published {platform.title()} post is no longer available in this workspace.")
    if creative_type == "video":
        if post["video_file"]:
            try:
                return media.signed_url(workspace_id, post_id)
            except ValueError as error:
                raise HTTPException(400, str(error)) from error
        url = str(post.get("video_url") or "")
        if urlparse(url).scheme == "https" and urlparse(url).hostname:
            return url
        raise HTTPException(400, "That post has no reusable video file. Choose another post or upload media.")
    if platform == "tiktok":
        raise HTTPException(400, "TikTok campaigns require a video creative.")
    image = db.one(
        """SELECT id FROM post_media
            WHERE post_id=%s AND workspace_id=%s ORDER BY position, id LIMIT 1""",
        (post_id, workspace_id),
    )
    if not image:
        raise HTTPException(400, "That post has no reusable image file. Choose another post or upload media.")
    try:
        return media.signed_image_url_by_id(workspace_id, post_id, image["id"])
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


def _url_tags(value: str) -> list[dict]:
    tags = []
    for pair in value.split("&"):
        if not pair.strip():
            continue
        key, separator, item_value = pair.partition("=")
        key, item_value = key.strip(), item_value.strip()
        if (not separator or not key or len(key) > 100 or len(item_value) > 300
                or not all(char.isalnum() or char in "_.-" for char in key)):
            raise HTTPException(400, "Tracking tags must be written as key=value pairs separated by &.")
        tags.append({"key": key, "value": item_value})
    if len(tags) > 30:
        raise HTTPException(400, "Use no more than 30 URL tracking tags.")
    return tags


def _campaign_request(workspace_id: int, values: dict) -> tuple[dict, dict]:
    platform = values.get("platform", "meta")
    connection = _selected_ad_account(
        workspace_id, values["ad_connection_id"], values["ad_account_id"], platform)
    end_date = values["end_date"]
    if isinstance(end_date, str):
        end_date = date.fromisoformat(end_date)
    if (values["media_asset_id"] is None) == (values["post_id"] is None):
        raise HTTPException(400, "Choose exactly one uploaded asset or existing published post.")
    if values["age_max"] < values["age_min"]:
        raise HTTPException(400, "Maximum age must be equal to or above minimum age.")
    if not values["campaign_name"].strip() or not values["body"].strip():
        raise HTTPException(400, "Enter a campaign name and primary text.")
    if platform == "meta" and not values["headline"].strip():
        raise HTTPException(400, "Enter a campaign headline.")
    if platform == "tiktok" and Decimal(str(values["daily_budget"])) < Decimal("20"):
        raise HTTPException(400, "TikTok's minimum daily budget is 20 in the ad account's currency.")
    countries = [str(code).strip().upper() for code in values["countries"]]
    if not countries:
        raise HTTPException(400, "Choose at least one target country.")
    if any(len(code) != 2 or not code.isalpha() for code in countries):
        raise HTTPException(400, "Countries must be ISO two-letter country codes.")
    if len(set(countries)) != len(countries):
        raise HTTPException(400, "Choose each country only once.")
    parsed_url = urlparse(values["link_url"])
    if parsed_url.scheme != "https" or not parsed_url.hostname or parsed_url.username or parsed_url.password:
        raise HTTPException(400, "The landing page URL must be a complete HTTPS URL.")
    if end_date <= date.today():
        raise HTTPException(400, "Choose a campaign end date after today.")
    if values["objective"] == "video_views" and values["creative_type"] != "video":
        raise HTTPException(400, "The video views objective requires a video creative.")
    if values["objective"] == "conversions" and not values["pixel_id"]:
        raise HTTPException(400, f"Choose a {platform.title()} pixel for a conversions campaign.")
    if platform == "tiktok" and values["creative_type"] != "video":
        raise HTTPException(400, "TikTok campaigns require a video creative.")
    if values["special_ad_categories"] and values["interests"]:
        raise HTTPException(400, "Remove interest targeting for ads with a special ad category.")
    if values["special_ad_categories"] and platform == "meta" and (
        values["age_min"] != 18 or values["age_max"] != 65 or values["gender"] != "all"
    ):
        raise HTTPException(400, "Meta restricts age and gender targeting for special ad categories. Use all ages and genders.")
    if platform == "tiktok" and any(category not in ("HOUSING", "EMPLOYMENT", "CREDIT")
                                    for category in values["special_ad_categories"]):
        raise HTTPException(400, "TikTok supports Housing, Employment, and Credit special ad categories.")
    if platform == "tiktok" and values["special_ad_categories"] and values["gender"] != "all":
        raise HTTPException(400, "TikTok restricts gender targeting for special ad categories.")
    if platform == "tiktok" and values["url_tags"]:
        raise HTTPException(400, "TikTok Ads does not support URL tracking tags.")
    if values["pixel_id"] and not values["pixel_id"].isdigit():
        raise HTTPException(400, f"Choose a valid numeric {platform.title()} pixel ID.")
    if platform == "tiktok" and values["placement_platforms"]:
        raise HTTPException(400, "TikTok campaigns use automatic TikTok placements.")
    if values["placement_platforms"] and not set(values["placement_platforms"]) <= {
        "facebook", "instagram", "threads", "messenger",
    }:
        raise HTTPException(400, "Choose supported Meta placement platforms.")

    if values["media_asset_id"] is not None:
        asset = db.one(
            """SELECT content_type FROM ad_campaign_assets
                WHERE id=%s AND workspace_id=%s""",
            (values["media_asset_id"], workspace_id),
        )
        if not asset:
            raise HTTPException(404, "Campaign media was not found in this workspace.")
        is_video = asset["content_type"].startswith("video/")
        if is_video != (values["creative_type"] == "video"):
            raise HTTPException(400, "The selected campaign media type does not match the creative.")
        if platform == "tiktok" and not is_video:
            raise HTTPException(400, "TikTok campaigns require a video creative.")
        media_url = _asset_url(workspace_id, values["media_asset_id"])
    else:
        media_url = _post_media_url(workspace_id, values["post_id"], values["creative_type"], platform)

    request_body = {
        "accountId": connection["id"],
        "adAccountId": values["ad_account_id"],
        "name": values["campaign_name"].strip(),
        "goal": values["objective"],
        "budgetAmount": float(values["daily_budget"]),
        "budgetType": "daily",
        "endDate": f"{end_date.isoformat()}T23:59:59Z",
        "body": values["body"].strip(),
        "linkUrl": values["link_url"].strip(),
        "callToAction": values["call_to_action"],
        "status": "PAUSED",
        "countries": countries,
    }
    if platform == "meta":
        request_body["headline"] = values["headline"].strip()
        request_body["advantageAudience"] = 0
        if not values["special_ad_categories"]:
            request_body["ageMin"] = values["age_min"]
            request_body["ageMax"] = values["age_max"]
        if values["creative_type"] == "video":
            request_body["video"] = {"url": media_url}
        else:
            request_body["imageUrl"] = media_url
        if values["gender"] != "all" and not values["special_ad_categories"]:
            request_body["gender"] = values["gender"]
    else:
        request_body["imageUrl"] = media_url
        request_body["ageMin"] = values["age_min"]
        request_body["ageMax"] = values["age_max"]
        if values["gender"] != "all":
            request_body["gender"] = values["gender"]
    if values["interests"]:
        request_body["interests"] = [
            {"id": interest["id"], "name": interest["name"]} for interest in values["interests"]
        ]
    if platform == "meta" and values["placement_platforms"]:
        request_body["placements"] = {"publisherPlatforms": values["placement_platforms"]}
    if values["special_ad_categories"]:
        request_body["specialAdCategories"] = values["special_ad_categories"]
    if platform == "meta" and values["pixel_id"]:
        request_body["tracking"] = {"pixelId": values["pixel_id"]}
    if platform == "meta" and values["url_tags"]:
        request_body.setdefault("tracking", {})["urlTags"] = _url_tags(values["url_tags"])
    if values["objective"] == "conversions":
        allowed_events = (
            {"PURCHASE", "COMPLETE_REGISTRATION", "ADD_TO_CART", "INITIATE_CHECKOUT",
             "ADD_PAYMENT_INFO", "SUBSCRIBE", "START_TRIAL", "VIEW_CONTENT", "SEARCH"}
            if platform == "meta"
            else {"ON_WEB_ORDER", "INITIATE_ORDER", "ON_WEB_CART", "ON_WEB_REGISTER", "ON_WEB_DETAIL",
                  "FORM", "ON_WEB_SEARCH", "ON_WEB_ADD_TO_WISHLIST", "LANDING_PAGE_VIEW"}
        )
        if values["conversion_event"] not in allowed_events:
            raise HTTPException(400, "Choose a supported conversion event for this ad platform.")
        request_body["promotedObject"] = {
            "pixelId": values["pixel_id"],
            "customEventType": values["conversion_event"],
        }
    return request_body, connection


def _campaign_create_result(
    workspace_id: int, draft_id: int, request_body: dict, idempotency_key: str, platform: str = "meta",
) -> dict:
    try:
        result = latehub.create_standalone_ad(request_body, idempotency_key)
    except latehub.LateHubError as error:
        unknown = error.status is None or error.status in (408, 409, 429) or error.status >= 500
        db.execute(
            """UPDATE ad_campaign_drafts SET status=%s, error=%s, updated_at=now()
                WHERE id=%s AND workspace_id=%s""",
            ("unknown" if unknown else "failed", str(error)[:1000], draft_id, workspace_id),
        )
        raise HTTPException(502 if unknown else error.status or 502, str(error)) from error
    except Exception as error:
        log.exception("Zernio campaign create failed unexpectedly (draft %s)", draft_id)
        db.execute(
            """UPDATE ad_campaign_drafts SET status='unknown', error=%s, updated_at=now()
                WHERE id=%s AND workspace_id=%s""",
            ("The provider response was not confirmed. Retry safely to check the same request.",
             draft_id, workspace_id),
        )
        raise HTTPException(502, "The provider response was not confirmed. Retry this campaign safely.")
    ad = result.get("ad") if isinstance(result, dict) else None
    if not isinstance(ad, dict) or not ad.get("platformCampaignId") or not ad.get("_id"):
        db.execute(
            """UPDATE ad_campaign_drafts SET status='unknown', response_json=%s, error=%s, updated_at=now()
                WHERE id=%s AND workspace_id=%s""",
            (db.Json(result or {}), "Provider created the request but returned no campaign identifiers.",
             draft_id, workspace_id),
        )
        raise HTTPException(502, "Meta may have created the campaign, but the provider response lacked campaign IDs. "
                                 "Check Ads Manager before retrying.")
    if platform == "tiktok" or str(ad.get("configuredStatus") or "").upper() != "PAUSED":
        try:
            if platform == "meta":
                latehub.update_ad_campaign_status(str(ad["platformCampaignId"]), "paused")
            else:
                latehub.update_ad_campaign_status(str(ad["platformCampaignId"]), "paused", platform)
        except latehub.LateHubError as error:
            db.execute(
                """UPDATE ad_campaign_drafts SET status='unknown', response_json=%s,
                          platform_campaign_id=%s, platform_ad_set_id=%s, zernio_ad_id=%s,
                          error=%s, updated_at=now()
                    WHERE id=%s AND workspace_id=%s""",
                (db.Json(result), str(ad["platformCampaignId"]), str(ad.get("platformAdSetId") or ""),
                 str(ad["_id"]), str(error)[:1000], draft_id, workspace_id),
            )
            raise HTTPException(
                502, "Meta created the campaign, but HookTrack could not confirm it is paused. "
                     "Check Ads Manager and pause it there before retrying."
            ) from error
    db.execute(
        """UPDATE ad_campaign_drafts SET status='paused', response_json=%s,
                  platform_campaign_id=%s, platform_ad_set_id=%s, zernio_ad_id=%s,
                  error='', updated_at=now()
            WHERE id=%s AND workspace_id=%s""",
        (db.Json(result), str(ad["platformCampaignId"]), str(ad.get("platformAdSetId") or ""),
         str(ad["_id"]), draft_id, workspace_id),
    )
    return {"id": draft_id, "status": "paused", "campaign": ad, "message": result.get("message", "")}


def _provider_result(workspace_id: int, boost_id: int, request_body: dict, idempotency_key: str) -> dict:
    try:
        result = latehub.boost_post(request_body, idempotency_key)
    except latehub.LateHubError as error:
        status = "unknown" if error.status is None or error.status in (408, 409, 429) or error.status >= 500 else "failed"
        db.execute(
            """UPDATE ad_boosts SET status=%s, error=%s, updated_at=now()
                WHERE id=%s AND workspace_id=%s""",
            (status, str(error)[:1000], boost_id, workspace_id),
        )
        raise HTTPException(502 if status == "unknown" else error.status or 502, str(error)) from error
    except Exception:
        log.exception("Zernio boost request failed unexpectedly (boost %s)", boost_id)
        db.execute(
            """UPDATE ad_boosts SET status='unknown', error=%s, updated_at=now()
                WHERE id=%s AND workspace_id=%s""",
            ("The provider response was not confirmed. Retry safely to check the same request.", boost_id, workspace_id),
        )
        raise HTTPException(502, "The provider response was not confirmed. Retry this boost safely from Ads.")

    db.execute(
        """UPDATE ad_boosts SET status='created', response_json=%s, error='', updated_at=now()
            WHERE id=%s AND workspace_id=%s""",
        (db.Json(result), boost_id, workspace_id),
    )
    return {"id": boost_id, "status": "created", "campaign_name": request_body["campaignName"]}


@router.get("")
def ads_home(ctx: Ctx = Depends(current)):
    workspace_id = ctx.workspace_id
    db.execute(
        """UPDATE ad_boosts SET status='unknown',
                  error='The previous request did not finish on the server. Retry safely to confirm its result.',
                  updated_at=now()
            WHERE workspace_id=%s AND status='pending' AND updated_at < now() - interval '5 minutes'""",
        (workspace_id,),
    )
    db.execute(
        """UPDATE ad_campaign_drafts SET status='unknown',
                  error='The previous create did not finish on the server. Retry safely with its saved request.',
                  updated_at=now()
            WHERE workspace_id=%s AND status='creating' AND updated_at < now() - interval '5 minutes'""",
        (workspace_id,),
    )
    posting_accounts = []
    ads_connections = []
    if latehub.available():
        try:
            posting_accounts = latehub.accounts(workspace_id)
            for connection in _connections(workspace_id):
                item = {**connection, "ad_accounts": [], "error": ""}
                try:
                    item["ad_accounts"] = latehub.ad_accounts(connection["id"])
                except latehub.LateHubError as error:
                    item["error"] = str(error)
                ads_connections.append(item)
        except latehub.LateHubError as error:
            raise HTTPException(502, str(error)) from error

    posts = db.query(
        """SELECT p.id, p.hook, p.caption, p.views, p.comments, p.post_date, p.content_type,
                  p.external_id, a.handle, a.platform
             FROM posts p JOIN accounts a ON a.id=p.account_id
            WHERE p.workspace_id=%s AND a.workspace_id=%s
              AND p.status IN ('posted', 'analysed')
              AND COALESCE(p.external_id, '') <> ''
              AND lower(a.platform) IN ('tiktok', 'instagram', 'facebook')
            ORDER BY p.views DESC NULLS LAST, p.post_date DESC, p.id DESC LIMIT 100""",
        (workspace_id, workspace_id),
    )
    for post in posts:
        if (post.get("platform") or "").strip().casefold() == "tiktok":
            matching_account = _matching_tiktok_account(post, posting_accounts)
            post["tiktok_posting_account_id"] = matching_account["id"] if matching_account else ""
    existing_media = []
    media_posts = db.query(
        """SELECT p.id, p.hook, p.caption, p.video_file, p.video_url, p.video_file_name,
                  a.handle, a.platform
             FROM posts p JOIN accounts a ON a.id=p.account_id
            WHERE p.workspace_id=%s AND a.workspace_id=%s AND p.status IN ('posted', 'analysed')
              AND lower(a.platform) IN ('facebook', 'instagram', 'tiktok')
            ORDER BY p.post_date DESC, p.id DESC LIMIT 100""",
        (workspace_id, workspace_id),
    )
    for post in media_posts:
        creative_type = ""
        if post["video_file"] or post["video_url"]:
            creative_type = "video"
        elif (post["platform"] or "").casefold() != "tiktok":
            image = db.one(
                "SELECT id FROM post_media WHERE workspace_id=%s AND post_id=%s ORDER BY position, id LIMIT 1",
                (workspace_id, post["id"]),
            )
            if image:
                creative_type = "image"
        if creative_type:
            existing_media.append({
                "post_id": post["id"],
                "creative_type": creative_type,
                "label": (post["hook"] or post["caption"] or post["video_file_name"] or
                          f"{post['platform']} post {post['id']}")[:140],
                "platform": post["platform"],
                "handle": post["handle"],
            })
    drafts = db.query(
        """SELECT id, campaign_name, ad_account_id, platform, status, draft_json, platform_campaign_id,
                  platform_ad_set_id, zernio_ad_id, pending_status, error, created_at, updated_at,
                  provider_requested_at > now() - interval '23 hours' AS retry_safe
             FROM ad_campaign_drafts WHERE workspace_id=%s ORDER BY created_at DESC LIMIT 50""",
        (workspace_id,),
    )
    assets = db.query(
        """SELECT id, file_name, file_size, content_type, created_at
             FROM ad_campaign_assets WHERE workspace_id=%s ORDER BY created_at DESC LIMIT 100""",
        (workspace_id,),
    )
    boosts = db.query(
        """SELECT id, post_id, post_label, platform, campaign_name, daily_budget,
                  duration_days, goal, status, error, created_at
             FROM ad_boosts WHERE workspace_id=%s
            ORDER BY created_at DESC LIMIT 50""",
        (workspace_id,),
    )
    return {
        "configured": latehub.available(),
        "can_manage": ctx.role in ("owner", "admin"),
        "posting_accounts": posting_accounts,
        "ads_connections": ads_connections,
        "posts": posts,
        "existing_media": existing_media,
        "campaign_drafts": drafts,
        "campaign_assets": assets,
        "boosts": boosts,
    }


@router.post("/assets/upload")
async def upload_campaign_asset(request: Request, file: UploadFile = File(...), ctx: Ctx = Depends(admin)):
    limit_image = 30 * 1024 * 1024
    limit_video = media.max_bytes()
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit_video + 4096:
        raise HTTPException(413, "That campaign asset is too large.")
    folder = media.UPLOAD_ROOT / str(ctx.workspace_id) / "ads"
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f".incoming-{secrets.token_hex(8)}"
    size, kind = 0, None
    try:
        with open(tmp, "wb") as output:
            while True:
                chunk = await file.read(media.CHUNK)
                if not chunk:
                    break
                if kind is None:
                    kind = media.sniff_image(chunk[:16])
                    if kind == "webp":
                        kind = None
                    if kind is None:
                        kind = media.sniff(chunk[:16])
                        if kind == "webm":
                            kind = None
                    if kind is None:
                        raise HTTPException(415, "Upload a JPEG, PNG, MP4 or MOV image/video.")
                size += len(chunk)
                limit = limit_image if kind in ("jpg", "png") else limit_video
                if size > limit:
                    raise HTTPException(413, f"That campaign asset exceeds the {limit // (1024 * 1024)} MB limit.")
                output.write(chunk)
        if not size or kind is None:
            raise HTTPException(400, "The file is empty.")
        name = (file.filename or "campaign-asset").replace("\\", "/").rsplit("/", 1)[-1][:120]
        content_type = {"jpg": "image/jpeg", "png": "image/png", "mp4": "video/mp4", "mov": "video/quicktime"}[kind]
        final = folder / f"{secrets.token_urlsafe(18)}.{kind}"
        os.replace(tmp, final)
        relative = f"{ctx.workspace_id}/ads/{final.name}"
        try:
            row = db.one(
                """INSERT INTO ad_campaign_assets
                       (workspace_id, file_path, file_name, file_size, content_type)
                   VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                (ctx.workspace_id, relative, name, size, content_type),
            )
        except Exception:
            final.unlink(missing_ok=True)
            raise
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return {"id": row["id"], "name": name, "size": size, "content_type": content_type}


@router.get("/media/ads/{ws}/{asset_id}/{exp}/{sig}")
def signed_campaign_asset(ws: int, asset_id: int, exp: int, sig: str):
    row = db.one(
        """SELECT file_path, content_type FROM ad_campaign_assets
            WHERE id=%s AND workspace_id=%s""",
        (asset_id, ws),
    )
    if (not row or exp < int(time.time())
            or not hmac.compare_digest(sig, media._sign(ws, asset_id, row["file_path"], exp))):
        raise HTTPException(404, "Not found")
    path = media._abs(row["file_path"])
    if not path:
        raise HTTPException(404, "Not found")
    return FileResponse(path, media_type=row["content_type"],
                        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})


@router.post("/drafts")
def create_campaign_draft(body: CampaignDraftIn, ctx: Ctx = Depends(admin)):
    values = body.model_dump(mode="json")
    _selected_ad_account(ctx.workspace_id, values["ad_connection_id"], values["ad_account_id"], values["platform"])
    if (values["media_asset_id"] is None) == (values["post_id"] is None):
        raise HTTPException(400, "Choose exactly one uploaded asset or existing published post.")
    draft = db.one(
        """INSERT INTO ad_campaign_drafts
               (workspace_id, campaign_name, ad_account_id, platform, status, draft_json, idempotency_key)
           VALUES (%s,%s,%s,%s,'draft',%s,%s) RETURNING id, status, created_at""",
        (ctx.workspace_id, values["campaign_name"].strip(), values["ad_account_id"], values["platform"],
         db.Json(values), secrets.token_urlsafe(24)),
    )
    return {"id": draft["id"], "status": draft["status"], "created_at": draft["created_at"]}


@router.put("/drafts/{draft_id}")
def update_campaign_draft(draft_id: int, body: CampaignDraftIn, ctx: Ctx = Depends(admin)):
    values = body.model_dump(mode="json")
    _selected_ad_account(ctx.workspace_id, values["ad_connection_id"], values["ad_account_id"], values["platform"])
    if (values["media_asset_id"] is None) == (values["post_id"] is None):
        raise HTTPException(400, "Choose exactly one uploaded asset or existing published post.")
    row = db.one(
        """UPDATE ad_campaign_drafts SET campaign_name=%s, ad_account_id=%s, platform=%s, draft_json=%s,
                  status='draft', idempotency_key=%s, request_json='{}'::jsonb,
                  response_json='{}'::jsonb, platform_campaign_id='', platform_ad_set_id='',
                  zernio_ad_id='', pending_status='', error='', updated_at=now()
            WHERE id=%s AND workspace_id=%s AND status IN ('draft', 'failed')
            RETURNING id, status""",
        (values["campaign_name"].strip(), values["ad_account_id"], values["platform"], db.Json(values),
         secrets.token_urlsafe(24), draft_id, ctx.workspace_id),
    )
    if not row:
        exists = db.one("SELECT id FROM ad_campaign_drafts WHERE id=%s AND workspace_id=%s",
                        (draft_id, ctx.workspace_id))
        raise HTTPException(409 if exists else 404,
                            "Only a saved draft can be edited." if exists else "Campaign draft not found.")
    return row


@router.delete("/drafts/{draft_id}")
def delete_campaign_draft(draft_id: int, ctx: Ctx = Depends(admin)):
    deleted = db.one(
        """DELETE FROM ad_campaign_drafts WHERE id=%s AND workspace_id=%s
              AND status IN ('draft', 'failed') RETURNING id""",
        (draft_id, ctx.workspace_id),
    )
    if not deleted:
        exists = db.one("SELECT id FROM ad_campaign_drafts WHERE id=%s AND workspace_id=%s",
                        (draft_id, ctx.workspace_id))
        raise HTTPException(409 if exists else 404,
                            "Only an unlaunched draft can be deleted." if exists else "Campaign draft not found.")
    return {"ok": True}


def _submit_saved_campaign(workspace_id: int, draft_id: int, retry: bool = False) -> dict:
    if retry:
        row = db.one(
            """SELECT id, status, platform, draft_json, request_json, idempotency_key, provider_requested_at
                 FROM ad_campaign_drafts WHERE id=%s AND workspace_id=%s""",
            (draft_id, workspace_id),
        )
        if not row:
            raise HTTPException(404, "Campaign draft not found.")
        if row["status"] != "unknown":
            raise HTTPException(409, "Only an unconfirmed campaign create can be retried.")
        fresh = db.one(
            "SELECT id FROM ad_campaign_drafts WHERE id=%s AND workspace_id=%s "
            "AND provider_requested_at > now() - interval '23 hours'",
            (draft_id, workspace_id),
        )
        if not fresh:
            raise HTTPException(409, "The provider's idempotency window may have expired. Check Meta Ads Manager "
                                     "before creating another campaign.")
        body = row["request_json"]
        key = row["idempotency_key"]
    else:
        row = db.one(
            """SELECT id, status, platform, draft_json, idempotency_key
                 FROM ad_campaign_drafts WHERE id=%s AND workspace_id=%s""",
            (draft_id, workspace_id),
        )
        if not row:
            raise HTTPException(404, "Campaign draft not found.")
        if row["status"] != "draft":
            raise HTTPException(409, "Only a saved draft can be submitted. Check the draft's current status.")
        body, _ = _campaign_request(workspace_id, row["draft_json"])
        key = row["idempotency_key"]
        claimed = db.one(
            """UPDATE ad_campaign_drafts SET status='creating', request_json=%s,
                      provider_requested_at=now(), error='', updated_at=now()
                WHERE id=%s AND workspace_id=%s AND status='draft' RETURNING id""",
            (db.Json(body), draft_id, workspace_id),
        )
        if not claimed:
            raise HTTPException(409, "This campaign is already being submitted.")
    claimed = db.one(
        """UPDATE ad_campaign_drafts SET status='creating', error='', updated_at=now()
            WHERE id=%s AND workspace_id=%s AND status='unknown' RETURNING id""",
        (draft_id, workspace_id),
    ) if retry else True
    if not claimed:
        raise HTTPException(409, "This campaign is already being submitted.")
    return _campaign_create_result(workspace_id, draft_id, body, key, row["platform"])


@router.post("/drafts/{draft_id}/create")
def create_campaign_from_draft(draft_id: int, ctx: Ctx = Depends(admin)):
    return _submit_saved_campaign(ctx.workspace_id, draft_id)


@router.post("/drafts/{draft_id}/retry")
def retry_campaign_create(draft_id: int, ctx: Ctx = Depends(admin)):
    return _submit_saved_campaign(ctx.workspace_id, draft_id, retry=True)


@router.put("/drafts/{draft_id}/status")
def update_campaign_status(draft_id: int, body: CampaignStatusIn, ctx: Ctx = Depends(admin)):
    row = db.one(
        """SELECT id, status, platform, platform_campaign_id, pending_status
             FROM ad_campaign_drafts WHERE id=%s AND workspace_id=%s""",
        (draft_id, ctx.workspace_id),
    )
    if not row:
        raise HTTPException(404, "Campaign draft not found.")
    if row["status"] not in ("paused", "active"):
        raise HTTPException(409, "Create the campaign as paused before changing its delivery status.")
    if row["status"] == body.status and not row["pending_status"]:
        return {"id": draft_id, "status": body.status, "unchanged": True}
    db.execute(
        """UPDATE ad_campaign_drafts SET pending_status=%s, error='', updated_at=now()
            WHERE id=%s AND workspace_id=%s""",
        (body.status, draft_id, ctx.workspace_id),
    )
    try:
        if row["platform"] == "meta":
            result = latehub.update_ad_campaign_status(row["platform_campaign_id"], body.status)
        else:
            result = latehub.update_ad_campaign_status(row["platform_campaign_id"], body.status, row["platform"])
    except latehub.LateHubError as error:
        unknown = error.status is None or error.status in (408, 409, 429) or error.status >= 500
        if unknown:
            db.execute(
                """UPDATE ad_campaign_drafts SET error=%s, updated_at=now()
                    WHERE id=%s AND workspace_id=%s""",
                (str(error)[:1000], draft_id, ctx.workspace_id),
            )
        else:
            db.execute(
                """UPDATE ad_campaign_drafts SET pending_status='', error=%s, updated_at=now()
                    WHERE id=%s AND workspace_id=%s""",
                (str(error)[:1000], draft_id, ctx.workspace_id),
            )
        raise HTTPException(502, str(error)) from error
    db.execute(
        """UPDATE ad_campaign_drafts SET status=%s, pending_status='', error='',
                  response_json=%s, updated_at=now()
            WHERE id=%s AND workspace_id=%s""",
        (body.status, db.Json(result), draft_id, ctx.workspace_id),
    )
    return {"id": draft_id, "status": body.status, "provider": result}


@router.get("/targeting-search")
def targeting_search(
    q: str, ad_connection_id: str, platform: Literal["meta", "tiktok"] = "meta",
    dimension: Literal["interest", "geo", "behavior", "interestKeyword", "hashtag"] = "interest",
    ctx: Ctx = Depends(current),
):
    if len(q.strip()) < 2 or len(q) > 100:
        raise HTTPException(400, "Enter at least two characters to search Meta targeting.")
    if platform == "meta" and dimension not in ("interest", "geo", "behavior"):
        raise HTTPException(400, "That targeting search is only available for TikTok Ads.")
    if platform == "tiktok" and dimension == "geo":
        raise HTTPException(400, "Choose a country in the TikTok campaign country field.")
    connection = _connection(ctx.workspace_id, platform, ad_connection_id)
    try:
        params = {"accountId": connection["id"], "q": q.strip(), "dimension": dimension}
        if platform == "tiktok":
            params["platform"] = "tiktok"
        return latehub.ad_targeting_search(params)
    except latehub.LateHubError as error:
        raise HTTPException(502, str(error)) from error


@router.get("/pixels")
def list_ad_pixels(
    ad_connection_id: str, ad_account_id: str, platform: Literal["meta", "tiktok"] = "meta",
    ctx: Ctx = Depends(current),
):
    connection = _selected_ad_account(ctx.workspace_id, ad_connection_id, ad_account_id, platform)
    try:
        return {"pixels": latehub.ad_tracking_tags(connection["id"], ad_account_id)}
    except latehub.LateHubError as error:
        raise HTTPException(502, str(error)) from error


@router.get("/campaigns")
def campaign_reporting(ctx: Ctx = Depends(current)):
    connections = _connections(ctx.workspace_id)
    accounts_by_platform = {"meta": [], "tiktok": []}
    for connection in connections:
        platform = {"metaads": "meta", "tiktokads": "tiktok"}.get(connection["platform"])
        if not platform:
            continue
        try:
            accounts_by_platform[platform].extend(
                account["id"] for account in latehub.ad_accounts(connection["id"])
                if account.get("selected")
            )
        except latehub.LateHubError as error:
            raise HTTPException(502, str(error)) from error
    campaigns = []
    for platform, account_ids in accounts_by_platform.items():
        if not account_ids:
            continue
        params = {
            "profileId": latehub.ensure_profile(ctx.workspace_id),
            "adAccountId": ",".join(account_ids[:50]),
            "fromDate": (date.today() - timedelta(days=30)).isoformat(),
            "toDate": date.today().isoformat(),
            "status": "not_deleted",
        }
        if platform == "tiktok":
            params["platform"] = platform
        try:
            result = latehub.ad_campaign_tree(params)
        except latehub.LateHubError as error:
            raise HTTPException(502, str(error)) from error
        if not isinstance(result, dict) or not isinstance(result.get("campaigns"), list):
            raise HTTPException(502, f"Zernio returned an invalid {platform.title()} campaign report.")
        campaigns.extend(result["campaigns"])
    return {
        "campaigns": campaigns,
        "from_date": (date.today() - timedelta(days=30)).isoformat(),
        "to_date": date.today().isoformat(),
    }


@router.post("/connect")
def connect_ads(body: ConnectAdsIn, request: Request, ctx: Ctx = Depends(admin)):
    base_url = (os.environ.get("HOOKTRACK_PUBLIC_URL") or str(request.base_url)).rstrip("/")
    try:
        result = latehub.connect_ads_url(
            ctx.workspace_id, body.platform, f"{base_url}/#/ads", body.posting_account_id)
    except latehub.LateHubError as error:
        raise HTTPException(error.status if error.status in (400, 401, 402, 403, 404, 409, 429, 503) else 502,
                            str(error)) from error
    return result


def _boost_request(workspace_id: int, body: BoostIn) -> tuple[dict, dict]:
    post = _post_for_boost(workspace_id, body.post_id, body.platform)
    if body.goal == "video_views" and post["content_type"] not in ("short_video", "long_video"):
        raise HTTPException(400, "The video-views goal requires a video post. Choose awareness or engagement instead.")
    connection = _connection(workspace_id, body.platform, body.ad_connection_id)
    try:
        ad_accounts = latehub.ad_accounts(connection["id"])
    except latehub.LateHubError as error:
        raise HTTPException(502, str(error)) from error
    ad_account = next((a for a in ad_accounts if a["id"] == body.ad_account_id and a["selected"]), None)
    if not ad_account:
        raise HTTPException(400, "Choose an ad account selected for this workspace's Ads connection.")

    account_id = connection["id"]
    if body.platform == "tiktok":
        try:
            posting_accounts = latehub.accounts(workspace_id)
        except latehub.LateHubError as error:
            raise HTTPException(502, str(error)) from error
        matching = _matching_tiktok_account(post, posting_accounts)
        if not matching:
            raise HTTPException(
                400, "Connect the TikTok publishing account for this post in Settings → Publishing, then link it "
                     "to TikTok Ads. Spark Ads must use the post's own TikTok account.")
        if connection.get("parent_account_id") and connection["parent_account_id"] != matching["id"]:
            raise HTTPException(
                400, "This TikTok Ads connection is linked to a different publishing account. Reconnect it to the "
                     "account that owns this post.")
        account_id = matching["id"]

    name = body.campaign_name.strip()
    if not name:
        raise HTTPException(400, "Enter a campaign name.")
    now = datetime.now(timezone.utc)
    request_body = {
        "accountId": account_id,
        "adAccountId": ad_account["id"],
        "platformPostId": post["external_id"],
        "name": f"{name} - Boost",
        "campaignName": name,
        "goal": body.goal,
        "budgetAmount": float(body.daily_budget),
        "budgetType": "daily",
        "startDate": now.isoformat(timespec="seconds"),
        "endDate": (now + timedelta(days=body.duration_days)).isoformat(timespec="seconds"),
    }
    if body.special_ad_categories:
        if body.platform == "tiktok" and any(category not in ("HOUSING", "EMPLOYMENT", "CREDIT")
                                            for category in body.special_ad_categories):
            raise HTTPException(400, "TikTok supports Housing, Employment, and Credit special ad categories.")
        request_body["specialAdCategories"] = body.special_ad_categories
    return request_body, post


@router.post("/boost")
def boost_post(body: BoostIn, ctx: Ctx = Depends(admin)):
    request_body, post = _boost_request(ctx.workspace_id, body)
    idempotency_key = secrets.token_urlsafe(24)
    inserted = db.one(
        """INSERT INTO ad_boosts
               (workspace_id, post_id, post_label, platform, ad_account_id, campaign_name,
                daily_budget, duration_days, goal, status, idempotency_key, request_json)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s)
           ON CONFLICT (workspace_id, post_id, platform)
             WHERE post_id IS NOT NULL AND status IN ('pending', 'created', 'unknown')
           DO NOTHING
           RETURNING id""",
        (ctx.workspace_id, body.post_id, (post["hook"] or post["caption"] or f"Post {body.post_id}")[:300],
         body.platform, body.ad_account_id, body.campaign_name.strip(), body.daily_budget, body.duration_days,
         body.goal, idempotency_key, db.Json(request_body)),
    )
    if not inserted:
        existing = db.one(
            """SELECT status FROM ad_boosts
                WHERE workspace_id=%s AND post_id=%s AND platform=%s
                  AND status IN ('pending', 'created', 'unknown')""",
            (ctx.workspace_id, body.post_id, body.platform),
        )
        if existing and existing["status"] == "created":
            raise HTTPException(409, "This post already has a boost for that platform.")
        if existing and existing["status"] == "pending":
            raise HTTPException(409, "This boost is already being submitted. Check Ads again before retrying.")
        if existing and existing["status"] == "unknown":
            raise HTTPException(409, "The previous submission may have reached the provider. Retry that saved boost "
                                     "from the Ads page before starting another.")
        raise HTTPException(409, "A boost for this post is already being submitted.")
    return _provider_result(ctx.workspace_id, inserted["id"], request_body, idempotency_key)


@router.post("/boost/{boost_id}/retry")
def retry_boost(boost_id: int, ctx: Ctx = Depends(admin)):
    row = db.one(
        """SELECT id, status, idempotency_key, request_json FROM ad_boosts
            WHERE id=%s AND workspace_id=%s""",
        (boost_id, ctx.workspace_id),
    )
    if not row:
        raise HTTPException(404, "Boost not found.")
    if row["status"] != "unknown":
        raise HTTPException(409, "Only a boost with an unconfirmed provider response can be retried.")
    return _provider_result(ctx.workspace_id, row["id"], row["request_json"], row["idempotency_key"])
