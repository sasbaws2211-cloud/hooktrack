import os
import struct
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import ads, latehub
from app.auth import Ctx


def _ctx(role="admin"):
    return Ctx(user_id=4, email="ads@example.com", name="Ads Admin", workspace_id=7, role=role)


def test_ad_account_listing_is_scoped_to_zernio_connection(monkeypatch):
    calls = []

    def call(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"accounts": [
            {"id": "act_1", "name": "Brand account", "currency": "USD"},
            {"adAccountId": "act_2", "name": "Not selected", "selected": False},
        ]}

    monkeypatch.setattr(latehub, "_call", call)

    assert latehub.ad_accounts("zernio-ads-connection") == [
        {"id": "act_1", "name": "Brand account", "currency": "USD", "selected": True},
        {"id": "act_2", "name": "Not selected", "currency": "", "selected": False},
    ]
    assert calls == [("GET", "/ads/accounts", {
        "params": {"accountId": "zernio-ads-connection", "inScopeOnly": "true"},
    })]


def test_meta_ads_tool_binds_requests_to_selected_connection_and_account(monkeypatch):
    seen = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *args: seen.append(args) or {"id": "meta-connection"})
    monkeypatch.setattr(
        latehub, "meta_ads_request",
        lambda method, path, **kwargs: seen.append((method, path, kwargs)) or {"ok": True},
    )
    request = ads.MetaAdsRequestIn(
        ad_connection_id="meta-connection", ad_account_id="act-1",
        method="POST", path="/ads/audiences", body={"type": "customer_list", "name": "Customers"},
    )

    assert ads.meta_ads_request(request, _ctx()) == {"ok": True}
    assert seen[0] == (7, "meta-connection", "act-1", "meta")
    assert seen[1] == ("POST", "/ads/audiences", {
        "params": {"accountId": "meta-connection", "adAccountId": "act-1"},
        "body": {
            "type": "customer_list", "name": "Customers",
            "accountId": "meta-connection", "adAccountId": "act-1",
        },
    })


