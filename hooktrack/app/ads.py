"""Workspace-scoped Meta, TikTok, Google and LinkedIn ads through Zernio."""
from __future__ import annotations

import logging
import hmac
import os
import re
import secrets
import struct
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator

from . import db, latehub, media, sociavault
from .auth import Ctx, admin, current

log = logging.getLogger("hooktrack.ads")
router = APIRouter(prefix="/api/ads", tags=["ads"])
_META_DYNAMIC_CTA_TYPES = frozenset("""
BOOK_TRAVEL CONTACT_US DONATE DONATE_NOW DOWNLOAD GET_DIRECTIONS GO_LIVE INTERESTED LEARN_MORE
SEE_DETAILS LIKE_PAGE MESSAGE_PAGE RAISE_MONEY SAVE SEND_TIP SHOP_NOW SIGN_UP VIEW_INSTAGRAM_PROFILE
INSTAGRAM_MESSAGE LOYALTY_LEARN_MORE PURCHASE_GIFT_CARDS PAY_TO_ACCESS SEE_MORE TRY_IN_CAMERA
WHATSAPP_LINK GET_IN_TOUCH TRY_NOW ASK_A_QUESTION START_A_CHAT CHAT_NOW ASK_US CHAT_WITH_US
SHOP_ON_RETAILER BOOK_NOW CHECK_AVAILABILITY ORDER_NOW WHATSAPP_MESSAGE GET_MOBILE_APP
INSTALL_MOBILE_APP USE_MOBILE_APP INSTALL_APP USE_APP PLAY_GAME TRY_DEMO WATCH_VIDEO WATCH_MORE
OPEN_LINK NO_BUTTON LISTEN_MUSIC MOBILE_DOWNLOAD GET_OFFER GET_OFFER_VIEW BUY_NOW BUY_TICKETS
UPDATE_APP BET_NOW ADD_TO_CART SELL_NOW GET_SHOWTIMES LISTEN_NOW GET_EVENT_TICKETS REMIND_ME
SEARCH_MORE PRE_REGISTER SWIPE_UP_PRODUCT SWIPE_UP_SHOP PLAY_GAME_ON_FACEBOOK VISIT_WORLD
OPEN_INSTANT_APP JOIN_GROUP GET_PROMOTIONS SEND_UPDATES INQUIRE_NOW VISIT_PROFILE CHAT_ON_WHATSAPP
EXPLORE_MORE CONFIRM JOIN_CHANNEL MAKE_AN_APPOINTMENT ASK_ABOUT_SERVICES BOOK_A_CONSULTATION
GET_A_QUOTE BUY_VIA_MESSAGE ASK_FOR_MORE_INFO VIEW_PRODUCT VIEW_CHANNEL WATCH_LIVE_VIDEO
JOIN_LIVE_VIDEO IMAGINE WATCH_NOW STREAM_NOW BUY_ACTIVITY_TICKETS CALL MISSED_CALL CALL_NOW
CALL_ME APPLY_NOW BUY GET_QUOTE SUBSCRIBE RECORD_NOW VOTE_NOW GIVE_FREE_RIDES REGISTER_NOW
OPEN_MESSENGER_EXT EVENT_RSVP CIVIC_ACTION SEND_INVITES REFER_FRIENDS REQUEST_TIME SEE_MENU SEARCH
TRY_IT TRY_ON LINK_CARD DIAL_CODE FIND_YOUR_GROUPS START_ORDER
""".split())


class ConnectAdsIn(BaseModel):
    platform: Literal["meta", "tiktok", "google", "linkedin"]
    posting_account_id: str = Field(default="", max_length=200)
    login_mode: Literal["classic", "business"] = "classic"


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


class GeoTargetIn(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    radius: int | None = Field(default=None, ge=17, le=100)
    distance_unit: Literal["kilometer", "mile"] | None = None


class MetaCarouselCardIn(BaseModel):
    media_asset_id: int = Field(gt=0)
    headline: str = Field(default="", max_length=255)
    description: str = Field(default="", max_length=255)
    link_url: str = Field(default="", max_length=2048)


class MetaPlacementRuleIn(BaseModel):
    media_asset_id: int = Field(gt=0)
    publisher_platforms: list[Literal[
        "facebook", "instagram", "threads", "messenger", "audience_network", "whatsapp",
    ]] = Field(default_factory=list, max_length=6)
    facebook_positions: list[Literal[
        "feed", "right_hand_column", "marketplace", "story", "search", "instream_video",
        "facebook_reels", "facebook_reels_overlay", "profile_feed", "notification",
    ]] = Field(default_factory=list, max_length=10)
    instagram_positions: list[Literal[
        "stream", "story", "explore", "explore_home", "reels", "profile_feed", "ig_search", "profile_reels",
    ]] = Field(default_factory=list, max_length=8)
    messenger_positions: list[Literal["messenger_home", "sponsored_messages", "story"]] = Field(
        default_factory=list, max_length=3)
    audience_network_positions: list[Literal["classic", "rewarded_video"]] = Field(default_factory=list, max_length=2)
    threads_positions: list[Literal["threads_stream"]] = Field(default_factory=list, max_length=1)
    whatsapp_positions: list[Literal["status"]] = Field(default_factory=list, max_length=1)
    headline: str = Field(default="", max_length=255)
    body: str = Field(default="", max_length=5000)
    description: str = Field(default="", max_length=255)


class CampaignDraftIn(BaseModel):
    platform: Literal["meta", "tiktok", "google", "linkedin"] = "meta"
    ad_connection_id: str = Field(min_length=1, max_length=200)
    ad_account_id: str = Field(min_length=1, max_length=200)
    campaign_name: str = Field(min_length=1, max_length=100)
    objective: Literal[
        "awareness", "traffic", "video_views", "conversions", "engagement", "lead_generation",
        "lead_conversion", "app_promotion", "catalog_sales", "page_likes", "page_visits",
    ] = "traffic"
    daily_budget: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    end_date: date
    creative_type: Literal["image", "video", "text"] = "image"
    meta_creative_format: Literal["single", "carousel", "placement_assets", "dynamic"] = "single"
    meta_carousel_cards: list[MetaCarouselCardIn] = Field(default_factory=list, max_length=10)
    meta_placement_asset_type: Literal["image", "video"] = "image"
    meta_default_media_asset_id: int | None = Field(default=None, gt=0)
    meta_placement_rules: list[MetaPlacementRuleIn] = Field(default_factory=list, max_length=10)
    meta_dynamic_media_type: Literal["image", "video"] = "image"
    meta_dynamic_ad_format: Literal["default", "SINGLE_IMAGE", "CAROUSEL_IMAGE", "SINGLE_VIDEO"] = "default"
    meta_dynamic_media_asset_ids: list[int] = Field(default_factory=list, max_length=10)
    meta_dynamic_bodies: list[str] = Field(default_factory=list, max_length=5)
    meta_dynamic_titles: list[str] = Field(default_factory=list, max_length=5)
    meta_dynamic_descriptions: list[str] = Field(default_factory=list, max_length=5)
    meta_dynamic_link_urls: list[str] = Field(default_factory=list, max_length=10)
    meta_dynamic_call_to_action_types: list[str] = Field(default_factory=list, max_length=120)
    meta_enhance_cta: Literal["default", "OPT_IN", "OPT_OUT"] = "default"
    meta_image_brightness_and_contrast: Literal["default", "OPT_IN", "OPT_OUT"] = "default"
    meta_text_optimizations: Literal["default", "OPT_IN", "OPT_OUT"] = "default"
    meta_multi_advertiser: Literal["default", "OPT_OUT"] = "default"
    media_asset_id: int | None = Field(default=None, gt=0)
    post_id: int | None = Field(default=None, gt=0)
    brand_logo_asset_id: int | None = Field(default=None, gt=0)
    headline: str = Field(default="", max_length=255)
    body: str = Field(default="", max_length=5000)
    link_url: str = Field(default="", max_length=2048)
    google_campaign_type: Literal["search", "demand_gen"] = "search"
    google_keywords: list[str] = Field(default_factory=list, max_length=30)
    google_extra_headlines: list[str] = Field(default_factory=list, max_length=14)
    google_extra_description: str = Field(default="", max_length=90)
    google_business_name: str = Field(default="", max_length=25)
    google_long_headline: str = Field(default="", max_length=90)
    youtube_video_id: str = Field(default="", max_length=20)
    linkedin_organization_id: str = Field(default="", max_length=100)
    call_to_action: Literal[
        "LEARN_MORE", "SHOP_NOW", "SIGN_UP", "CONTACT_US", "DOWNLOAD", "GET_OFFER", "GET_QUOTE",
        "SUBSCRIBE", "WATCH_MORE", "ADD_TO_CART", "APPLY_NOW", "BOOK_NOW", "BUY_TICKETS",
        "DONATE", "GET_DIRECTIONS", "LISTEN_NOW", "ORDER_NOW", "PLAY_GAME", "REQUEST_TIME",
        "SEE_MENU", "START_ORDER", "INSTALL_MOBILE_APP", "USE_APP", "BUY_NOW",
    ] = "LEARN_MORE"
    countries: list[str] = Field(default_factory=lambda: ["US"], max_length=25)
    age_min: int = Field(default=18, ge=13, le=65)
    age_max: int = Field(default=65, ge=18, le=65)
    gender: Literal["all", "male", "female"] = "all"
    interests: list[InterestIn] = Field(default_factory=list, max_length=25)
    behaviors: list[InterestIn] = Field(default_factory=list, max_length=25)
    work_positions: list[InterestIn] = Field(default_factory=list, max_length=25)
    work_employers: list[InterestIn] = Field(default_factory=list, max_length=25)
    work_industries: list[InterestIn] = Field(default_factory=list, max_length=25)
    cities: list[GeoTargetIn] = Field(default_factory=list, max_length=20)
    regions: list[GeoTargetIn] = Field(default_factory=list, max_length=20)
    zips: list[GeoTargetIn] = Field(default_factory=list, max_length=20)
    metros: list[GeoTargetIn] = Field(default_factory=list, max_length=20)
    audience_include: list[str] = Field(default_factory=list, max_length=25)
    audience_exclude: list[str] = Field(default_factory=list, max_length=25)
    placement_platforms: list[Literal["facebook", "instagram", "threads", "messenger"]] = Field(
        default_factory=list, max_length=4)
    special_ad_categories: list[Literal[
        "HOUSING", "EMPLOYMENT", "CREDIT", "FINANCIAL_PRODUCTS_SERVICES",
        "ISSUES_ELECTIONS_POLITICS", "ONLINE_GAMBLING_AND_GAMING",
    ]] = Field(default_factory=list, max_length=6)
    pixel_id: str = Field(default="", max_length=100)
    conversion_event: str = Field(default="PURCHASE", max_length=100)
    meta_page_id: str = Field(default="", max_length=100)
    meta_lead_gen_form_id: str = Field(default="", max_length=100)
    meta_application_id: str = Field(default="", max_length=100)
    meta_object_store_url: str = Field(default="", max_length=2048)
    meta_product_catalog_id: str = Field(default="", max_length=100)
    meta_product_set_id: str = Field(default="", max_length=100)
    meta_custom_conversion_id: str = Field(default="", max_length=100)
    meta_custom_event_str: str = Field(default="", max_length=100)
    meta_offline_dataset_id: str = Field(default="", max_length=100)
    url_tags: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_required_creative_copy(self):
        if self.platform == "meta" and self.meta_creative_format == "dynamic":
            return self
        if not self.body.strip():
            raise ValueError("Enter primary text.")
        if self.platform in ("meta", "google", "linkedin") and not self.headline.strip():
            raise ValueError("Enter a campaign headline.")
        if not self.link_url.strip():
            raise ValueError("Enter a landing page URL.")
        return self


class CampaignStatusIn(BaseModel):
    status: Literal["active", "paused"]


class AdsApiRequestIn(BaseModel):
    ad_connection_id: str = Field(min_length=1, max_length=200)
    ad_account_id: str = Field(min_length=1, max_length=200)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(min_length=5, max_length=512)
    query: dict = Field(default_factory=dict)
    body: dict = Field(default_factory=dict)


MetaAdsRequestIn = AdsApiRequestIn


class MetaMessagingCallAdIn(BaseModel):
    ad_connection_id: str = Field(min_length=1, max_length=200)
    ad_account_id: str = Field(min_length=1, max_length=200)
    ad_type: Literal["messaging", "call"]
    destination: Literal["whatsapp", "messenger", "instagram_direct"] = "messenger"
    campaign_name: str = Field(min_length=1, max_length=255)
    headline: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=5000)
    description: str = Field(default="", max_length=255)
    media_asset_id: int = Field(gt=0)
    daily_budget: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    countries: list[str] = Field(min_length=1, max_length=25)
    link_url: str = Field(default="", max_length=2048)
    phone_number: str = Field(default="", max_length=20)
    whatsapp_phone_number: str = Field(default="", max_length=20)
    welcome_text: str = Field(default="", max_length=1000)
    welcome_prefill_text: str = Field(default="", max_length=1000)