def test_meta_ads_tool_rejects_mismatched_account_and_unsafe_paths(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    request = ads.MetaAdsRequestIn(
        ad_connection_id="meta-connection", ad_account_id="act-1",
        method="GET", path="/ads/tree", query={"accountId": "another-connection"},
    )
    with pytest.raises(HTTPException, match="accountId must match"):
        ads.meta_ads_request(request, _ctx())

    for path in ("/ads/../accounts", "/ads//audiences", "/accounts/another-connection/tracking-tags"):
        request = ads.MetaAdsRequestIn(
            ad_connection_id="meta-connection", ad_account_id="act-1",
            method="GET", path=path,
        )
        with pytest.raises(HTTPException, match="endpoint path"):
            ads.meta_ads_request(request, _ctx())


def test_meta_events_tool_does_not_inject_ad_account_field(monkeypatch):
    calls = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(ads.latehub, "ad_tracking_tags", lambda *_args: [{"id": "pixel-1"}])
    monkeypatch.setattr(
        latehub, "meta_ads_request",
        lambda method, path, **kwargs: calls.append((method, path, kwargs)) or {"events_received": 1},
    )
    request = ads.MetaAdsRequestIn(
        ad_connection_id="meta-connection", ad_account_id="act-1",
        method="POST", path="/events", body={"destinationId": "pixel-1", "events": []},
    )

    assert ads.meta_ads_request(request, _ctx()) == {"events_received": 1}
    assert calls == [("POST", "/events", {
        "params": {"accountId": "meta-connection"},
        "body": {"destinationId": "pixel-1", "events": []},
    })]


def test_meta_events_tool_rejects_pixel_outside_selected_account(monkeypatch):
    calls = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(ads.latehub, "ad_tracking_tags", lambda *_args: [{"id": "another-pixel"}])
    monkeypatch.setattr(latehub, "meta_ads_request", lambda *_args, **_kwargs: calls.append("called"))
    request = ads.MetaAdsRequestIn(
        ad_connection_id="meta-connection", ad_account_id="act-1",
        method="POST", path="/events", body={"destinationId": "pixel-1", "events": []},
    )

    with pytest.raises(HTTPException, match="pixel available to the selected ad account"):
        ads.meta_ads_request(request, _ctx())
    assert calls == []


def test_workspace_account_lists_separate_publishing_and_ads_accounts(monkeypatch):
    monkeypatch.setattr(latehub, "ensure_profile", lambda _workspace_id: "own-profile")
    monkeypatch.setattr(latehub.db, "query", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(latehub, "_call", lambda *_args, **_kwargs: {"accounts": [
        {"_id": "fb-own", "platform": "facebook", "profileId": "own-profile", "isActive": True},
        {"_id": "meta-own", "platform": "metaads", "profileId": "own-profile", "isActive": True},
        {"_id": "google-own", "platform": "googleads", "profileId": "own-profile", "isActive": True},
        {"_id": "linkedin-own", "platform": "linkedinads", "profileId": "own-profile", "isActive": True},
        {"_id": "tt-other", "platform": "tiktokads", "profileId": "another-profile", "isActive": True},
    ]})

    assert [a["id"] for a in latehub.accounts(7)] == ["fb-own"]
    assert [a["id"] for a in latehub.ads_connections(7)] == ["meta-own", "google-own", "linkedin-own"]


def test_ads_connection_is_workspace_scoped_and_links_selected_posting_account(monkeypatch):
    monkeypatch.setattr(latehub, "ensure_profile", lambda workspace_id: f"profile-{workspace_id}")
    monkeypatch.setattr(latehub, "accounts", lambda _workspace_id: [
        {"id": "tt-posting", "platform": "tiktok", "username": "@creator"},
    ])
    calls = []
    monkeypatch.setattr(latehub, "_call", lambda method, path, **kwargs:
                        calls.append((method, path, kwargs)) or {"authUrl": "https://zernio.com/oauth"})

    result = latehub.connect_ads_url(7, "tiktok", "https://hooktrack.example/#/ads", "tt-posting")

    assert result == {"auth_url": "https://zernio.com/oauth", "already_connected": False}
    assert calls == [("GET", "/connect/tiktok/ads", {"params": {
        "profileId": "profile-7",
        "accountId": "tt-posting",
        "redirect_url": "https://hooktrack.example/#/ads",
    }})]


def test_meta_ads_uses_the_selected_facebook_or_instagram_connection_path(monkeypatch):
    monkeypatch.setattr(latehub, "ensure_profile", lambda _workspace_id: "profile-7")
    monkeypatch.setattr(latehub, "accounts", lambda _workspace_id: [
        {"id": "ig-posting", "platform": "instagram", "username": "@brand"},
    ])
    calls = []
    monkeypatch.setattr(latehub, "_call", lambda method, path, **kwargs:
                        calls.append((method, path, kwargs)) or {"authUrl": "https://zernio.com/oauth"})

    latehub.connect_ads_url(7, "meta", "https://hooktrack.example/#/ads", "ig-posting")

    assert calls[0][1] == "/connect/instagram/ads"
    assert calls[0][2]["params"]["accountId"] == "ig-posting"


def test_meta_business_login_connects_without_a_publishing_account(monkeypatch):
    monkeypatch.setattr(latehub, "ensure_profile", lambda _workspace_id: "profile-7")
    monkeypatch.setattr(latehub, "accounts", lambda _workspace_id: pytest.fail("Business Login is standalone"))
    calls = []
    monkeypatch.setattr(latehub, "_call", lambda method, path, **kwargs:
                        calls.append((method, path, kwargs)) or {"authUrl": "https://facebook.com/oauth"})

    result = latehub.connect_ads_url(
        7, "meta", "https://hooktrack.example/#/ads", "", login_mode="business",
    )

    assert result == {"auth_url": "https://facebook.com/oauth", "already_connected": False}
    assert calls == [("GET", "/connect/facebook/ads", {"params": {
        "profileId": "profile-7",
        "loginMode": "business",
        "redirect_url": "https://hooktrack.example/#/ads",
    }})]


def test_google_ads_connection_starts_standalone_oauth(monkeypatch):
    monkeypatch.setattr(latehub, "ensure_profile", lambda _workspace_id: "profile-7")
    calls = []
    monkeypatch.setattr(latehub, "_call", lambda method, path, **kwargs:
                        calls.append((method, path, kwargs)) or {"authUrl": "https://accounts.google.com/oauth"})

    result = latehub.connect_ads_url(7, "google", "https://hooktrack.example/#/ads", "")

    assert result == {"auth_url": "https://accounts.google.com/oauth", "already_connected": False}
    assert calls == [("GET", "/connect/googleads/ads", {"params": {
        "profileId": "profile-7",
        "redirect_url": "https://hooktrack.example/#/ads",
    }})]


def test_linkedin_ads_connection_uses_the_linkedin_publishing_identity(monkeypatch):
    monkeypatch.setattr(latehub, "ensure_profile", lambda _workspace_id: "profile-7")
    monkeypatch.setattr(latehub, "accounts", lambda _workspace_id: [
        {"id": "linkedin-posting", "platform": "linkedin"},
    ])
    calls = []
    monkeypatch.setattr(latehub, "_call", lambda method, path, **kwargs:
                        calls.append((method, path, kwargs)) or {"alreadyConnected": True})

    result = latehub.connect_ads_url(7, "linkedin", "https://hooktrack.example/#/ads", "linkedin-posting")

    assert result == {"auth_url": "", "already_connected": True}
    assert calls[0][1] == "/connect/linkedin/ads"
    assert calls[0][2]["params"]["accountId"] == "linkedin-posting"


def test_meta_boost_uses_verified_connection_and_selected_ad_account(monkeypatch):
    post = {
        "id": 18, "status": "posted", "external_id": "meta-post-22", "content_type": "short_video",
        "hook": "A good hook", "caption": "Caption", "views": 1200,
        "handle": "@brand", "account_platform": "Instagram",
    }
    monkeypatch.setattr(ads, "_post_for_boost", lambda *_args: post)
    monkeypatch.setattr(ads, "_connection", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(latehub, "ad_accounts", lambda _connection_id: [
        {"id": "act_approved", "name": "Brand", "currency": "USD", "selected": True},
    ])
    body = ads.BoostIn(
        post_id=18, platform="meta", ad_connection_id="meta-connection", ad_account_id="act_approved",
        campaign_name="Top reel", daily_budget=Decimal("10.50"), duration_days=7,
        goal="video_views", special_ad_categories=["HOUSING"],
    )

    request, returned_post = ads._boost_request(7, body)

    assert returned_post is post
    assert request["accountId"] == "meta-connection"
    assert request["adAccountId"] == "act_approved"
    assert request["platformPostId"] == "meta-post-22"
    assert request["campaignName"] == "Top reel"
    assert request["budgetAmount"] == 10.5
    assert request["budgetType"] == "daily"
    assert request["specialAdCategories"] == ["HOUSING"]
    start = datetime.fromisoformat(request["startDate"])
    end = datetime.fromisoformat(request["endDate"])
    assert (end - start).days == 7


def test_tiktok_boost_requires_matching_publishing_identity(monkeypatch):
    post = {
        "id": 18, "status": "posted", "external_id": "tt-post-22", "content_type": "short_video",
        "hook": "A good hook", "caption": "Caption", "views": 1200,
        "handle": "@creator", "account_platform": "TikTok",
    }
    monkeypatch.setattr(ads, "_post_for_boost", lambda *_args: post)
    monkeypatch.setattr(ads, "_connection", lambda *_args: {"id": "tt-ads-connection"})
    monkeypatch.setattr(latehub, "ad_accounts", lambda _connection_id: [
        {"id": "advertiser-1", "name": "TikTok Advertiser", "currency": "USD", "selected": True},
    ])
    monkeypatch.setattr(latehub, "accounts", lambda _workspace_id: [
        {"id": "wrong-account", "platform": "tiktok", "username": "@someoneelse"},
        {"id": "matching-account", "platform": "tiktok", "username": "@creator"},
    ])
    body = ads.BoostIn(
        post_id=18, platform="tiktok", ad_connection_id="tt-ads-connection",
        ad_account_id="advertiser-1", campaign_name="Top video",
        daily_budget=Decimal("25"), duration_days=3,
    )

    request, _ = ads._boost_request(7, body)

    assert request["accountId"] == "matching-account"
    assert request["adAccountId"] == "advertiser-1"
    assert request["platformPostId"] == "tt-post-22"


def test_tiktok_boost_refuses_when_publishing_account_does_not_match(monkeypatch):
    post = {
        "handle": "@creator", "account_platform": "TikTok", "external_id": "tt-post-22",
        "content_type": "short_video",
    }
    monkeypatch.setattr(ads, "_post_for_boost", lambda *_args: post)
    monkeypatch.setattr(ads, "_connection", lambda *_args: {"id": "tt-ads-connection"})
    monkeypatch.setattr(latehub, "ad_accounts", lambda _connection_id: [
        {"id": "advertiser-1", "selected": True},
    ])
    monkeypatch.setattr(latehub, "accounts", lambda _workspace_id: [])
    body = ads.BoostIn(
        post_id=18, platform="tiktok", ad_connection_id="tt-ads-connection",
        ad_account_id="advertiser-1", campaign_name="Top video",
        daily_budget=Decimal("25"), duration_days=3,
    )

    with pytest.raises(HTTPException) as error:
        ads._boost_request(7, body)

    assert error.value.status_code == 400
    assert "Spark Ads" in error.value.detail


def test_provider_boost_uses_idempotency_key_and_records_success(monkeypatch):
    calls = []
    updates = []
    monkeypatch.setattr(ads.latehub, "boost_post", lambda body, key:
                        calls.append((body, key)) or {"campaignId": "campaign-1"})
    monkeypatch.setattr(ads.db, "execute", lambda query, params: updates.append((query, params)))
    body = {"campaignName": "Test campaign"}

    result = ads._provider_result(7, 42, body, "same-idempotency-key")

    assert result == {"id": 42, "status": "created", "campaign_name": "Test campaign"}
    assert calls == [(body, "same-idempotency-key")]
    assert updates[0][1][0].adapted == {"campaignId": "campaign-1"}
    assert updates[0][1][1:] == (42, 7)


def test_failed_boost_with_unknown_provider_result_can_be_retried_safely(monkeypatch):
    updates = []
    def timeout(*_args):
        raise latehub.LateHubError("Timed out")

    monkeypatch.setattr(ads.latehub, "boost_post", timeout)
    monkeypatch.setattr(ads.db, "execute", lambda query, params: updates.append((query, params)))

    with pytest.raises(HTTPException) as error:
        ads._provider_result(7, 42, {"campaignName": "Test"}, "same-idempotency-key")

    assert error.value.status_code == 502
    assert "SET status=%s" in updates[0][0]
    assert updates[0][1] == (
        "unknown", "Timed out", 42, 7,
    )


def test_retry_reuses_stored_request_and_idempotency_key(monkeypatch):
    request_body = {"campaignName": "Saved campaign"}
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {
        "id": 42, "status": "unknown", "request_json": request_body, "idempotency_key": "existing-key",
    })
    calls = []
    monkeypatch.setattr(ads, "_provider_result", lambda workspace_id, boost_id, body, key:
                        calls.append((workspace_id, boost_id, body, key)) or {"status": "created"})

    assert ads.retry_boost(42, _ctx()) == {"status": "created"}
    assert calls == [(7, 42, request_body, "existing-key")]


def _campaign_values(**overrides):
    values = {
        "ad_connection_id": "meta-connection",
        "ad_account_id": "act_approved",
        "campaign_name": "Spring launch",
        "objective": "traffic",
        "daily_budget": "25.00",
        "end_date": (date.today() + timedelta(days=7)).isoformat(),
        "creative_type": "image",
        "media_asset_id": 41,
        "post_id": None,
        "headline": "Spring is here",
        "body": "See what's new.",
        "link_url": "https://example.com/spring",
        "call_to_action": "LEARN_MORE",
        "countries": ["US", "CA"],
        "age_min": 21,
        "age_max": 55,
        "gender": "female",
        "interests": [{"id": "interest-1", "name": "Gardening"}],
        "placement_platforms": ["facebook", "instagram"],
        "special_ad_categories": [],
        "pixel_id": "1729525464415281",
        "conversion_event": "PURCHASE",
        "url_tags": "utm_source=meta&utm_medium=paid_social",
        "brand_logo_asset_id": None,
        "google_campaign_type": "search",
        "google_keywords": [],
        "google_extra_headlines": [],
        "google_extra_description": "",
        "google_business_name": "",
        "google_long_headline": "",
        "youtube_video_id": "",
        "linkedin_organization_id": "",
    }
    values.update(overrides)
    return ads.CampaignDraftIn(**values).model_dump(mode="json")


@pytest.mark.parametrize(
    ("campaign_type", "creative_type"),
    [("search", "text"), ("demand_gen", "video")],
)
def test_google_campaign_draft_can_be_created_without_general_media(monkeypatch, campaign_type, creative_type):
    calls = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "google-connection"})
    monkeypatch.setattr(ads.db, "one", lambda query, params: calls.append((query, params)) or {
        "id": 55, "status": "draft", "created_at": "now",
    })
    body = ads.CampaignDraftIn(**_campaign_values(
        platform="google",
        ad_connection_id="google-connection",
        ad_account_id="customer-123",
        google_campaign_type=campaign_type,
        creative_type=creative_type,
        media_asset_id=None,
        post_id=None,
        interests=[],
        placement_platforms=[],
        special_ad_categories=[],
        pixel_id="",
        url_tags="",
    ))

    result = ads.create_campaign_draft(body, _ctx())

    assert result["status"] == "draft"
    assert "INSERT INTO ad_campaign_drafts" in calls[0][0]
    assert calls[0][1][3] == "google"


@pytest.mark.parametrize(
    ("campaign_type", "creative_type"),
    [("search", "text"), ("demand_gen", "video")],
)
def test_google_campaign_draft_can_be_updated_without_general_media(monkeypatch, campaign_type, creative_type):
    calls = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "google-connection"})
    monkeypatch.setattr(ads.db, "one", lambda query, params: calls.append((query, params)) or {
        "id": 55, "status": "draft",
    })
    body = ads.CampaignDraftIn(**_campaign_values(
        platform="google",
        ad_connection_id="google-connection",
        ad_account_id="customer-123",
        google_campaign_type=campaign_type,
        creative_type=creative_type,
        media_asset_id=None,
        post_id=None,
        interests=[],
        placement_platforms=[],
        special_ad_categories=[],
        pixel_id="",
        url_tags="",
    ))

    result = ads.update_campaign_draft(55, body, _ctx())

    assert result == {"id": 55, "status": "draft"}
    assert "UPDATE ad_campaign_drafts SET" in calls[0][0]
    assert calls[0][1][2] == "google"


@pytest.mark.parametrize("operation", ["create", "update"])
def test_non_google_campaign_drafts_still_require_exactly_one_media_choice(monkeypatch, operation):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: pytest.fail("database should not be called"))
    body = ads.CampaignDraftIn(**_campaign_values(media_asset_id=None, post_id=None))

    with pytest.raises(HTTPException) as error:
        if operation == "create":
            ads.create_campaign_draft(body, _ctx())
        else:
            ads.update_campaign_draft(55, body, _ctx())

    assert error.value.status_code == 400
    assert "Choose exactly one" in error.value.detail