class MetaRfBuyIn(BaseModel):
    ad_connection_id: str = Field(min_length=1, max_length=200)
    ad_account_id: str = Field(min_length=1, max_length=200)
    workflow_id: str = Field(min_length=32, max_length=32, pattern=r"^[a-f0-9]{32}$")
    prediction_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    campaign_name: str = Field(min_length=1, max_length=255)
    headline: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=5000)
    link_url: str = Field(min_length=1, max_length=2048)
    call_to_action: Literal["LEARN_MORE", "SHOP_NOW", "SIGN_UP", "CONTACT_US", "GET_OFFER"] = "LEARN_MORE"
    media_asset_id: int = Field(gt=0)
    countries: list[str] = Field(min_length=1, max_length=25)
    age_min: int = Field(ge=18, le=65)
    age_max: int = Field(ge=18, le=65)
    placement: Literal["facebook", "instagram", "both"]


class MetaRfWorkflowCreateIn(BaseModel):
    ad_connection_id: str = Field(min_length=1, max_length=200)
    ad_account_id: str = Field(min_length=1, max_length=200)
    quote_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    config: dict = Field(max_length=30)
    quote: dict = Field(max_length=60)


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
        raise HTTPException(error.status or 502, str(error)) from error


def _connection(workspace_id: int, platform: str, account_id: str) -> dict:
    expected = {
        "meta": "metaads",
        "tiktok": "tiktokads",
        "google": "googleads",
        "linkedin": "linkedinads",
    }[platform]
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
        raise HTTPException(error.status or 502, str(error)) from error
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


def _messaging_call_payload(workspace_id: int, values: dict, account_id: str) -> tuple[str, dict]:
    name = values["campaign_name"].strip()
    headline = values["headline"].strip()
    body = values["body"].strip()
    if not name or not headline or not body:
        raise HTTPException(400, "Enter a campaign name, headline, and primary text.")
    if Decimal(str(values["daily_budget"])) <= 0:
        raise HTTPException(400, "Enter a positive daily budget.")
    countries = [str(country).strip().upper() for country in values["countries"]]
    if not countries or any(len(country) != 2 or not country.isalpha() for country in countries):
        raise HTTPException(400, "Choose target countries using ISO two-letter country codes.")
    if len(set(countries)) != len(countries):
        raise HTTPException(400, "Choose each target country only once.")
    asset = db.one(
        """SELECT content_type FROM ad_campaign_assets
            WHERE id=%s AND workspace_id=%s""",
        (values["media_asset_id"], workspace_id),
    )
    if not asset:
        raise HTTPException(404, "Campaign media was not found in this workspace.")
    content_type = asset["content_type"]
    if content_type not in ("image/jpeg", "image/png", "video/mp4", "video/quicktime"):
        raise HTTPException(400, "Messaging and call ads require an uploaded JPEG, PNG, MP4, or MOV asset.")

    request_body = {
        "accountId": account_id,
        "adAccountId": values["ad_account_id"],
        "name": name,
        "headline": headline,
        "body": body,
        "budgetAmount": float(values["daily_budget"]),
        "budgetType": "daily",
        "status": "PAUSED",
        "campaignStatus": "PAUSED",
        "countries": countries,
    }
    if content_type.startswith("video/"):
        request_body["video"] = {"url": _asset_url(workspace_id, values["media_asset_id"])}
    else:
        request_body["imageUrl"] = _asset_url(workspace_id, values["media_asset_id"])
    description = values["description"].strip()
    if description:
        request_body["description"] = description
    if values["ad_type"] == "messaging":
        request_body["destination"] = values["destination"]
        whatsapp_phone = values["whatsapp_phone_number"].strip()
        if whatsapp_phone and values["destination"] != "whatsapp":
            raise HTTPException(400, "A WhatsApp phone number can only be set for WhatsApp messaging ads.")
        if whatsapp_phone:
            if not re.fullmatch(r"\+[1-9]\d{6,14}", whatsapp_phone):
                raise HTTPException(400, "Enter the WhatsApp number in E.164 format, such as +15551234567.")
            request_body["whatsappPhoneNumber"] = whatsapp_phone
        welcome_text = values["welcome_text"].strip()
        welcome_prefill = values["welcome_prefill_text"].strip()
        if bool(welcome_text) != bool(welcome_prefill):
            raise HTTPException(400, "Enter both welcome message text and prefilled reply, or leave both blank.")
        if welcome_text:
            request_body["welcomeMessage"] = {
                "text": welcome_text,
                "prefillText": welcome_prefill,
            }
        path = "/ads/messaging"
    else:
        if not re.fullmatch(r"\+[1-9]\d{6,14}", values["phone_number"].strip()):
            raise HTTPException(400, "Enter the call ad number in E.164 format, such as +15551234567.")
        link_url = values["link_url"].strip()
        parsed_link = urlparse(link_url)
        if (parsed_link.scheme != "https" or not parsed_link.hostname
                or parsed_link.username or parsed_link.password):
            raise HTTPException(400, "Call ads need a complete HTTPS website URL; do not use a tel: link.")
        request_body["phoneNumber"] = values["phone_number"].strip()
        request_body["linkUrl"] = link_url
        path = "/ads/call"
    return path, request_body


def _image_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as image:
            header = image.read(24)
            if header.startswith(b"\x89PNG\r\n\x1a\n"):
                if len(header) < 24 or struct.unpack(">I", header[8:12])[0] != 13 or header[12:16] != b"IHDR":
                    return None
                width, height = struct.unpack(">II", header[16:24])
                return (width, height) if width and height else None
            if not header.startswith(b"\xff\xd8"):
                return None
            image.seek(2)
            while True:
                prefix = image.read(1)
                while prefix and prefix != b"\xff":
                    prefix = image.read(1)
                while prefix == b"\xff":
                    prefix = image.read(1)
                if not prefix:
                    return None
                marker = prefix[0]
                if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
                    if marker == 0xD9:
                        return None
                    continue
                segment_length = image.read(2)
                if len(segment_length) != 2:
                    return None
                length = struct.unpack(">H", segment_length)[0]
                if length < 2:
                    return None
                if marker in {
                    0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
                }:
                    frame = image.read(5)
                    if len(frame) != 5:
                        return None
                    height, width = struct.unpack(">HH", frame[1:5])
                    return (width, height) if width and height else None
                if marker == 0xDA:
                    return None
                image.seek(length - 2, 1)
    except OSError as error:
        raise HTTPException(400, "Could not read the uploaded logo. Upload it again.") from error


def _post_media_url(workspace_id: int, post_id: int, creative_type: str, platform: str = "meta") -> str:
    allowed_platforms = {
        "meta": ("facebook", "instagram"),
        "tiktok": ("tiktok",),
        "linkedin": ("linkedin",),
    }[platform]
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


def _validate_campaign_media_choice(platform: str, media_asset_id: int | None, post_id: int | None) -> None:
    if platform == "google":
        if media_asset_id is not None or post_id is not None:
            raise HTTPException(
                400, "Google campaigns use campaign-type-specific creative fields, not general media.")
        return
    if (media_asset_id is None) == (post_id is None):
        raise HTTPException(400, "Choose exactly one uploaded asset or existing published post.")