def test_meta_campaign_draft_maps_to_paused_create_payload(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {"content_type": "image/png"})
    monkeypatch.setattr(ads, "_asset_url", lambda *_args: "https://hooktrack.example/api/ads/media/ads/7/41/99/sig")

    request, connection = ads._campaign_request(7, _campaign_values(objective="conversions"))

    assert connection["id"] == "meta-connection"
    assert request["status"] == "PAUSED"
    assert request["accountId"] == "meta-connection"
    assert request["adAccountId"] == "act_approved"
    assert request["budgetAmount"] == 25.0
    assert request["budgetType"] == "daily"
    assert request["endDate"].endswith("T23:59:59Z")
    assert request["countries"] == ["US", "CA"]
    assert request["ageMin"] == 21
    assert request["ageMax"] == 55
    assert request["gender"] == "female"
    assert request["interests"] == [{"id": "interest-1", "name": "Gardening"}]
    assert request["placements"] == {"publisherPlatforms": ["facebook", "instagram"]}
    assert request["imageUrl"].startswith("https://hooktrack.example/")
    assert request["tracking"] == {
        "pixelId": "1729525464415281",
        "urlTags": [{"key": "utm_source", "value": "meta"}, {"key": "utm_medium", "value": "paid_social"}],
    }
    assert request["promotedObject"] == {
        "pixelId": "1729525464415281", "customEventType": "PURCHASE",
    }


@pytest.mark.parametrize(
    ("objective", "overrides", "expected"),
    [
        ("lead_generation", {"meta_lead_gen_form_id": "form-1", "meta_page_id": "page-1"},
         {"leadGenFormId": "form-1", "promotedObject": {"pageId": "page-1"}}),
        ("lead_conversion", {"conversion_event": "LEAD"},
         {"promotedObject": {"pixelId": "1729525464415281", "customEventType": "LEAD"}}),
        ("app_promotion", {"meta_application_id": "app-1",
                           "meta_object_store_url": "https://apps.example/app"},
         {"promotedObject": {"applicationId": "app-1", "objectStoreUrl": "https://apps.example/app"}}),
        ("catalog_sales", {"meta_product_set_id": "set-1", "meta_product_catalog_id": "catalog-1"},
         {"promotedObject": {
             "pixelId": "1729525464415281", "customEventType": "PURCHASE",
             "productSetId": "set-1", "productCatalogId": "catalog-1",
         }}),
        ("conversions", {"conversion_event": "OTHER", "meta_custom_event_str": "TrialStarted"},
         {"promotedObject": {
             "pixelId": "1729525464415281", "customEventType": "OTHER",
             "customEventStr": "TrialStarted",
         }}),
        ("conversions", {"pixel_id": "", "meta_custom_conversion_id": "custom-1"},
         {"promotedObject": {"customConversionId": "custom-1"}}),
    ],
)
def test_meta_campaign_supports_documented_conversion_goals(monkeypatch, objective, overrides, expected):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {"content_type": "image/png"})
    monkeypatch.setattr(ads, "_asset_url", lambda *_args: "https://hooktrack.example/ad.png")

    request, _ = ads._campaign_request(7, _campaign_values(objective=objective, **overrides))

    assert request["status"] == "PAUSED"
    for key, value in expected.items():
        assert request[key] == value


@pytest.mark.parametrize(
    ("objective", "overrides", "message"),
    [
        ("lead_generation", {}, "instant form"),
        ("lead_conversion", {"pixel_id": "", "meta_custom_conversion_id": ""},
         "pixel or custom conversion"),
        ("conversions", {"conversion_event": "OTHER"}, "custom Meta event name"),
        ("app_promotion", {"meta_application_id": "app-1", "meta_object_store_url": "http://apps.example/app"},
         "HTTPS app store URL"),
        ("catalog_sales", {"meta_product_set_id": ""}, "product set ID"),
    ],
)
def test_meta_campaign_validates_objective_specific_inputs(monkeypatch, objective, overrides, message):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})

    with pytest.raises(HTTPException, match=message):
        ads._campaign_request(7, _campaign_values(objective=objective, **overrides))


def test_non_meta_platform_rejects_meta_only_objective(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "tiktok-connection"})

    with pytest.raises(HTTPException, match="TikTok supports"):
        ads._campaign_request(7, _campaign_values(
            platform="tiktok", ad_connection_id="tiktok-connection", ad_account_id="advertiser-1",
            objective="lead_generation", creative_type="video", placement_platforms=[], url_tags="",
        ))


def test_tiktok_campaign_maps_video_targeting_and_pixel_to_create_payload(monkeypatch):
    selected = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *args: selected.append(args) or {"id": "tiktok-connection"})
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {"content_type": "video/mp4"})
    monkeypatch.setattr(ads, "_asset_url", lambda *_args: "https://hooktrack.example/ad.mp4")
    values = _campaign_values(
        platform="tiktok",
        ad_connection_id="tiktok-connection",
        ad_account_id="advertiser-1",
        objective="conversions",
        daily_budget="20",
        creative_type="video",
        headline="",
        pixel_id="7987654321098765432",
        conversion_event="ON_WEB_ORDER",
        placement_platforms=[],
        url_tags="",
    )

    request, connection = ads._campaign_request(7, values)

    assert selected == [(7, "tiktok-connection", "advertiser-1", "tiktok")]
    assert connection["id"] == "tiktok-connection"
    assert request["status"] == "PAUSED"
    assert request["accountId"] == "tiktok-connection"
    assert request["adAccountId"] == "advertiser-1"
    assert request["budgetAmount"] == 20
    assert request["imageUrl"] == "https://hooktrack.example/ad.mp4"
    assert request["ageMin"] == 21
    assert request["ageMax"] == 55
    assert request["gender"] == "female"
    assert request["interests"] == [{"id": "interest-1", "name": "Gardening"}]
    assert request["promotedObject"] == {
        "pixelId": "7987654321098765432",
        "customEventType": "ON_WEB_ORDER",
    }
    assert "headline" not in request
    assert "video" not in request
    assert "placements" not in request
    assert "tracking" not in request
    assert "advantageAudience" not in request


def test_google_search_campaign_maps_keywords_and_rsa_text(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "google-connection"})
    values = _campaign_values(
        platform="google",
        ad_connection_id="google-connection",
        ad_account_id="customer-123",
        headline="Find better tools",
        body="Find software for your work.",
        daily_budget="35",
        google_keywords=["project software", "team planning"],
        google_extra_headlines=["Plan work together", "Start your free trial"],
        google_extra_description="Make project planning simple.",
        media_asset_id=None,
        creative_type="text",
        interests=[],
        placement_platforms=[],
        special_ad_categories=[],
        pixel_id="",
        url_tags="",
    )

    request, connection = ads._campaign_request(7, values)

    assert connection["id"] == "google-connection"
    assert request["status"] == "PAUSED"
    assert request["campaignType"] == "search"
    assert request["keywords"] == ["project software", "team planning"]
    assert request["additionalHeadlines"] == ["Plan work together", "Start your free trial"]
    assert request["additionalDescriptions"] == ["Make project planning simple."]
    assert request["budgetAmount"] == 35
    assert "imageUrl" not in request
    assert "goal" in request