def _campaign_request(workspace_id: int, values: dict) -> tuple[dict, dict]:
    platform = values.get("platform", "meta")
    values = {
        "google_campaign_type": "search",
        "google_keywords": [],
        "google_extra_headlines": [],
        "google_extra_description": "",
        "google_business_name": "",
        "google_long_headline": "",
        "youtube_video_id": "",
        "brand_logo_asset_id": None,
        "meta_creative_format": "single",
        "meta_carousel_cards": [],
        "meta_placement_asset_type": "image",
        "meta_default_media_asset_id": None,
        "meta_placement_rules": [],
        "meta_dynamic_media_type": "image",
        "meta_dynamic_ad_format": "default",
        "meta_dynamic_media_asset_ids": [],
        "meta_dynamic_bodies": [],
        "meta_dynamic_titles": [],
        "meta_dynamic_descriptions": [],
        "meta_dynamic_link_urls": [],
        "meta_dynamic_call_to_action_types": [],
        "meta_enhance_cta": "default",
        "meta_image_brightness_and_contrast": "default",
        "meta_text_optimizations": "default",
        "meta_multi_advertiser": "default",
        "linkedin_organization_id": "",
        "behaviors": [],
        "work_positions": [],
        "work_employers": [],
        "work_industries": [],
        "cities": [],
        "regions": [],
        "zips": [],
        "metros": [],
        "audience_include": [],
        "audience_exclude": [],
        **values,
    }
    connection = _selected_ad_account(
        workspace_id, values["ad_connection_id"], values["ad_account_id"], platform)
    end_date = values["end_date"]
    if isinstance(end_date, str):
        end_date = date.fromisoformat(end_date)
    is_google_search = platform == "google" and values["google_campaign_type"] == "search"
    is_google_demand_gen = platform == "google" and values["google_campaign_type"] == "demand_gen"
    is_meta_dynamic = platform == "meta" and values["meta_creative_format"] == "dynamic"
    needs_media = platform in ("meta", "tiktok", "linkedin")
    if platform != "meta" and (
        values["meta_creative_format"] != "single" or values["meta_carousel_cards"]
        or values["meta_default_media_asset_id"] is not None or values["meta_placement_rules"]
        or values["meta_dynamic_media_asset_ids"] or values["meta_dynamic_bodies"]
        or values["meta_dynamic_titles"] or values["meta_dynamic_descriptions"]
        or values["meta_dynamic_link_urls"] or values["meta_dynamic_call_to_action_types"]
        or values["meta_dynamic_ad_format"] != "default"
        or any(values[field] != "default" for field in (
            "meta_enhance_cta", "meta_image_brightness_and_contrast",
            "meta_text_optimizations", "meta_multi_advertiser",
        ))
    ):
        raise HTTPException(400, "Meta creative-format settings are available only for Meta campaigns.")
    if platform == "meta" and values["meta_creative_format"] != "carousel" and values["meta_carousel_cards"]:
        raise HTTPException(400, "Remove carousel cards or select the Meta carousel format.")
    if platform == "meta" and values["meta_creative_format"] != "placement_assets" and (
        values["meta_default_media_asset_id"] is not None or values["meta_placement_rules"]
    ):
        raise HTTPException(400, "Remove placement assets or select the Meta per-placement format.")
    if platform == "meta" and values["meta_creative_format"] != "dynamic" and (
        any(values[field] for field in (
            "meta_dynamic_media_asset_ids", "meta_dynamic_bodies", "meta_dynamic_titles",
            "meta_dynamic_descriptions", "meta_dynamic_link_urls", "meta_dynamic_call_to_action_types",
        )) or values["meta_dynamic_ad_format"] != "default"
    ):
        raise HTTPException(400, "Remove dynamic creative assets or select the Meta dynamic creative format.")
    is_meta_carousel = platform == "meta" and values["meta_creative_format"] == "carousel"
    is_meta_placement_assets = platform == "meta" and values["meta_creative_format"] == "placement_assets"
    is_meta_dynamic = platform == "meta" and values["meta_creative_format"] == "dynamic"
    if is_meta_carousel and values["creative_type"] != "image":
        raise HTTPException(400, "Meta carousel cards require the image creative type.")
    if is_meta_placement_assets and values["creative_type"] != values["meta_placement_asset_type"]:
        raise HTTPException(400, "Meta placement creative type must match its image or video assets.")
    if is_meta_dynamic and values["creative_type"] != values["meta_dynamic_media_type"]:
        raise HTTPException(400, "Meta dynamic creative type must match its image or video asset pool.")
    if not is_meta_carousel and not is_meta_placement_assets and not is_meta_dynamic:
        _validate_campaign_media_choice(platform, values["media_asset_id"], values["post_id"])
    elif values["media_asset_id"] is not None or values["post_id"] is not None:
        raise HTTPException(400, "Choose the selected Meta creative format instead of single media.")
    if is_google_demand_gen and not values["brand_logo_asset_id"]:
        raise HTTPException(400, "Google Demand Gen requires a square logo image.")
    if values["age_max"] < values["age_min"]:
        raise HTTPException(400, "Maximum age must be equal to or above minimum age.")
    if platform != "meta" and values["age_min"] < 18:
        raise HTTPException(400, "This ad platform requires a minimum target age of 18.")
    if not values["campaign_name"].strip():
        raise HTTPException(400, "Enter a campaign name.")
    if not values["body"].strip() and not is_meta_dynamic:
        raise HTTPException(400, "Enter primary text.")
    if platform in ("meta", "google", "linkedin") and not values["headline"].strip() and not is_meta_dynamic:
        raise HTTPException(400, "Enter a campaign headline.")
    if platform == "tiktok" and Decimal(str(values["daily_budget"])) < Decimal("20"):
        raise HTTPException(400, "TikTok's minimum daily budget is 20 in the ad account's currency.")
    if platform == "linkedin" and Decimal(str(values["daily_budget"])) < Decimal("10"):
        raise HTTPException(400, "LinkedIn's minimum daily budget is 10 in the ad account's currency.")
    if platform == "google" and Decimal(str(values["daily_budget"])) % 1:
        raise HTTPException(400, "Google Ads daily budgets must be whole units of the ad account's currency.")
    countries = [str(code).strip().upper() for code in values["countries"]]
    has_meta_locations = platform == "meta" and any(values.get(field) for field in ("cities", "regions", "zips", "metros"))
    if not countries and not has_meta_locations:
        raise HTTPException(400, "Choose at least one target country.")
    if any(len(code) != 2 or not code.isalpha() for code in countries):
        raise HTTPException(400, "Countries must be ISO two-letter country codes.")
    if len(set(countries)) != len(countries):
        raise HTTPException(400, "Choose each country only once.")
    if not is_meta_dynamic or values["link_url"].strip():
        parsed_url = urlparse(values["link_url"])
        if parsed_url.scheme != "https" or not parsed_url.hostname or parsed_url.username or parsed_url.password:
            raise HTTPException(400, "The landing page URL must be a complete HTTPS URL.")
    if end_date <= date.today():
        raise HTTPException(400, "Choose a campaign end date after today.")
    if values["objective"] == "video_views" and values["creative_type"] != "video":
        raise HTTPException(400, "The video views objective requires a video creative.")
    if (platform in ("meta", "tiktok") and values["objective"] == "conversions" and not values["pixel_id"]
            and not (platform == "meta" and values["meta_custom_conversion_id"])):
        raise HTTPException(400, f"Choose a {platform.title()} pixel for a conversions campaign.")
    if platform == "meta" and values["objective"] not in {
        "awareness", "traffic", "video_views", "conversions", "engagement", "lead_generation",
        "lead_conversion", "app_promotion", "catalog_sales", "page_likes", "page_visits",
    }:
        raise HTTPException(400, "Choose a Meta-supported campaign objective.")
    if platform == "tiktok" and values["objective"] not in (
        "awareness", "traffic", "video_views", "conversions", "engagement",
    ):
        raise HTTPException(400, "TikTok supports Awareness, Traffic, Video views, Conversions, or Engagement.")
    if platform == "google" and values["objective"] not in ("traffic", "awareness", "engagement"):
        raise HTTPException(400, "Google Ads campaigns support Traffic, Awareness, or Engagement objectives.")
    if platform == "linkedin" and values["objective"] not in (
        "traffic", "awareness", "engagement", "video_views",
    ):
        raise HTTPException(400, "LinkedIn campaigns support Traffic, Awareness, Engagement, or Video views.")
    if platform == "google" and values["google_campaign_type"] == "search" and values["objective"] == "video_views":
        raise HTTPException(400, "Google Search does not support a video views objective.")
    if platform == "google" and values["google_campaign_type"] == "demand_gen":
        if not values["google_business_name"].strip():
            raise HTTPException(400, "Enter a business name for the Google Demand Gen video ad.")
        if not values["headline"].strip() or len(values["headline"].strip()) > 40:
            raise HTTPException(400, "Google Demand Gen headlines must be 1–40 characters.")
        if not values["google_long_headline"].strip():
            raise HTTPException(400, "Enter a long headline for the Google Demand Gen video ad.")
        if len(values["body"].strip()) > 90 or len(values["google_long_headline"].strip()) > 90:
            raise HTTPException(400, "Google Demand Gen descriptions and long headlines must be at most 90 characters.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", values["youtube_video_id"].strip()):
            raise HTTPException(400, "Enter an 11-character YouTube video ID for the video ad.")
        if not values["brand_logo_asset_id"]:
            raise HTTPException(400, "Upload a square logo image for the Google Demand Gen video ad.")
        if values["objective"] == "conversions":
            raise HTTPException(400, "Google Ads campaign creation does not support conversion objectives.")
    if platform == "google" and values["google_campaign_type"] == "search":
        keywords = [str(item).strip() for item in values["google_keywords"] if str(item).strip()]
        if not keywords:
            raise HTTPException(400, "Add at least one keyword for the Google Search campaign.")
        if any(len(keyword) > 80 for keyword in keywords):
            raise HTTPException(400, "Google Search keywords must be 80 characters or fewer.")
        if len(values["headline"].strip()) > 30 or any(
            len(str(item).strip()) > 30 for item in values["google_extra_headlines"]
        ):
            raise HTTPException(400, "Google Search headlines must be 30 characters or fewer.")
        if len(values["body"].strip()) > 90 or len(values["google_extra_description"].strip()) > 90:
            raise HTTPException(400, "Google Search descriptions must be 90 characters or fewer.")
        headlines = [values["headline"].strip(), *[
            str(item).strip() for item in values["google_extra_headlines"] if str(item).strip()
        ]]
        if len(set(headlines)) < 3:
            raise HTTPException(400, "Google Search needs at least three different headlines.")
        if len({values["body"].strip(), values["google_extra_description"].strip()} - {""}) < 2:
            raise HTTPException(400, "Google Search needs two different descriptions.")
    if platform == "linkedin":
        if values["creative_type"] not in ("image", "video"):
            raise HTTPException(400, "LinkedIn ads require a single image or video creative.")
        if values["objective"] == "video_views" and values["creative_type"] != "video":
            raise HTTPException(400, "LinkedIn video views campaigns require a video creative.")
        allowed_ctas = {
            "LEARN_MORE", "SIGN_UP", "DOWNLOAD", "SUBSCRIBE", "REGISTER", "JOIN", "ATTEND",
            "REQUEST_DEMO", "VIEW_QUOTE", "APPLY", "SEE_MORE", "SHOP_NOW", "BUY_NOW",
        }
        if values["call_to_action"] not in allowed_ctas:
            raise HTTPException(400, "Choose a LinkedIn-supported call to action.")
        organization_id = values["linkedin_organization_id"].strip()
        if organization_id and not re.fullmatch(r"(?:\d+|urn:li:organization:\d+)", organization_id):
            raise HTTPException(400, "Enter a numeric LinkedIn Company Page ID or urn:li:organization ID.")
    if platform == "meta":
        goal = values["objective"]
        event = values["conversion_event"]
        if is_meta_dynamic:
            if goal == "page_likes":
                raise HTTPException(400, "Meta dynamic creative is not supported with the page likes objective.")
            asset_ids = values["meta_dynamic_media_asset_ids"]
            if not 1 <= len(asset_ids) <= 10:
                raise HTTPException(400, "Meta dynamic creative needs between 1 and 10 uploaded images or videos.")
            if len(set(asset_ids)) != len(asset_ids):
                raise HTTPException(400, "Choose a different uploaded asset for each dynamic creative option.")
            if not values["meta_dynamic_call_to_action_types"]:
                raise HTTPException(400, "Choose at least one call to action for Meta dynamic creative.")
            if any(value not in _META_DYNAMIC_CTA_TYPES
                   for value in values["meta_dynamic_call_to_action_types"]):
                raise HTTPException(400, "Choose supported Meta dynamic creative call-to-action types.")
            if values["meta_dynamic_ad_format"] not in (
                ("default", "SINGLE_IMAGE", "CAROUSEL_IMAGE")
                if values["meta_dynamic_media_type"] == "image" else ("default", "SINGLE_VIDEO")
            ):
                raise HTTPException(400, "The Meta dynamic ad format must match the selected image or video assets.")
            if len(set(values["meta_dynamic_call_to_action_types"])) != len(
                values["meta_dynamic_call_to_action_types"]
            ):
                raise HTTPException(400, "Choose each dynamic creative call to action only once.")
            if any(not text.strip() for field in (
                "meta_dynamic_bodies", "meta_dynamic_titles", "meta_dynamic_descriptions",
            ) for text in values[field]):
                raise HTTPException(400, "Remove blank entries from Meta dynamic creative text variations.")
            if any(len(text.strip()) > 5000 for text in values["meta_dynamic_bodies"]):
                raise HTTPException(400, "Meta dynamic primary text variations must be 5,000 characters or fewer.")
            if any(len(text.strip()) > 255 for field in (
                "meta_dynamic_titles", "meta_dynamic_descriptions",
            ) for text in values[field]):
                raise HTTPException(400, "Meta dynamic headlines and descriptions must be 255 characters or fewer.")
            link_urls = [url.strip() for url in values["meta_dynamic_link_urls"] if url.strip()]
            if goal != "lead_generation" and not link_urls:
                raise HTTPException(400, "Add at least one destination URL for Meta dynamic creative.")
            for link in link_urls:
                parsed_link = urlparse(link)
                if (parsed_link.scheme != "https" or not parsed_link.hostname
                        or parsed_link.username or parsed_link.password):
                    raise HTTPException(400, "Each Meta dynamic creative destination must be a complete HTTPS URL.")
        elif values["meta_creative_format"] == "carousel":
            cards = values["meta_carousel_cards"]
            if not 2 <= len(cards) <= 10:
                raise HTTPException(400, "Meta carousel ads require between 2 and 10 image cards.")
            if goal == "catalog_sales":
                raise HTTPException(400, "Meta carousel cards cannot be used with the catalog sales objective.")
            if goal == "lead_generation" and values["meta_lead_gen_form_id"].strip():
                raise HTTPException(400, "Meta carousel cards cannot be used with an instant lead form.")
            card_asset_ids = [card["media_asset_id"] for card in cards]
            if len(set(card_asset_ids)) != len(card_asset_ids):
                raise HTTPException(400, "Choose a different image asset for each carousel card.")
        elif values["meta_creative_format"] == "placement_assets":
            rules = values["meta_placement_rules"]
            if not values["meta_default_media_asset_id"]:
                raise HTTPException(400, "Choose a default image or video for Meta placements.")
            if not 1 <= len(rules) <= 10:
                raise HTTPException(400, "Meta per-placement creative needs between 1 and 10 placement rules.")
            for rule in rules:
                platforms = set(rule["publisher_platforms"])
                position_fields = {
                    "facebook_positions": ("facebook", rule["facebook_positions"]),
                    "instagram_positions": ("instagram", rule["instagram_positions"]),
                    "messenger_positions": ("messenger", rule["messenger_positions"]),
                    "audience_network_positions": ("audience_network", rule["audience_network_positions"]),
                    "threads_positions": ("threads", rule["threads_positions"]),
                    "whatsapp_positions": ("whatsapp", rule["whatsapp_positions"]),
                }
                if not platforms and not any(items for _field, items in position_fields.values()):
                    raise HTTPException(400, "Each Meta placement rule must choose at least one publisher platform or position.")
                for field, (platform_name, positions) in position_fields.items():
                    if positions and platform_name not in platforms:
                        raise HTTPException(400, f"Add {platform_name} to publisher platforms for its selected positions.")
        elif values["meta_carousel_cards"]:
            raise HTTPException(400, "Remove carousel cards or select the Meta carousel format.")
        if goal in ("conversions", "lead_conversion", "catalog_sales"):
            if goal == "catalog_sales" and values["meta_custom_conversion_id"]:
                raise HTTPException(400, "Catalog sales uses a pixel event, not a custom conversion ID.")
            if goal == "catalog_sales" and not values["pixel_id"]:
                raise HTTPException(400, "Catalog sales requires a Meta pixel.")
            if not values["meta_custom_conversion_id"] and not values["pixel_id"]:
                raise HTTPException(400, "Choose a Meta pixel or custom conversion for this campaign objective.")
            if not values["meta_custom_conversion_id"] or goal == "catalog_sales":
                allowed_events = {
                    "PURCHASE", "LEAD", "COMPLETE_REGISTRATION", "ADD_TO_CART", "INITIATE_CHECKOUT",
                    "ADD_PAYMENT_INFO", "SUBSCRIBE", "START_TRIAL", "VIEW_CONTENT", "SEARCH",
                    "CONTACT", "SUBMIT_APPLICATION", "SCHEDULE", "OTHER",
                }
                if event not in allowed_events:
                    raise HTTPException(400, "Choose a supported Meta conversion event.")
            if (not values["meta_custom_conversion_id"] or goal == "catalog_sales") and (
                event == "OTHER" and not values["meta_custom_event_str"].strip()
            ):
                raise HTTPException(400, "Enter the case-sensitive custom Meta event name for OTHER.")
            if (not values["meta_custom_conversion_id"] or goal == "catalog_sales") and (
                event != "OTHER" and values["meta_custom_event_str"].strip()
            ):
                raise HTTPException(400, "A custom Meta event name is only valid with the OTHER event.")
            if values["meta_custom_conversion_id"] and goal != "catalog_sales" and (
                values["meta_custom_event_str"].strip() or event != "PURCHASE"
            ):
                raise HTTPException(400, "A Meta custom conversion ID cannot be combined with a pixel event.")
            if not values["meta_custom_conversion_id"] or goal == "catalog_sales":
                if goal in ("conversions", "catalog_sales") and event in {
                    "LEAD", "CONTACT", "SUBMIT_APPLICATION", "SCHEDULE",
                }:
                    raise HTTPException(400, "Use Lead conversions for Meta lead events.")
                if goal == "lead_conversion" and event in {
                    "PURCHASE", "ADD_TO_CART", "INITIATE_CHECKOUT", "ADD_PAYMENT_INFO",
                }:
                    raise HTTPException(400, "Choose a lead event for the Meta Lead conversions objective.")
        if goal == "lead_generation" and not values["meta_lead_gen_form_id"].strip():
            raise HTTPException(400, "Choose a Meta instant form for a lead generation campaign.")
        if goal == "app_promotion":
            app_url = urlparse(values["meta_object_store_url"])
            if not values["meta_application_id"].strip() or app_url.scheme != "https" or not app_url.hostname:
                raise HTTPException(400, "App promotion requires an app ID and HTTPS app store URL.")
        if goal == "catalog_sales" and not values["meta_product_set_id"].strip():
            raise HTTPException(400, "Catalog sales requires a Meta product set ID.")
    if platform in ("meta", "tiktok") and values["special_ad_categories"] and values["interests"]:
        raise HTTPException(400, "Remove interest targeting for ads with a special ad category.")
    if platform == "tiktok" and values["creative_type"] != "video":
        raise HTTPException(400, "TikTok campaigns require a video creative.")
    if values["special_ad_categories"] and platform == "meta" and (
        values["age_min"] != 18 or values["age_max"] != 65 or values["gender"] != "all"
    ):
        raise HTTPException(400, "Meta restricts age and gender targeting for special ad categories. Use all ages and genders.")
    if platform != "meta" and any(values.get(field) for field in (
        "behaviors", "work_positions", "work_employers", "work_industries",
        "cities", "regions", "zips", "metros", "audience_include", "audience_exclude",
    )):
        raise HTTPException(400, "Behaviors, Meta locations and custom audiences are available only for Meta campaigns.")
    if platform == "tiktok" and any(category not in ("HOUSING", "EMPLOYMENT", "CREDIT")
                                    for category in values["special_ad_categories"]):
        raise HTTPException(400, "TikTok supports Housing, Employment, and Credit special ad categories.")
    if platform == "tiktok" and values["special_ad_categories"] and values["gender"] != "all":
        raise HTTPException(400, "TikTok restricts gender targeting for special ad categories.")
    if platform == "tiktok" and values["url_tags"]:
        raise HTTPException(400, "TikTok Ads does not support URL tracking tags.")
    if platform in ("meta", "tiktok") and values["pixel_id"] and not values["pixel_id"].isdigit():
        raise HTTPException(400, f"Choose a valid numeric {platform.title()} pixel ID.")
    if platform == "tiktok" and values["placement_platforms"]:
        raise HTTPException(400, "TikTok campaigns use automatic TikTok placements.")
    if values["placement_platforms"] and not set(values["placement_platforms"]) <= {
        "facebook", "instagram", "threads", "messenger",
    }:
        raise HTTPException(400, "Choose supported Meta placement platforms.")

    if is_meta_carousel or is_meta_placement_assets or is_meta_dynamic:
        media_url = ""
    elif needs_media and values["media_asset_id"] is not None:
        asset = db.one(
            """SELECT content_type, file_size FROM ad_campaign_assets
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
        if platform == "linkedin" and not is_video and (
            asset["content_type"] not in ("image/jpeg", "image/png") or asset["file_size"] > 5 * 1024 * 1024
        ):
            raise HTTPException(400, "LinkedIn image ads require JPEG or PNG media no larger than 5 MB.")
        media_url = _asset_url(workspace_id, values["media_asset_id"])
    elif needs_media:
        media_url = _post_media_url(workspace_id, values["post_id"], values["creative_type"], platform)
    else:
        media_url = ""

    request_body = {
        "accountId": connection["id"],
        "adAccountId": values["ad_account_id"],
        "name": values["campaign_name"].strip(),
        "budgetAmount": float(values["daily_budget"]),
        "budgetType": "daily",
        "endDate": f"{end_date.isoformat()}T23:59:59Z",
        "status": "PAUSED",
        **({"countries": countries} if not has_meta_locations else {}),
    }
    if not is_google_demand_gen:
        request_body["goal"] = values["objective"]
    if platform == "meta":
        if not is_meta_dynamic:
            request_body["body"] = values["body"].strip()
            request_body["linkUrl"] = values["link_url"].strip()
            request_body["callToAction"] = values["call_to_action"]
            request_body["headline"] = values["headline"].strip()
        request_body["advantageAudience"] = 0
        creative_features = {
            provider_key: values[field]
            for field, provider_key in (
                ("meta_enhance_cta", "enhance_cta"),
                ("meta_image_brightness_and_contrast", "image_brightness_and_contrast"),
                ("meta_text_optimizations", "text_optimizations"),
            )
            if values[field] != "default"
        }
        if creative_features:
            request_body["creativeFeatures"] = creative_features
        if values["meta_multi_advertiser"] != "default":
            request_body["multiAdvertiser"] = values["meta_multi_advertiser"]
        if not values["special_ad_categories"]:
            request_body["ageMin"] = values["age_min"]
            request_body["ageMax"] = values["age_max"]
        if is_meta_dynamic:
            media_type = values["meta_dynamic_media_type"]
            allowed_content_types = ("image/jpeg", "image/png") if media_type == "image" else (
                "video/mp4", "video/quicktime",
            )
            urls = []
            for asset_id in values["meta_dynamic_media_asset_ids"]:
                asset = db.one(
                    """SELECT content_type FROM ad_campaign_assets
                        WHERE id=%s AND workspace_id=%s""",
                    (asset_id, workspace_id),
                )
                if not asset:
                    raise HTTPException(404, "A Meta dynamic creative asset was not found in this workspace.")
                if asset["content_type"] not in allowed_content_types:
                    raise HTTPException(400, f"Meta dynamic {media_type} creatives require supported uploaded {media_type} assets.")
                urls.append(_asset_url(workspace_id, asset_id))
            dynamic_creative = {
                "imageUrls" if media_type == "image" else "videoUrls": urls,
                "callToActionTypes": values["meta_dynamic_call_to_action_types"],
            }
            if values["meta_dynamic_ad_format"] != "default":
                dynamic_creative["adFormat"] = values["meta_dynamic_ad_format"]
            for field, provider_field in (
                ("meta_dynamic_bodies", "bodies"),
                ("meta_dynamic_titles", "titles"),
                ("meta_dynamic_descriptions", "descriptions"),
                ("meta_dynamic_link_urls", "linkUrls"),
            ):
                entries = [item.strip() for item in values[field] if item.strip()]
                if entries:
                    dynamic_creative[provider_field] = entries
            request_body["dynamicCreative"] = dynamic_creative
        elif values["meta_creative_format"] == "placement_assets":
            asset_type = values["meta_placement_asset_type"]
            allowed_content_types = ("image/jpeg", "image/png") if asset_type == "image" else (
                "video/mp4", "video/quicktime",
            )
            default_asset = db.one(
                """SELECT content_type FROM ad_campaign_assets
                    WHERE id=%s AND workspace_id=%s""",
                (values["meta_default_media_asset_id"], workspace_id),
            )
            if not default_asset:
                raise HTTPException(404, "The default Meta placement asset was not found in this workspace.")
            if default_asset["content_type"] not in allowed_content_types:
                raise HTTPException(400, f"Meta {asset_type} placement creatives require supported uploaded {asset_type} assets.")
            default_url = _asset_url(workspace_id, values["meta_default_media_asset_id"])
            placement_assets = {
                "defaultImageUrl" if asset_type == "image" else "defaultVideoUrl": default_url,
                "rules": [],
            }
            rule_fields = (
                ("publisher_platforms", "publisherPlatforms"),
                ("facebook_positions", "facebookPositions"),
                ("instagram_positions", "instagramPositions"),
                ("messenger_positions", "messengerPositions"),
                ("audience_network_positions", "audienceNetworkPositions"),
                ("threads_positions", "threadsPositions"),
                ("whatsapp_positions", "whatsappPositions"),
            )
            for rule in values["meta_placement_rules"]:
                asset = db.one(
                    """SELECT content_type FROM ad_campaign_assets
                        WHERE id=%s AND workspace_id=%s""",
                    (rule["media_asset_id"], workspace_id),
                )
                if not asset:
                    raise HTTPException(404, "A Meta placement rule asset was not found in this workspace.")
                if asset["content_type"] not in allowed_content_types:
                    raise HTTPException(400, f"All Meta placement assets must be supported {asset_type} files.")
                placements = {
                    provider_field: rule[field]
                    for field, provider_field in rule_fields if rule[field]
                }
                rule_payload = {
                    "videoUrl" if asset_type == "video" else "imageUrl": _asset_url(
                        workspace_id, rule["media_asset_id"]),
                    "placements": placements,
                }
                for field in ("headline", "body", "description"):
                    if rule[field].strip():
                        rule_payload[field] = rule[field].strip()
                placement_assets["rules"].append(rule_payload)
            request_body["placementAssets"] = placement_assets
        elif values["meta_creative_format"] == "carousel":
            carousel_cards = []
            for card in values["meta_carousel_cards"]:
                asset = db.one(
                    """SELECT content_type FROM ad_campaign_assets
                        WHERE id=%s AND workspace_id=%s""",
                    (card["media_asset_id"], workspace_id),
                )
                if not asset:
                    raise HTTPException(404, "A Meta carousel image was not found in this workspace.")
                if asset["content_type"] not in ("image/jpeg", "image/png"):
                    raise HTTPException(400, "Meta carousel cards require uploaded JPEG or PNG images.")
                card_payload = {"imageUrl": _asset_url(workspace_id, card["media_asset_id"])}
                for field, provider_field in (("headline", "headline"), ("description", "description")):
                    if card[field].strip():
                        card_payload[provider_field] = card[field].strip()
                card_link = card["link_url"].strip()
                if card_link:
                    parsed_card_link = urlparse(card_link)
                    if (parsed_card_link.scheme != "https" or not parsed_card_link.hostname
                            or parsed_card_link.username or parsed_card_link.password):
                        raise HTTPException(400, "Each Meta carousel card link must be a complete HTTPS URL.")
                    card_payload["linkUrl"] = card_link
                carousel_cards.append(card_payload)
            request_body["carouselCards"] = carousel_cards
        elif values["creative_type"] == "video":
            request_body["video"] = {"url": media_url}
        else:
            request_body["imageUrl"] = media_url
        if values["gender"] != "all" and not values["special_ad_categories"]:
            request_body["gender"] = values["gender"]
        if values["behaviors"]:
            request_body["behaviors"] = [
                {"id": item["id"], "name": item["name"]} for item in values["behaviors"]
            ]
        for field, provider_field in (
            ("work_positions", "workPositions"),
            ("work_employers", "workEmployers"),
            ("work_industries", "workIndustries"),
        ):
            if values[field]:
                request_body[provider_field] = [
                    {"id": item["id"], "name": item["name"]} for item in values[field]
                ]
        if values["cities"]:
            request_body["cities"] = [
                {key: value for key, value in {
                    "key": item["key"], "radius": item["radius"],
                    "distance_unit": item["distance_unit"],
                }.items() if value is not None}
                for item in values["cities"]
            ]
        if values["regions"]:
            request_body["regions"] = [{"key": item["key"]} for item in values["regions"]]
        if values["zips"]:
            request_body["zips"] = [{"key": item["key"]} for item in values["zips"]]
        if values["metros"]:
            request_body["metros"] = [{"key": item["key"]} for item in values["metros"]]
        if values["audience_include"]:
            request_body["audienceInclude"] = values["audience_include"]
        if values["audience_exclude"]:
            request_body["audienceExclude"] = values["audience_exclude"]
    elif platform == "tiktok":
        request_body["body"] = values["body"].strip()
        request_body["linkUrl"] = values["link_url"].strip()
        request_body["callToAction"] = values["call_to_action"]
        request_body["imageUrl"] = media_url
        request_body["ageMin"] = values["age_min"]
        request_body["ageMax"] = values["age_max"]
        if values["gender"] != "all":
            request_body["gender"] = values["gender"]
    if platform in ("meta", "tiktok") and values["interests"]:
        request_body["interests"] = [
            {"id": interest["id"], "name": interest["name"]} for interest in values["interests"]
        ]
    if platform == "linkedin":
        request_body["body"] = values["body"].strip()
        request_body["linkUrl"] = values["link_url"].strip()
        request_body["callToAction"] = values["call_to_action"]
        request_body["headline"] = values["headline"].strip()
        request_body["ageMin"] = values["age_min"]
        request_body["ageMax"] = values["age_max"]
        if values["gender"] != "all":
            request_body["gender"] = values["gender"]
        request_body["targeting"] = {"countries": countries}
        if values["linkedin_organization_id"].strip():
            request_body["organizationId"] = values["linkedin_organization_id"].strip()
        if values["creative_type"] == "video":
            request_body["video"] = {"url": media_url}
        else:
            request_body["imageUrl"] = media_url
    if is_google_search:
        request_body["campaignType"] = "search"
        request_body["headline"] = values["headline"].strip()
        request_body["body"] = values["body"].strip()
        request_body["linkUrl"] = values["link_url"].strip()
        request_body["keywords"] = [str(item).strip() for item in values["google_keywords"] if str(item).strip()]
        request_body["additionalHeadlines"] = [
            str(item).strip() for item in values["google_extra_headlines"] if str(item).strip()
        ]
        request_body["additionalDescriptions"] = [values["google_extra_description"].strip()]
    if is_google_demand_gen:
        logo = db.one(
            """SELECT file_path, content_type, file_size FROM ad_campaign_assets
                WHERE id=%s AND workspace_id=%s""",
            (values["brand_logo_asset_id"], workspace_id),
        )
        if (not logo or logo["content_type"] not in ("image/jpeg", "image/png")
                or logo["file_size"] > 5 * 1024 * 1024):
            raise HTTPException(400, "Upload a JPEG or PNG logo image for Google Demand Gen.")
        logo_path = media._abs(logo["file_path"])
        if not logo_path:
            raise HTTPException(404, "Campaign logo was not found. Upload it again before creating this campaign.")
        logo_dimensions = _image_dimensions(logo_path)
        if not logo_dimensions or logo_dimensions[0] != logo_dimensions[1]:
            raise HTTPException(400, "Google Demand Gen requires a valid square JPEG or PNG logo image.")
        logo_url = _asset_url(workspace_id, values["brand_logo_asset_id"])
        request_body["campaignType"] = "demand_gen"
        request_body["demandGen"] = {
            "finalUrl": values["link_url"].strip(),
            "businessName": values["google_business_name"].strip(),
            "headlines": [values["headline"].strip()],
            "longHeadlines": [values["google_long_headline"].strip()],
            "descriptions": [values["body"].strip()],
            "youtubeVideoIds": [values["youtube_video_id"].strip()],
            "images": {"logo": [logo_url]},
            "channels": ["youtube_in_stream", "youtube_in_feed", "youtube_shorts"],
        }
    if platform == "meta" and values["placement_platforms"]:
        request_body["placements"] = {"publisherPlatforms": values["placement_platforms"]}
    if platform in ("meta", "tiktok") and values["special_ad_categories"]:
        request_body["specialAdCategories"] = values["special_ad_categories"]
    if platform == "meta" and values["pixel_id"]:
        request_body["tracking"] = {"pixelId": values["pixel_id"]}
    if platform == "meta" and values["url_tags"]:
        request_body.setdefault("tracking", {})["urlTags"] = _url_tags(values["url_tags"])
    if platform == "meta" and values["objective"] in ("conversions", "lead_conversion", "catalog_sales"):
        if values["meta_custom_conversion_id"] and values["objective"] != "catalog_sales":
            promoted_object = {"customConversionId": values["meta_custom_conversion_id"].strip()}
        else:
            promoted_object = {
                "pixelId": values["pixel_id"],
                "customEventType": values["conversion_event"],
            }
            if values["conversion_event"] == "OTHER":
                promoted_object["customEventStr"] = values["meta_custom_event_str"].strip()
        if values["objective"] == "catalog_sales":
            promoted_object["productSetId"] = values["meta_product_set_id"].strip()
            if values["meta_product_catalog_id"].strip():
                promoted_object["productCatalogId"] = values["meta_product_catalog_id"].strip()
        if values["meta_offline_dataset_id"].strip():
            promoted_object["offlineConversionDataSetId"] = values["meta_offline_dataset_id"].strip()
        request_body["promotedObject"] = promoted_object
    elif platform == "meta" and values["objective"] == "lead_generation":
        request_body["leadGenFormId"] = values["meta_lead_gen_form_id"].strip()
        if values["meta_page_id"].strip():
            request_body["promotedObject"] = {"pageId": values["meta_page_id"].strip()}
    elif platform == "meta" and values["objective"] == "app_promotion":
        request_body["promotedObject"] = {
            "applicationId": values["meta_application_id"].strip(),
            "objectStoreUrl": values["meta_object_store_url"].strip(),
        }
    elif platform == "tiktok" and values["objective"] == "conversions":
        allowed_events = {"ON_WEB_ORDER", "INITIATE_ORDER", "ON_WEB_CART", "ON_WEB_REGISTER", "ON_WEB_DETAIL",
                          "FORM", "ON_WEB_SEARCH", "ON_WEB_ADD_TO_WISHLIST", "LANDING_PAGE_VIEW"}
        if values["conversion_event"] not in allowed_events:
            raise HTTPException(400, f"Choose a supported conversion event for {platform.title()} Ads.")
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
    if (not isinstance(ad, dict) or not ad.get("platformCampaignId") or not ad.get("_id")
            or (platform == "linkedin" and not ad.get("platformAdSetId"))):
        db.execute(
            """UPDATE ad_campaign_drafts SET status='unknown', response_json=%s, error=%s,
                      platform_campaign_id=%s, platform_ad_set_id=%s, zernio_ad_id=%s, updated_at=now()
                WHERE id=%s AND workspace_id=%s""",
            (db.Json(result or {}), "Provider created the request but returned incomplete campaign identifiers.",
             str(ad.get("platformCampaignId") or "") if isinstance(ad, dict) else "",
             str(ad.get("platformAdSetId") or "") if isinstance(ad, dict) else "",
             str(ad.get("_id") or "") if isinstance(ad, dict) else "", draft_id, workspace_id),
        )
        raise HTTPException(
            502, f"{platform.title()} may have created the campaign, but the provider response lacked required "
                 "campaign identifiers. "
                 "Check the platform's Ads Manager before retrying.")
    if platform != "meta" or str(ad.get("configuredStatus") or "").upper() != "PAUSED":
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
                502, f"{platform.title()} created the campaign, but HookTrack could not confirm it is paused. "
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
              AND lower(a.platform) IN ('facebook', 'instagram', 'tiktok', 'linkedin')
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
    if values["platform"] != "meta" or values["meta_creative_format"] == "single":
        _validate_campaign_media_choice(values["platform"], values["media_asset_id"], values["post_id"])
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
    if values["platform"] != "meta" or values["meta_creative_format"] == "single":
        _validate_campaign_media_choice(values["platform"], values["media_asset_id"], values["post_id"])
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
            raise HTTPException(
                409, f"The provider's idempotency window may have expired. Check {row['platform'].title()} Ads "
                     "Manager before creating another campaign.")
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
        """SELECT id, status, platform, platform_campaign_id, platform_ad_set_id, zernio_ad_id, pending_status
             FROM ad_campaign_drafts WHERE id=%s AND workspace_id=%s""",
        (draft_id, ctx.workspace_id),
    )
    if not row:
        raise HTTPException(404, "Campaign draft not found.")
    if row["status"] not in ("paused", "active"):
        raise HTTPException(409, "Create the campaign as paused before changing its delivery status.")
    if row["platform"] == "linkedin" and not (
        row["platform_campaign_id"] and row["platform_ad_set_id"] and row["zernio_ad_id"]
    ):
        raise HTTPException(
            409, "LinkedIn campaign identifiers are incomplete. Check LinkedIn Campaign Manager before proceeding.")
    if row["status"] == body.status and not row["pending_status"]:
        return {"id": draft_id, "status": body.status, "unchanged": True}
    db.execute(
        """UPDATE ad_campaign_drafts SET pending_status=%s, error='', updated_at=now()
            WHERE id=%s AND workspace_id=%s""",
        (body.status, draft_id, ctx.workspace_id),
    )
    try:
        if row["platform"] == "linkedin" and body.status == "active":
            latehub.update_ad_campaign_status(row["platform_campaign_id"], "active", "linkedin")
            latehub.update_ad_set_status(row["platform_ad_set_id"], "active", "linkedin")
            result = latehub.update_ad_status(row["zernio_ad_id"], "active", "linkedin")
        else:
            result = latehub.update_ad_campaign_status(
                row["platform_campaign_id"], body.status, row["platform"])
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
        raise HTTPException(502 if unknown else error.status or 502, str(error)) from error
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


@router.post("/meta/request")
def meta_ads_request(body: MetaAdsRequestIn, ctx: Ctx = Depends(admin)):
    """Expose Zernio's documented Meta Ads operations with workspace-scoped credentials."""
    connection = _selected_ad_account(
        ctx.workspace_id, body.ad_connection_id, body.ad_account_id, "meta")
    path = body.path
    valid_ads_path = (
        path.startswith("/ads/")
        and not path.startswith("//")
        and all(part not in ("", ".", "..") for part in path.split("/")[1:])
        and re.fullmatch(r"/ads/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", path) is not None
    )
    tracking_match = re.fullmatch(
        r"/accounts/([A-Za-z0-9_-]+)/tracking-tags(?:/([A-Za-z0-9_-]+)(?:/([A-Za-z0-9_-]+))?)?",
        path,
    )
    valid_tracking_path = bool(tracking_match and tracking_match.group(1) == connection["id"])
    if not valid_ads_path and not valid_tracking_path and path != "/events":
        raise HTTPException(400, "Choose a Zernio Ads endpoint path. Only Ads, tracking-tag, and conversion-event endpoints are allowed.")

    params = dict(body.query)
    request_body = dict(body.body)
    for data in (params, request_body):
        for field, expected in (("accountId", connection["id"]), ("adAccountId", body.ad_account_id)):
            if field in data and str(data[field]) != expected:
                raise HTTPException(400, f"{field} must match the selected workspace Ads account.")
    if path != "/events":
        params["accountId"] = connection["id"]
        params["adAccountId"] = body.ad_account_id
    if path == "/ads/tree":
        profile_id = latehub.ensure_profile(ctx.workspace_id)
        if "profileId" in params and str(params["profileId"]) != profile_id:
            raise HTTPException(400, "profileId must match the selected workspace.")
        params["profileId"] = profile_id
    account_scoped_create_paths = {
        "/ads/create", "/ads/campaigns", "/ads/boost", "/ads/messaging", "/ads/call",
        "/ads/creatives", "/ads/audiences", "/ads/lead-forms", "/ads/rf-predictions",
        "/ads/images", "/ads/videos", "/ads/preview", "/ads/insights/reports",
    }
    if path in account_scoped_create_paths and body.method == "POST":
        request_body.setdefault("accountId", connection["id"])
        request_body.setdefault("adAccountId", body.ad_account_id)
        if path == "/ads/lead-forms":
            request_body.pop("adAccountId", None)
    if valid_tracking_path and body.method == "POST":
        request_body.setdefault("adAccountId", body.ad_account_id)
    if path == "/events":
        profile_id = latehub.ensure_profile(ctx.workspace_id)
        if "profileId" in request_body and str(request_body["profileId"]) != profile_id:
            raise HTTPException(400, "profileId must match the selected workspace.")
        request_body["profileId"] = profile_id
        events = request_body.get("events")
        if not isinstance(events, list) or not events:
            raise HTTPException(400, "Provide at least one conversion event.")
        if len(events) > 1000:
            raise HTTPException(400, "Meta accepts at most 1,000 conversion events per request.")
        destinations = request_body.get("destinations")
        if not destinations and request_body.get("destinationId"):
            destinations = [{"accountId": connection["id"], "destinationId": request_body.pop("destinationId")}]
        if not isinstance(destinations, list) or not destinations:
            raise HTTPException(400, "Choose at least one Meta pixel destination before sending conversion events.")
        try:
            pixels = latehub.ad_tracking_tags(connection["id"], body.ad_account_id)
        except latehub.LateHubError as error:
            raise HTTPException(502, str(error)) from error
        pixel_ids = {str(pixel.get("id") or "") for pixel in pixels}
        for destination in destinations:
            if not isinstance(destination, dict) or str(destination.get("accountId") or "") != connection["id"]:
                raise HTTPException(400, "Each conversion destination must use the selected workspace Meta connection.")
            if str(destination.get("destinationId") or "") not in pixel_ids:
                raise HTTPException(400, "Each conversion destination must be a pixel available to the selected ad account.")
        request_body["destinations"] = destinations
    try:
        return latehub.meta_ads_request(
            body.method, path, params=params,
            body=request_body if body.method != "GET" else None,
        )
    except latehub.LateHubError as error:
        raise HTTPException(error.status if error.status in (400, 401, 402, 403, 404, 409, 422, 429) else 502,
                            str(error)) from error


@router.post("/tiktok/request")
def tiktok_ads_request(body: AdsApiRequestIn, ctx: Ctx = Depends(admin)):
    """Expose documented TikTok Ads operations through the selected workspace account."""
    connection = _selected_ad_account(
        ctx.workspace_id, body.ad_connection_id, body.ad_account_id, "tiktok")
    path = body.path
    valid_ads_path = (
        path.startswith("/ads/")
        and not path.startswith("//")
        and all(part not in ("", ".", "..") for part in path.split("/")[1:])
        and re.fullmatch(r"/ads/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", path) is not None
    )
    tracking_match = re.fullmatch(
        r"/accounts/([A-Za-z0-9_-]+)/tracking-tags"
        r"(?:/([A-Za-z0-9_-]+)(?:/(stats|events(?:/[A-Za-z0-9_-]+)?|shared-accounts|install))?)?",
        path,
    )
    valid_tracking_path = bool(tracking_match and tracking_match.group(1) == connection["id"])
    if not valid_ads_path and not valid_tracking_path and path != "/events":
        raise HTTPException(400, "Choose a documented TikTok Ads, pixel, or conversion-event endpoint.")
    if path == "/ads/insights/reports" or path.startswith("/ads/insights/reports/"):
        raise HTTPException(400, "TikTok async report runs are not supported by Zernio; use live /ads/insights.")
    if path.startswith("/ads/rf-predictions"):
        raise HTTPException(400, "Reach & Frequency predictions are not supported for TikTok Ads.")
    if path == "/events" and body.method != "POST":
        raise HTTPException(400, "TikTok offline conversion events must be sent with POST.")
    if valid_tracking_path and body.method == "DELETE":
        suffix = path.removeprefix(f"/accounts/{connection['id']}/tracking-tags")
        if not suffix or re.fullmatch(r"/[A-Za-z0-9_-]+", suffix) or "/events" in suffix:
            raise HTTPException(400, "TikTok does not support pixel or pixel-event deletion; only shared-account links and installations can be removed.")

    params = dict(body.query)
    request_body = dict(body.body)
    boost_account_ids = {connection["id"]}
    boost_account_id = connection["id"]
    if path == "/ads/boost":
        parent_id = str(connection.get("parent_account_id") or "")
        if parent_id:
            try:
                workspace_tiktok_ids = {
                    account["id"] for account in latehub.accounts(ctx.workspace_id)
                    if account["platform"] == "tiktok"
                }
            except latehub.LateHubError as error:
                raise HTTPException(error.status or 502, str(error)) from error
            if parent_id in workspace_tiktok_ids:
                boost_account_ids.add(parent_id)
                boost_account_id = parent_id
    for data in (params, request_body):
        if "accountId" in data and str(data["accountId"]) not in boost_account_ids:
            raise HTTPException(400, "accountId must match the selected TikTok Ads connection or its linked workspace TikTok account.")
        if "adAccountId" in data and str(data["adAccountId"]) != body.ad_account_id:
            raise HTTPException(400, "adAccountId must match the selected workspace TikTok Ads advertiser.")
    if path != "/events":
        params["accountId"] = connection["id"]
        if not path.endswith("/install"):
            params["adAccountId"] = body.ad_account_id
    if path in {
        "/ads/create", "/ads/audiences", "/ads/images", "/ads/videos",
    } and body.method == "POST":
        request_body.setdefault("accountId", connection["id"])
        request_body.setdefault("adAccountId", body.ad_account_id)
    if path == "/ads/boost" and body.method == "POST":
        request_body.setdefault("accountId", boost_account_id)
        request_body.setdefault("adAccountId", body.ad_account_id)
        params["accountId"] = request_body["accountId"]
    if valid_tracking_path and body.method == "POST" and not path.endswith("/install"):
        request_body.setdefault("adAccountId", body.ad_account_id)
    if valid_tracking_path and path.endswith("/install"):
        store_account_id = (
            request_body.get("storeAccountId") if body.method == "POST"
            else params.get("storeAccountId")
        )
        if not store_account_id:
            raise HTTPException(400, "Provide the connected Shopify or WordPress storeAccountId.")
        try:
            store_account = next(
                (account for account in latehub.accounts(ctx.workspace_id)
                 if account["id"] == str(store_account_id)),
                None,
            )
        except latehub.LateHubError as error:
            raise HTTPException(error.status or 502, str(error)) from error
        if not store_account or store_account["platform"] not in ("shopify", "wordpress"):
            raise HTTPException(400, "The store must be a Shopify or WordPress account connected to this workspace.")
    if valid_tracking_path and path.endswith("/shared-accounts") and body.method == "DELETE":
        share_account_id = params.get("adAccountId")
        if not share_account_id:
            raise HTTPException(400, "Provide the shared advertiser adAccountId to unlink.")
        if str(share_account_id) != body.ad_account_id:
            raise HTTPException(400, "The shared advertiser adAccountId must match the selected TikTok advertiser.")
    if path == "/events":
        profile_id = latehub.ensure_profile(ctx.workspace_id)
        if "profileId" in request_body and str(request_body["profileId"]) != profile_id:
            raise HTTPException(400, "profileId must match the selected workspace.")
        request_body["profileId"] = profile_id
        events = request_body.get("events")
        destinations = request_body.get("destinations")
        if not isinstance(events, list) or not events:
            raise HTTPException(400, "Provide at least one offline conversion event.")
        if len(events) > 1000:
            raise HTTPException(400, "TikTok accepts at most 1,000 conversion events per request.")
        if not isinstance(destinations, list) or not destinations:
            raise HTTPException(400, "Choose at least one TikTok offline event-set destination.")
        for destination in destinations:
            if not isinstance(destination, dict) or str(destination.get("accountId") or "") != connection["id"]:
                raise HTTPException(400, "Each conversion destination must use the selected workspace TikTok connection.")
        request_body["destinations"] = destinations
    try:
        return latehub.tiktok_ads_request(
            body.method, path, params=params,
            body=request_body if body.method != "GET" else None,
        )
    except latehub.LateHubError as error:
        raise HTTPException(error.status if error.status in (400, 401, 402, 403, 404, 409, 422, 429) else 502,
                            str(error)) from error


@router.post("/meta/messaging-call")
def create_meta_messaging_call_ad(body: MetaMessagingCallAdIn, ctx: Ctx = Depends(admin)):
    connection = _selected_ad_account(
        ctx.workspace_id, body.ad_connection_id, body.ad_account_id, "meta")
    path, request_body = _messaging_call_payload(
        ctx.workspace_id, body.model_dump(mode="json"), connection["id"])
    try:
        return latehub.meta_ads_request("POST", path, body=request_body)
    except latehub.LateHubError as error:
        raise HTTPException(error.status if error.status in (400, 401, 402, 403, 404, 409, 422, 429) else 502,
                            str(error)) from error


@router.post("/meta/reach-frequency/buy")
def buy_meta_reach_frequency_campaign(body: MetaRfBuyIn, ctx: Ctx = Depends(admin)):
    connection = _selected_ad_account(
        ctx.workspace_id, body.ad_connection_id, body.ad_account_id, "meta")
    workflow = db.one(
        """SELECT workflow_id, ad_connection_id, ad_account_id, status, reserved_id, config_json
             FROM meta_reach_frequency_workflows
            WHERE workflow_id=%s AND workspace_id=%s""",
        (body.workflow_id, ctx.workspace_id),
    )
    if not workflow or workflow["ad_connection_id"] != body.ad_connection_id \
            or workflow["ad_account_id"] != body.ad_account_id:
        raise HTTPException(404, "Saved Reach & Frequency reservation was not found for this account.")
    if workflow["status"] != "reserved" or workflow["reserved_id"] != body.prediction_id:
        raise HTTPException(409, "This Reach & Frequency reservation is not available for a new campaign.")
    saved_targeting = workflow["config_json"].get("targeting") or {}
    saved_placements = workflow["config_json"].get("placements") or {}
    countries = [country.strip().upper() for country in body.countries]
    saved_countries = [str(country).strip().upper() for country in saved_targeting.get("countries", [])]
    if any(not re.fullmatch(r"[A-Z]{2}", country) for country in countries):
        raise HTTPException(400, "Choose target countries using ISO two-letter country codes.")
    if len(set(countries)) != len(countries):
        raise HTTPException(400, "Choose each target country only once.")
    if body.age_max < body.age_min:
        raise HTTPException(400, "Maximum age must be equal to or above minimum age.")
    requested_placements = {
        "publisherPlatforms": ["facebook", "instagram"] if body.placement == "both" else [body.placement],
        "devicePlatforms": ["mobile", "desktop"],
    }
    if body.placement in ("facebook", "both"):
        requested_placements["facebookPositions"] = ["feed"]
    if body.placement in ("instagram", "both"):
        requested_placements["instagramPositions"] = ["stream"]
    if (countries != saved_countries or body.age_min != saved_targeting.get("ageMin")
            or body.age_max != saved_targeting.get("ageMax") or requested_placements != saved_placements):
        raise HTTPException(400, "Campaign targeting and placements must match the saved Reach & Frequency quote.")
    if not body.campaign_name.strip() or not body.headline.strip() or not body.body.strip():
        raise HTTPException(400, "Enter a campaign name, headline, and primary text.")
    parsed_link = urlparse(body.link_url.strip())
    if (parsed_link.scheme != "https" or not parsed_link.hostname
            or parsed_link.username or parsed_link.password):
        raise HTTPException(400, "Enter a complete HTTPS landing page URL.")
    asset = db.one(
        """SELECT content_type FROM ad_campaign_assets
            WHERE id=%s AND workspace_id=%s""",
        (body.media_asset_id, ctx.workspace_id),
    )
    if not asset:
        raise HTTPException(404, "Campaign media was not found in this workspace.")
    content_type = asset["content_type"]
    if content_type not in ("image/jpeg", "image/png", "video/mp4", "video/quicktime"):
        raise HTTPException(400, "Reach & Frequency ads require an uploaded JPEG, PNG, MP4, or MOV asset.")
    placements = requested_placements
    request_body = {
        "accountId": connection["id"],
        "adAccountId": body.ad_account_id,
        "name": body.campaign_name.strip(),
        "goal": "awareness",
        "buyingType": "RESERVED",
        "rfPredictionId": body.prediction_id,
        "headline": body.headline.strip(),
        "body": body.body.strip(),
        "callToAction": body.call_to_action,
        "linkUrl": body.link_url.strip(),
        "countries": countries,
        "ageMin": body.age_min,
        "ageMax": body.age_max,
        "placements": placements,
    }
    media_url = _asset_url(ctx.workspace_id, body.media_asset_id)
    if content_type.startswith("video/"):
        request_body["video"] = {"url": media_url}
    else:
        request_body["imageUrl"] = media_url
    if not db.execute(
        """UPDATE meta_reach_frequency_workflows
              SET status='buy_pending', buy_json=%s, error='', updated_at=now()
            WHERE workflow_id=%s AND workspace_id=%s AND status='reserved' AND reserved_id=%s""",
        (db.Json(request_body), body.workflow_id, ctx.workspace_id, body.prediction_id),
    ):
        raise HTTPException(409, "This reservation is already being used or is no longer available.")
    try:
        response = latehub.meta_ads_request("POST", "/ads/create", body=request_body)
    except latehub.LateHubError as error:
        uncertain = error.status is None or error.status in (408, 409, 429) or error.status >= 500
        db.execute(
            """UPDATE meta_reach_frequency_workflows
                  SET status=%s, error=%s, updated_at=now()
                WHERE workflow_id=%s AND workspace_id=%s AND status='buy_pending'""",
            ("unknown" if uncertain else "reserved", str(error), body.workflow_id, ctx.workspace_id),
        )
        raise HTTPException(error.status if error.status in (400, 401, 402, 403, 404, 409, 422, 429) else 502,
                            str(error)) from error
    db.execute(
        """UPDATE meta_reach_frequency_workflows
              SET status='bought', buy_json=%s, error='', updated_at=now()
            WHERE workflow_id=%s AND workspace_id=%s AND status='buy_pending'""",
        (db.Json(response), body.workflow_id, ctx.workspace_id),
    )
    return response


@router.get("/meta/reach-frequency/workflows")
def list_meta_reach_frequency_workflows(
    ad_connection_id: str, ad_account_id: str, ctx: Ctx = Depends(admin),
):
    _selected_ad_account(ctx.workspace_id, ad_connection_id, ad_account_id, "meta")
    workflows = db.query(
        """SELECT workflow_id, ad_connection_id, ad_account_id, status, quote_id, reserved_id,
                  config_json, quote_json, reservation_json, buy_json, error, created_at, updated_at
             FROM meta_reach_frequency_workflows
            WHERE workspace_id=%s AND ad_connection_id=%s AND ad_account_id=%s
            ORDER BY updated_at DESC LIMIT 25""",
        (ctx.workspace_id, ad_connection_id, ad_account_id),
    )
    return {"workflows": workflows}


@router.post("/meta/reach-frequency/workflows")
def create_meta_reach_frequency_workflow(body: MetaRfWorkflowCreateIn, ctx: Ctx = Depends(admin)):
    _selected_ad_account(ctx.workspace_id, body.ad_connection_id, body.ad_account_id, "meta")
    quote_id = str(body.quote.get("predictionId") or body.quote_id)
    if quote_id != body.quote_id:
        raise HTTPException(400, "The quote response ID must match the saved prediction ID.")
    if body.quote.get("status") not in ("ready", "pending"):
        raise HTTPException(400, "Only a ready or pending Meta quote can be saved.")
    workflow_id = secrets.token_hex(16)
    row = db.one(
        """INSERT INTO meta_reach_frequency_workflows
               (workflow_id, workspace_id, ad_connection_id, ad_account_id, status,
                quote_id, config_json, quote_json)
           VALUES (%s,%s,%s,%s,'quote_ready',%s,%s,%s)
           RETURNING workflow_id, status, quote_id, reserved_id, config_json, quote_json,
                     reservation_json, buy_json, error, created_at, updated_at""",
        (workflow_id, ctx.workspace_id, body.ad_connection_id, body.ad_account_id,
         quote_id, db.Json(body.config), db.Json(body.quote)),
    )
    return row


@router.post("/meta/reach-frequency/workflows/{workflow_id}/refresh")
def refresh_meta_reach_frequency_workflow(workflow_id: str, ctx: Ctx = Depends(admin)):
    if not re.fullmatch(r"[a-f0-9]{32}", workflow_id):
        raise HTTPException(404, "Reach & Frequency workflow not found.")
    row = db.one(
        """SELECT * FROM meta_reach_frequency_workflows
            WHERE workflow_id=%s AND workspace_id=%s""",
        (workflow_id, ctx.workspace_id),
    )
    if not row:
        raise HTTPException(404, "Reach & Frequency workflow not found.")
    connection = _selected_ad_account(ctx.workspace_id, row["ad_connection_id"], row["ad_account_id"], "meta")
    if row["status"] not in ("quote_ready", "reserved"):
        raise HTTPException(409, "This workflow is mid-operation or terminal; inspect the saved state before retrying.")
    prediction_id = row["reserved_id"] if row["status"] == "reserved" else row["quote_id"]
    try:
        response = latehub.meta_ads_request(
            "GET", f"/ads/rf-predictions/{prediction_id}",
            params={"accountId": connection["id"], "adAccountId": row["ad_account_id"]},
        )
    except latehub.LateHubError as error:
        raise HTTPException(error.status if error.status in (400, 401, 402, 403, 404, 409, 422, 429) else 502,
                            str(error)) from error
    prediction = response.get("prediction") if isinstance(response, dict) else None
    if not isinstance(prediction, dict) or str(prediction.get("predictionId") or "") != prediction_id:
        raise HTTPException(502, "Zernio returned an invalid Reach & Frequency prediction.")
    if row["status"] == "reserved":
        if prediction.get("status") != "ready":
            raise HTTPException(409, "The saved reservation is no longer ready; inspect its provider status before continuing.")
        db.execute(
            """UPDATE meta_reach_frequency_workflows
                  SET reservation_json=%s, updated_at=now()
                WHERE workflow_id=%s AND workspace_id=%s""",
            (db.Json(prediction), workflow_id, ctx.workspace_id),
        )
    else:
        db.execute(
            """UPDATE meta_reach_frequency_workflows
                  SET quote_json=%s, updated_at=now()
                WHERE workflow_id=%s AND workspace_id=%s""",
            (db.Json(prediction), workflow_id, ctx.workspace_id),
        )
    return {"workflow_id": workflow_id, "status": row["status"], "prediction": prediction}


@router.post("/meta/reach-frequency/workflows/{workflow_id}/reserve")
def reserve_meta_reach_frequency_workflow(workflow_id: str, ctx: Ctx = Depends(admin)):
    if not re.fullmatch(r"[a-f0-9]{32}", workflow_id):
        raise HTTPException(404, "Reach & Frequency workflow not found.")
    row = db.one(
        """SELECT * FROM meta_reach_frequency_workflows
            WHERE workflow_id=%s AND workspace_id=%s""",
        (workflow_id, ctx.workspace_id),
    )
    if not row:
        raise HTTPException(404, "Reach & Frequency workflow not found.")
    connection = _selected_ad_account(ctx.workspace_id, row["ad_connection_id"], row["ad_account_id"], "meta")
    if row["status"] != "quote_ready" or row["quote_json"].get("status") != "ready":
        raise HTTPException(409, "Only a saved ready quote can be reserved.")
    if not db.execute(
        """UPDATE meta_reach_frequency_workflows
              SET status='reserve_pending', error='', updated_at=now()
            WHERE workflow_id=%s AND workspace_id=%s AND status='quote_ready'""",
        (workflow_id, ctx.workspace_id),
    ):
        raise HTTPException(409, "This quote is already being reserved or is no longer available.")
    try:
        response = latehub.meta_ads_request(
            "POST", f"/ads/rf-predictions/{row['quote_id']}/reserve",
            body={"accountId": connection["id"], "adAccountId": row["ad_account_id"]},
        )
        prediction = response.get("prediction") if isinstance(response, dict) else None
        reservation_id = str((prediction or {}).get("predictionId") or "")
        if not isinstance(prediction, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", reservation_id):
            db.execute(
                """UPDATE meta_reach_frequency_workflows
                      SET status='unknown', error=%s, updated_at=now()
                    WHERE workflow_id=%s AND workspace_id=%s AND status='reserve_pending'""",
                ("Zernio returned an invalid reservation response; check the provider before retrying.",
                 workflow_id, ctx.workspace_id),
            )
            raise HTTPException(502, "Zernio returned an invalid Reach & Frequency reservation response.")
        if prediction.get("status") != "ready":
            db.execute(
                """UPDATE meta_reach_frequency_workflows
                      SET status='unknown', reserved_id=%s, reservation_json=%s, error=%s, updated_at=now()
                    WHERE workflow_id=%s AND workspace_id=%s AND status='reserve_pending'""",
                (reservation_id, db.Json(prediction),
                 "Provider returned a reservation that is not ready; inspect before continuing.",
                 workflow_id, ctx.workspace_id),
            )
            raise HTTPException(409, "The provider returned a non-ready reservation; inspect it before continuing.")
        db.execute(
            """UPDATE meta_reach_frequency_workflows
                  SET status='reserved', reserved_id=%s, reservation_json=%s, error='', updated_at=now()
                WHERE workflow_id=%s AND workspace_id=%s AND status='reserve_pending'""",
            (reservation_id, db.Json(prediction), workflow_id, ctx.workspace_id),
        )
        return {"workflow_id": workflow_id, "status": "reserved", "prediction": prediction}
    except latehub.LateHubError as error:
        uncertain = error.status is None or error.status in (408, 429) or error.status >= 500
        db.execute(
            """UPDATE meta_reach_frequency_workflows
                  SET status=%s, error=%s, updated_at=now()
                WHERE workflow_id=%s AND workspace_id=%s AND status='reserve_pending'""",
            ("unknown" if uncertain else "quote_ready", str(error), workflow_id, ctx.workspace_id),
        )
        raise HTTPException(error.status if error.status in (400, 401, 402, 403, 404, 409, 422, 429) else 502,
                            str(error)) from error


@router.post("/meta/reach-frequency/workflows/{workflow_id}/release")
def release_meta_reach_frequency_workflow(workflow_id: str, ctx: Ctx = Depends(admin)):
    if not re.fullmatch(r"[a-f0-9]{32}", workflow_id):
        raise HTTPException(404, "Reach & Frequency workflow not found.")
    row = db.one(
        """SELECT * FROM meta_reach_frequency_workflows
            WHERE workflow_id=%s AND workspace_id=%s""",
        (workflow_id, ctx.workspace_id),
    )
    if not row:
        raise HTTPException(404, "Reach & Frequency workflow not found.")
    connection = _selected_ad_account(ctx.workspace_id, row["ad_connection_id"], row["ad_account_id"], "meta")
    if row["status"] != "reserved" or not row["reserved_id"]:
        raise HTTPException(409, "Only an unused saved reservation can be released.")
    if not db.execute(
        """UPDATE meta_reach_frequency_workflows
              SET status='release_pending', error='', updated_at=now()
            WHERE workflow_id=%s AND workspace_id=%s AND status='reserved'""",
        (workflow_id, ctx.workspace_id),
    ):
        raise HTTPException(409, "This reservation is already being changed.")
    try:
        response = latehub.meta_ads_request(
            "DELETE", f"/ads/rf-predictions/{row['reserved_id']}",
            params={"accountId": connection["id"], "adAccountId": row["ad_account_id"]},
        )
    except latehub.LateHubError as error:
        uncertain = error.status is None or error.status in (408, 429) or error.status >= 500
        db.execute(
            """UPDATE meta_reach_frequency_workflows
                  SET status=%s, error=%s, updated_at=now()
                WHERE workflow_id=%s AND workspace_id=%s AND status='release_pending'""",
            ("unknown" if uncertain else "reserved", str(error), workflow_id, ctx.workspace_id),
        )
        raise HTTPException(error.status if error.status in (400, 401, 402, 403, 404, 409, 422, 429) else 502,
                            str(error)) from error
    db.execute(
        """UPDATE meta_reach_frequency_workflows
              SET status='released', error='', updated_at=now()
            WHERE workflow_id=%s AND workspace_id=%s AND status='release_pending'""",
        (workflow_id, ctx.workspace_id),
    )
    return {"workflow_id": workflow_id, "status": "released", "response": response}


@router.get("/campaigns")
def campaign_reporting(ctx: Ctx = Depends(current)):
    connections = _connections(ctx.workspace_id)
    accounts_by_platform = {"meta": [], "tiktok": [], "google": [], "linkedin": []}
    for connection in connections:
        platform = {
            "metaads": "meta", "tiktokads": "tiktok",
            "googleads": "google", "linkedinads": "linkedin",
        }.get(connection["platform"])
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
        if platform != "meta":
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
            ctx.workspace_id, body.platform, f"{base_url}/#/ads", body.posting_account_id,
            login_mode=body.login_mode,
        )
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