def test_google_youtube_demand_gen_campaign_uses_video_id_and_uploaded_logo(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "google-connection"})
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {
        "file_path": "7/ads/logo.png", "content_type": "image/png", "file_size": 1024,
    })
    monkeypatch.setattr(ads.media, "_abs", lambda _path: Path("logo.png"))
    monkeypatch.setattr(ads, "_image_dimensions", lambda _path: (512, 512))
    monkeypatch.setattr(ads, "_asset_url", lambda *_args: "https://hooktrack.example/logo.png")
    values = _campaign_values(
        platform="google",
        ad_connection_id="google-connection",
        ad_account_id="customer-123",
        google_campaign_type="demand_gen",
        headline="Watch us work",
        body="Social marketing made easier.",
        google_business_name="HookTrack",
        google_long_headline="Make every social campaign easier to manage",
        youtube_video_id="abcdefghijk",
        brand_logo_asset_id=42,
        media_asset_id=None,
        creative_type="video",
        objective="traffic",
        interests=[],
        placement_platforms=[],
        special_ad_categories=[],
        pixel_id="",
        url_tags="",
    )

    request, _ = ads._campaign_request(7, values)

    assert request["campaignType"] == "demand_gen"
    assert request["demandGen"]["youtubeVideoIds"] == ["abcdefghijk"]
    assert request["demandGen"]["channels"] == ["youtube_in_stream", "youtube_in_feed", "youtube_shorts"]
    assert request["demandGen"]["images"] == {"logo": ["https://hooktrack.example/logo.png"]}
    assert request["demandGen"]["businessName"] == "HookTrack"
    assert "goal" not in request
    assert "imageUrl" not in request


@pytest.mark.parametrize(
    ("image_data", "expected_dimensions"),
    [
        (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 300, 300),
         (300, 300)),
        (b"\xff\xd8\xff\xc0\x00\x0b\x08\x00\x64\x00\x80\x03", (128, 100)),
        (b"not an image", None),
        (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 0, 0), None),
    ],
)
def test_campaign_logo_dimension_reader(image_data, expected_dimensions, tmp_path):
    image_path = tmp_path / "logo"
    image_path.write_bytes(image_data)

    assert ads._image_dimensions(image_path) == expected_dimensions


@pytest.mark.parametrize("dimensions", [None, (640, 320)])
def test_google_demand_gen_rejects_invalid_or_non_square_logo(monkeypatch, dimensions):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "google-connection"})
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {
        "file_path": "7/ads/logo.png", "content_type": "image/png", "file_size": 1024,
    })
    monkeypatch.setattr(ads.media, "_abs", lambda _path: Path("logo.png"))
    monkeypatch.setattr(ads, "_image_dimensions", lambda _path: dimensions)
    values = _campaign_values(
        platform="google",
        ad_connection_id="google-connection",
        ad_account_id="customer-123",
        google_campaign_type="demand_gen",
        headline="Watch us work",
        body="Social marketing made easier.",
        google_business_name="HookTrack",
        google_long_headline="Make every social campaign easier to manage",
        youtube_video_id="abcdefghijk",
        brand_logo_asset_id=42,
        media_asset_id=None,
        creative_type="video",
        objective="traffic",
        interests=[],
        placement_platforms=[],
        special_ad_categories=[],
        pixel_id="",
        url_tags="",
    )

    with pytest.raises(HTTPException) as error:
        ads._campaign_request(7, values)

    assert error.value.status_code == 400
    assert "square JPEG or PNG" in error.value.detail


def test_linkedin_campaign_maps_single_image_and_company_page(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "linkedin-connection"})
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {
        "content_type": "image/jpeg", "file_size": 1024,
    })
    monkeypatch.setattr(ads, "_asset_url", lambda *_args: "https://hooktrack.example/ad.jpg")
    values = _campaign_values(
        platform="linkedin",
        ad_connection_id="linkedin-connection",
        ad_account_id="sponsored-123",
        daily_budget="10",
        creative_type="image",
        linkedin_organization_id="urn:li:organization:1234",
        call_to_action="LEARN_MORE",
        interests=[],
        placement_platforms=[],
        special_ad_categories=[],
        pixel_id="",
        url_tags="",
    )

    request, _ = ads._campaign_request(7, values)

    assert request["accountId"] == "linkedin-connection"
    assert request["organizationId"] == "urn:li:organization:1234"
    assert request["status"] == "PAUSED"
    assert request["imageUrl"] == "https://hooktrack.example/ad.jpg"
    assert request["targeting"]["countries"] == ["US", "CA"]
    assert request["budgetAmount"] == 10
    assert "video" not in request


def test_tiktok_campaign_requires_minimum_daily_budget(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "tiktok-connection"})

    with pytest.raises(HTTPException) as error:
        ads._campaign_request(7, _campaign_values(
            platform="tiktok",
            daily_budget="19.99",
            creative_type="video",
            headline="",
            placement_platforms=[],
            url_tags="",
        ))

    assert error.value.status_code == 400
    assert "minimum daily budget" in error.value.detail


def test_tiktok_campaign_rejects_image_creatives(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "tiktok-connection"})

    with pytest.raises(HTTPException) as error:
        ads._campaign_request(7, _campaign_values(
            platform="tiktok",
            creative_type="image",
            headline="",
            placement_platforms=[],
            url_tags="",
        ))

    assert error.value.status_code == 400
    assert "video creative" in error.value.detail


def test_video_views_campaign_requires_video_asset(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})

    with pytest.raises(HTTPException) as error:
        ads._campaign_request(7, _campaign_values(objective="video_views"))

    assert error.value.status_code == 400
    assert "video creative" in error.value.detail


def test_conversions_campaign_requires_pixel(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})

    with pytest.raises(HTTPException) as error:
        ads._campaign_request(7, _campaign_values(objective="conversions", pixel_id=""))

    assert error.value.status_code == 400
    assert "pixel" in error.value.detail


def test_special_category_removes_restricted_age_gender_targeting(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {"content_type": "image/png"})
    monkeypatch.setattr(ads, "_asset_url", lambda *_args: "https://hooktrack.example/ad.png")
    values = _campaign_values(
        interests=[],
        special_ad_categories=["HOUSING"],
        age_min=18,
        age_max=65,
        gender="all",
    )

    request, _ = ads._campaign_request(7, values)

    assert request["specialAdCategories"] == ["HOUSING"]
    assert "ageMin" not in request
    assert "ageMax" not in request
    assert "gender" not in request


def test_campaign_create_saves_provider_ids_and_requires_paused_status(monkeypatch):
    updates = []
    monkeypatch.setattr(ads.latehub, "create_standalone_ad", lambda *_args: {
        "ad": {
            "_id": "zernio-ad", "platformCampaignId": "campaign-1",
            "platformAdSetId": "adset-1", "configuredStatus": "PAUSED", "status": "paused",
        },
        "message": "Ad created",
    })
    monkeypatch.setattr(ads.db, "execute", lambda query, params: updates.append((query, params)))

    result = ads._campaign_create_result(7, 42, {"name": "Spring launch"}, "idempotency-key")

    assert result["status"] == "paused"
    assert updates[0][1][1:5] == ("campaign-1", "adset-1", "zernio-ad", 42)


def test_campaign_create_pauses_if_provider_does_not_confirm_paused(monkeypatch):
    updates = []
    pause_calls = []
    monkeypatch.setattr(ads.latehub, "create_standalone_ad", lambda *_args: {
        "ad": {"_id": "zernio-ad", "platformCampaignId": "campaign-1", "configuredStatus": "ACTIVE"},
    })
    monkeypatch.setattr(ads.latehub, "update_ad_campaign_status",
                        lambda campaign_id, status: pause_calls.append((campaign_id, status)) or {"status": "paused"})
    monkeypatch.setattr(ads.db, "execute", lambda query, params: updates.append((query, params)))

    result = ads._campaign_create_result(7, 42, {"name": "Spring launch"}, "idempotency-key")

    assert result["status"] == "paused"
    assert pause_calls == [("campaign-1", "paused")]


def test_tiktok_campaign_create_explicitly_confirms_paused_status(monkeypatch):
    updates = []
    pause_calls = []
    monkeypatch.setattr(ads.latehub, "create_standalone_ad", lambda *_args: {
        "ad": {
            "_id": "zernio-ad", "platform": "tiktok", "platformCampaignId": "campaign-1",
            "platformAdSetId": "adgroup-1", "status": "pending_review",
        },
        "message": "Ad created",
    })
    monkeypatch.setattr(ads.latehub, "update_ad_campaign_status",
                        lambda campaign_id, status, platform: pause_calls.append(
                            (campaign_id, status, platform)) or {"status": "paused"})
    monkeypatch.setattr(ads.db, "execute", lambda query, params: updates.append((query, params)))

    result = ads._campaign_create_result(
        7, 42, {"name": "Spring launch", "status": "PAUSED"}, "idempotency-key", "tiktok",
    )

    assert result["status"] == "paused"
    assert pause_calls == [("campaign-1", "paused", "tiktok")]
    assert updates[-1][1][1:5] == ("campaign-1", "adgroup-1", "zernio-ad", 42)


def test_google_campaign_create_explicitly_confirms_paused_status(monkeypatch):
    pause_calls = []
    monkeypatch.setattr(ads.latehub, "create_standalone_ad", lambda *_args: {
        "ad": {"_id": "zernio-ad", "platformCampaignId": "google-campaign"},
    })
    monkeypatch.setattr(ads.latehub, "update_ad_campaign_status",
                        lambda campaign_id, status, platform: pause_calls.append(
                            (campaign_id, status, platform)) or {"status": "paused"})
    monkeypatch.setattr(ads.db, "execute", lambda *_args: None)

    result = ads._campaign_create_result(
        7, 42, {"campaignType": "search"}, "google-key", "google",
    )

    assert result["status"] == "paused"
    assert pause_calls == [("google-campaign", "paused", "google")]


def test_linkedin_campaign_create_requires_all_activation_ids(monkeypatch):
    updates = []
    monkeypatch.setattr(ads.latehub, "create_standalone_ad", lambda *_args: {
        "ad": {"_id": "zernio-ad", "platformCampaignId": "group-1"},
    })
    monkeypatch.setattr(ads.db, "execute", lambda query, params: updates.append((query, params)))

    with pytest.raises(HTTPException) as error:
        ads._campaign_create_result(
            7, 42, {"name": "Spring launch"}, "linkedin-key", "linkedin",
        )

    assert error.value.status_code == 502
    assert "required campaign identifiers" in error.value.detail
    assert updates[0][1][2:5] == ("group-1", "", "zernio-ad")


def test_linkedin_campaign_launch_enables_group_campaign_and_ad_in_order(monkeypatch):
    calls = []
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {
        "id": 42, "status": "paused", "platform": "linkedin", "platform_campaign_id": "group-1",
        "platform_ad_set_id": "campaign-1", "zernio_ad_id": "ad-1", "pending_status": "",
    })
    monkeypatch.setattr(ads.db, "execute", lambda *_args: None)
    monkeypatch.setattr(ads.latehub, "update_ad_campaign_status",
                        lambda *args: calls.append(("group", args)) or {"status": "active"})
    monkeypatch.setattr(ads.latehub, "update_ad_set_status",
                        lambda *args: calls.append(("campaign", args)) or {"status": "active"})
    monkeypatch.setattr(ads.latehub, "update_ad_status",
                        lambda *args: calls.append(("ad", args)) or {"status": "active"})

    result = ads.update_campaign_status(42, ads.CampaignStatusIn(status="active"), _ctx())

    assert result["status"] == "active"
    assert calls == [
        ("group", ("group-1", "active", "linkedin")),
        ("campaign", ("campaign-1", "active", "linkedin")),
        ("ad", ("ad-1", "active", "linkedin")),
    ]


def test_linkedin_campaign_with_incomplete_ids_cannot_be_launched(monkeypatch):
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {
        "id": 42, "status": "paused", "platform": "linkedin", "platform_campaign_id": "group-1",
        "platform_ad_set_id": "", "zernio_ad_id": "ad-1", "pending_status": "",
    })
    monkeypatch.setattr(ads.db, "execute", lambda *_args: pytest.fail("status should not be updated"))
    monkeypatch.setattr(
        ads.latehub, "update_ad_campaign_status",
        lambda *_args: pytest.fail("provider should not be called"),
    )

    with pytest.raises(HTTPException) as error:
        ads.update_campaign_status(42, ads.CampaignStatusIn(status="active"), _ctx())

    assert error.value.status_code == 409
    assert "identifiers are incomplete" in error.value.detail


@pytest.mark.parametrize(
    ("provider_status", "expected_status", "pending_should_remain"),
    [(403, 403, False), (503, 502, True)],
)
def test_campaign_status_error_code_matches_provider_certainty(
    monkeypatch, provider_status, expected_status, pending_should_remain,
):
    updates = []
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {
        "id": 42, "status": "paused", "platform": "meta", "platform_campaign_id": "campaign-1",
        "platform_ad_set_id": "adset-1", "zernio_ad_id": "ad-1", "pending_status": "",
    })
    monkeypatch.setattr(ads.db, "execute", lambda query, params: updates.append((query, params)))

    def fail_provider(*_args, **_kwargs):
        raise latehub.LateHubError("provider rejected status change", provider_status)

    monkeypatch.setattr(ads.latehub, "update_ad_campaign_status", fail_provider)

    with pytest.raises(HTTPException) as error:
        ads.update_campaign_status(42, ads.CampaignStatusIn(status="active"), _ctx())

    assert error.value.status_code == expected_status
    assert ("pending_status=''" in updates[-1][0]) is not pending_should_remain


def test_campaign_reporting_merges_selected_accounts_for_all_builder_platforms(monkeypatch):
    monkeypatch.setattr(ads, "_connections", lambda _workspace_id: [
        {"id": "meta-connection", "platform": "metaads"},
        {"id": "tiktok-connection", "platform": "tiktokads"},
        {"id": "google-connection", "platform": "googleads"},
        {"id": "linkedin-connection", "platform": "linkedinads"},
    ])
    monkeypatch.setattr(ads.latehub, "ad_accounts", lambda connection_id: [
        {"id": f"advertiser-{connection_id}", "selected": True},
    ])
    monkeypatch.setattr(ads.latehub, "ensure_profile", lambda _workspace_id: "profile-7")
    calls = []

    def tree(params):
        calls.append(params)
        platform = params.get("platform", "meta")
        return {"campaigns": [{"platform": platform, "platformCampaignId": platform}]}

    monkeypatch.setattr(ads.latehub, "ad_campaign_tree", tree)

    result = ads.campaign_reporting(_ctx())

    assert {campaign["platform"] for campaign in result["campaigns"]} == {
        "meta", "tiktok", "google", "linkedin",
    }
    assert all(call["profileId"] == "profile-7" for call in calls)
    assert next(call for call in calls if call["adAccountId"].endswith("tiktok-connection"))["platform"] == "tiktok"
    assert not any(call["adAccountId"].endswith("meta-connection") and "platform" in call for call in calls)
    assert next(call for call in calls if call["adAccountId"].endswith("google-connection"))["platform"] == "google"
    assert next(call for call in calls if call["adAccountId"].endswith("linkedin-connection"))["platform"] == "linkedin"
