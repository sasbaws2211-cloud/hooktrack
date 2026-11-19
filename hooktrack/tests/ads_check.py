import os
import struct
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

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


def test_tiktok_ads_tool_scopes_campaign_and_pixel_requests_to_selected_advertiser(monkeypatch):
    seen = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *args: seen.append(("selected", args)) or {"id": "tiktok-connection"})
    monkeypatch.setattr(
        latehub, "tiktok_ads_request",
        lambda method, path, **kwargs: seen.append((method, path, kwargs)) or {"ok": True},
    )
    campaign = ads.AdsApiRequestIn(
        ad_connection_id="tiktok-connection", ad_account_id="advertiser-1",
        method="POST", path="/ads/create", body={"name": "TikTok draft", "validateOnly": True},
    )
    pixel = ads.AdsApiRequestIn(
        ad_connection_id="tiktok-connection", ad_account_id="advertiser-1",
        method="GET", path="/accounts/tiktok-connection/tracking-tags/pixel-1",
    )

    assert ads.tiktok_ads_request(campaign, _ctx()) == {"ok": True}
    assert ads.tiktok_ads_request(pixel, _ctx()) == {"ok": True}
    assert seen == [
        ("selected", (7, "tiktok-connection", "advertiser-1", "tiktok")),
        ("POST", "/ads/create", {
            "params": {"accountId": "tiktok-connection", "adAccountId": "advertiser-1"},
            "body": {"name": "TikTok draft", "validateOnly": True,
                     "accountId": "tiktok-connection", "adAccountId": "advertiser-1"},
        }),
        ("selected", (7, "tiktok-connection", "advertiser-1", "tiktok")),
        ("GET", "/accounts/tiktok-connection/tracking-tags/pixel-1", {
            "params": {"accountId": "tiktok-connection", "adAccountId": "advertiser-1"},
            "body": None,
        }),
    ]


def test_tiktok_spark_boost_uses_only_linked_workspace_posting_identity(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ads, "_selected_ad_account",
        lambda *_args: {"id": "tiktok-connection", "parent_account_id": "posting-account"},
    )
    monkeypatch.setattr(ads.latehub, "accounts", lambda _workspace_id: [
        {"id": "posting-account", "platform": "tiktok"},
        {"id": "other-tiktok-account", "platform": "tiktok"},
    ])
    monkeypatch.setattr(
        ads.latehub, "tiktok_ads_request",
        lambda method, path, **kwargs: calls.append((method, path, kwargs)) or {"ok": True},
    )
    request = ads.AdsApiRequestIn(
        ad_connection_id="tiktok-connection", ad_account_id="advertiser-1",
        method="POST", path="/ads/boost",
        body={"platformPostId": "video-1", "sparkAuthCode": "creator-code"},
    )

    assert ads.tiktok_ads_request(request, _ctx()) == {"ok": True}
    assert calls[0][2]["params"]["accountId"] == "posting-account"
    assert calls[0][2]["body"]["accountId"] == "posting-account"
    assert calls[0][2]["body"]["adAccountId"] == "advertiser-1"

    request.body["accountId"] = "other-tiktok-account"
    with pytest.raises(HTTPException, match="selected TikTok Ads connection"):
        ads.tiktok_ads_request(request, _ctx())


def test_tiktok_offline_events_are_workspace_scoped_and_require_connection_destination(monkeypatch):
    calls = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "tiktok-connection"})
    monkeypatch.setattr(ads.latehub, "ensure_profile", lambda workspace_id: f"profile-{workspace_id}")
    monkeypatch.setattr(
        ads.latehub, "tiktok_ads_request",
        lambda method, path, **kwargs: calls.append((method, path, kwargs)) or {"eventsReceived": 1},
    )
    request = ads.AdsApiRequestIn(
        ad_connection_id="tiktok-connection", ad_account_id="advertiser-1",
        method="POST", path="/events",
        body={
            "destinations": [{"accountId": "tiktok-connection", "destinationId": "event-set-1"}],
            "events": [{"eventName": "Purchase", "user": {"email": "customer@example.com"}}],
        },
    )

    assert ads.tiktok_ads_request(request, _ctx()) == {"eventsReceived": 1}
    assert calls == [("POST", "/events", {
        "params": {},
        "body": {
            "destinations": [{"accountId": "tiktok-connection", "destinationId": "event-set-1"}],
            "events": [{"eventName": "Purchase", "user": {"email": "customer@example.com"}}],
            "profileId": "profile-7",
        },
    })]

    request.body["destinations"][0]["accountId"] = "another-workspace-connection"
    with pytest.raises(HTTPException, match="selected workspace TikTok connection"):
        ads.tiktok_ads_request(request, _ctx())


def test_tiktok_pixel_install_requires_workspace_connected_store(monkeypatch):
    calls = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "tiktok-connection"})
    monkeypatch.setattr(ads.latehub, "accounts", lambda workspace_id: [
        {"id": "shopify-1", "platform": "shopify"},
        {"id": "wordpress-1", "platform": "wordpress"},
    ])
    monkeypatch.setattr(
        ads.latehub, "tiktok_ads_request",
        lambda method, path, **kwargs: calls.append((method, path, kwargs)) or {"installed": True},
    )
    request = ads.AdsApiRequestIn(
        ad_connection_id="tiktok-connection", ad_account_id="advertiser-1",
        method="POST",
        path="/accounts/tiktok-connection/tracking-tags/pixel-1/install",
        body={"storeAccountId": "shopify-1"},
    )

    assert ads.tiktok_ads_request(request, _ctx()) == {"installed": True}
    assert calls == [("POST", request.path, {
        "params": {"accountId": "tiktok-connection"},
        "body": {"storeAccountId": "shopify-1"},
    })]

    request.body["storeAccountId"] = "shopify-outside-workspace"
    with pytest.raises(HTTPException, match="connected to this workspace"):
        ads.tiktok_ads_request(request, _ctx())


@pytest.mark.parametrize("path", [
    "/ads/insights/reports", "/ads/rf-predictions",
    "/accounts/another-connection/tracking-tags",
    "/ads/../profiles",
])
def test_tiktok_ads_tool_rejects_unsupported_or_out_of_scope_paths(monkeypatch, path):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "tiktok-connection"})
    request = ads.AdsApiRequestIn(
        ad_connection_id="tiktok-connection", ad_account_id="advertiser-1",
        method="GET", path=path,
    )
    with pytest.raises(HTTPException):
        ads.tiktok_ads_request(request, _ctx())


def test_tiktok_ads_tool_rejects_pixel_and_pixel_event_deletion(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "tiktok-connection"})
    for path in (
        "/accounts/tiktok-connection/tracking-tags/pixel-1",
        "/accounts/tiktok-connection/tracking-tags/pixel-1/events/event-1",
    ):
        request = ads.AdsApiRequestIn(
            ad_connection_id="tiktok-connection", ad_account_id="advertiser-1",
            method="DELETE", path=path,
        )
        with pytest.raises(HTTPException, match="does not support pixel or pixel-event deletion"):
            ads.tiktok_ads_request(request, _ctx())


def test_tiktok_pixel_share_unlink_uses_scoped_advertiser(monkeypatch):
    calls = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "tiktok-connection"})
    monkeypatch.setattr(
        ads.latehub, "tiktok_ads_request",
        lambda method, path, **kwargs: calls.append((method, path, kwargs)) or {"unlinked": True},
    )
    request = ads.AdsApiRequestIn(
        ad_connection_id="tiktok-connection", ad_account_id="advertiser-1",
        method="DELETE",
        path="/accounts/tiktok-connection/tracking-tags/pixel-1/shared-accounts",
        query={"adAccountId": "advertiser-1"},
    )

    assert ads.tiktok_ads_request(request, _ctx()) == {"unlinked": True}
    assert calls == [("DELETE", request.path, {
        "params": {"accountId": "tiktok-connection", "adAccountId": "advertiser-1"},
        "body": {},
    })]

    request.query["adAccountId"] = "advertiser-2"
    with pytest.raises(HTTPException, match="adAccountId must match"):
        ads.tiktok_ads_request(request, _ctx())


def test_google_ads_tools_scope_read_and_create_requests_to_selected_customer(monkeypatch):
    seen = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *args: seen.append(("selected", args)) or {"id": "google-connection"})
    monkeypatch.setattr(
        ads.latehub, "google_ads_request",
        lambda method, path, **kwargs: seen.append((method, path, kwargs)) or {"ok": True},
    )
    read = ads.AdsApiRequestIn(
        ad_connection_id="google-connection", ad_account_id="customer-1",
        method="GET", path="/ads/insights",
        query={"query": "SELECT campaign.name FROM campaign"},
    )
    create = ads.AdsApiRequestIn(
        ad_connection_id="google-connection", ad_account_id="customer-1",
        method="POST", path="/ads/create",
        body={"campaignType": "pmax"},
        idempotency_key="google-create-123",
    )

    assert ads.google_ads_request(read, _ctx()) == {"ok": True}
    assert ads.google_ads_request(create, _ctx()) == {"ok": True}
    assert seen == [
        ("selected", (7, "google-connection", "customer-1", "google")),
        ("GET", "/ads/insights", {
            "params": {"query": "SELECT campaign.name FROM campaign",
                       "accountId": "google-connection", "adAccountId": "customer-1"},
            "body": None,
        }),
        ("selected", (7, "google-connection", "customer-1", "google")),
        ("POST", "/ads/create", {
            "params": {"accountId": "google-connection", "adAccountId": "customer-1"},
            "body": {"campaignType": "pmax",
                     "accountId": "google-connection", "adAccountId": "customer-1"},
            "idempotency_key": "google-create-123",
        }),
    ]


def test_google_ads_tools_reject_idempotency_key_for_non_create_requests(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "google-connection"})
    request = ads.AdsApiRequestIn(
        ad_connection_id="google-connection", ad_account_id="customer-1",
        method="GET", path="/ads/tree", idempotency_key="not-valid-here",
    )
    with pytest.raises(HTTPException, match="supported only for Google campaign creation"):
        ads.google_ads_request(request, _ctx())


def test_google_ads_tools_do_not_forward_idempotency_key_for_validate_only_create(monkeypatch):
    calls = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "google-connection"})
    monkeypatch.setattr(
        ads.latehub, "google_ads_request",
        lambda method, path, **kwargs: calls.append((method, path, kwargs)) or {"ok": True},
    )
    request = ads.AdsApiRequestIn(
        ad_connection_id="google-connection", ad_account_id="customer-1",
        method="POST", path="/ads/create",
        body={"campaignType": "pmax", "validateOnly": True},
        idempotency_key="google-validate-123",
    )

    assert ads.google_ads_request(request, _ctx()) == {"ok": True}
    assert calls == [("POST", "/ads/create", {
        "params": {"accountId": "google-connection", "adAccountId": "customer-1"},
        "body": {"campaignType": "pmax", "validateOnly": True,
                 "accountId": "google-connection", "adAccountId": "customer-1"},
    })]


def test_google_ads_tools_reject_invalid_idempotency_key_characters(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "google-connection"})
    request = ads.AdsApiRequestIn(
        ad_connection_id="google-connection", ad_account_id="customer-1",
        method="POST", path="/ads/create", idempotency_key="invalid key",
    )
    with pytest.raises(HTTPException, match="must use letters, numbers"):
        ads.google_ads_request(request, _ctx())


def test_google_offline_conversions_bind_profile_connection_and_customer_destination(monkeypatch):
    calls = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "google-connection"})
    monkeypatch.setattr(ads.latehub, "ensure_profile", lambda workspace_id: f"profile-{workspace_id}")
    monkeypatch.setattr(
        ads.latehub, "google_ads_request",
        lambda method, path, **kwargs: calls.append((method, path, kwargs)) or {"eventsReceived": 1},
    )
    request = ads.AdsApiRequestIn(
        ad_connection_id="google-connection", ad_account_id="customer-1",
        method="POST", path="/events",
        body={
            "destinations": [{
                "accountId": "google-connection",
                "destinationId": "customers/customer-1/conversionActions/123",
            }],
            "events": [{"eventName": "Purchase", "user": {"email": "customer@example.com"}}],
        },
    )

    assert ads.google_ads_request(request, _ctx()) == {"eventsReceived": 1}
    assert calls == [("POST", "/events", {
        "params": {},
        "body": {
            "destinations": [{
                "accountId": "google-connection",
                "destinationId": "customers/customer-1/conversionActions/123",
            }],
            "events": [{"eventName": "Purchase", "user": {"email": "customer@example.com"}}],
            "profileId": "profile-7",
        },
    })]

    request.body["destinations"][0]["destinationId"] = "customers/customer-2/conversionActions/123"
    with pytest.raises(HTTPException, match="selected Google Ads customer"):
        ads.google_ads_request(request, _ctx())


@pytest.mark.parametrize("path", [
    "/ads/../profiles", "/accounts/google-connection/tracking-tags",
    "/events/another-route",
])
def test_google_ads_tool_rejects_non_ads_and_out_of_scope_paths(monkeypatch, path):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "google-connection"})
    request = ads.AdsApiRequestIn(
        ad_connection_id="google-connection", ad_account_id="customer-1",
        method="GET", path=path,
    )
    with pytest.raises(HTTPException):
        ads.google_ads_request(request, _ctx())


def test_meta_reach_frequency_requests_keep_quote_reserve_and_read_account_scoped(monkeypatch):
    calls = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(
        ads.latehub, "meta_ads_request",
        lambda method, path, **kwargs: calls.append((method, path, kwargs)) or {"ok": True},
    )
    quote = ads.MetaAdsRequestIn(
        ad_connection_id="meta-connection", ad_account_id="act-1",
        method="POST", path="/ads/rf-predictions",
        body={"budgetAmount": 500, "targeting": {"countries": ["US"]}},
    )
    reserve = ads.MetaAdsRequestIn(
        ad_connection_id="meta-connection", ad_account_id="act-1",
        method="POST", path="/ads/rf-predictions/quote-1/reserve",
        body={"accountId": "meta-connection", "adAccountId": "act-1"},
    )
    read = ads.MetaAdsRequestIn(
        ad_connection_id="meta-connection", ad_account_id="act-1",
        method="GET", path="/ads/rf-predictions/reserved-1",
    )

    assert ads.meta_ads_request(quote, _ctx()) == {"ok": True}
    assert ads.meta_ads_request(reserve, _ctx()) == {"ok": True}
    assert ads.meta_ads_request(read, _ctx()) == {"ok": True}

    assert calls == [
        ("POST", "/ads/rf-predictions", {
            "params": {"accountId": "meta-connection", "adAccountId": "act-1"},
            "body": {"budgetAmount": 500, "targeting": {"countries": ["US"]},
                     "accountId": "meta-connection", "adAccountId": "act-1"},
        }),
        ("POST", "/ads/rf-predictions/quote-1/reserve", {
            "params": {"accountId": "meta-connection", "adAccountId": "act-1"},
            "body": {"accountId": "meta-connection", "adAccountId": "act-1"},
        }),
        ("GET", "/ads/rf-predictions/reserved-1", {
            "params": {"accountId": "meta-connection", "adAccountId": "act-1"},
            "body": None,
        }),
    ]


def test_meta_analytics_requests_bind_daily_timeline_and_live_insights_to_selected_account(monkeypatch):
    calls = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *args: calls.append(("selected", args)) or {"id": "meta-connection"})
    monkeypatch.setattr(
        ads.latehub, "meta_ads_request",
        lambda method, path, **kwargs: calls.append((method, path, kwargs)) or
        ({"rows": []} if path == "/ads/timeline" else {"objectId": "act-1", "data": [], "paging": {}}),
    )
    timeline = ads.MetaAdsRequestIn(
        ad_connection_id="meta-connection", ad_account_id="act-1",
        method="GET", path="/ads/timeline",
        query={"fromDate": "2026-10-01", "toDate": "2026-10-31", "compare": "previous"},
    )
    insights = ads.MetaAdsRequestIn(
        ad_connection_id="meta-connection", ad_account_id="act-1",
        method="GET", path="/ads/insights",
        query={
            "objectId": "act-1", "level": "campaign", "fields": "campaign_id,spend",
            "breakdowns": "publisher_platform", "fromDate": "2026-10-01", "toDate": "2026-10-31",
            "limit": 100,
        },
    )

    assert ads.meta_ads_request(timeline, _ctx()) == {"rows": []}
    assert ads.meta_ads_request(insights, _ctx()) == {"objectId": "act-1", "data": [], "paging": {}}
    assert calls == [
        ("selected", (7, "meta-connection", "act-1", "meta")),
        ("GET", "/ads/timeline", {"params": {
            "fromDate": "2026-10-01", "toDate": "2026-10-31", "compare": "previous",
            "accountId": "meta-connection", "adAccountId": "act-1",
        }, "body": None}),
        ("selected", (7, "meta-connection", "act-1", "meta")),
        ("GET", "/ads/insights", {"params": {
            "objectId": "act-1", "level": "campaign", "fields": "campaign_id,spend",
            "breakdowns": "publisher_platform", "fromDate": "2026-10-01", "toDate": "2026-10-31",
            "limit": 100, "accountId": "meta-connection", "adAccountId": "act-1",
        }, "body": None}),
    ]


def test_meta_async_insights_report_create_is_account_scoped_and_keeps_native_body_types(monkeypatch):
    calls = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(
        ads.latehub, "meta_ads_request",
        lambda method, path, **kwargs: calls.append((method, path, kwargs)) or
        ({"reportRunId": "report-1"} if method == "POST" else {
            "reportRunId": "report-1", "status": "Job Running", "percentCompletion": 50,
        }),
    )
    request = ads.MetaAdsRequestIn(
        ad_connection_id="meta-connection", ad_account_id="act-1",
        method="POST", path="/ads/insights/reports",
        body={
            "objectId": "act-1", "level": "ad", "fields": "ad_id,spend,actions",
            "actionAttributionWindows": ["7d_click", "1d_view"],
            "useUnifiedAttributionSetting": True,
            "fromDate": "2026-10-01", "toDate": "2026-10-31",
        },
    )
    poll = ads.MetaAdsRequestIn(
        ad_connection_id="meta-connection", ad_account_id="act-1",
        method="GET", path="/ads/insights/reports/report-1",
    )

    assert ads.meta_ads_request(request, _ctx()) == {"reportRunId": "report-1"}
    assert ads.meta_ads_request(poll, _ctx()) == {
        "reportRunId": "report-1", "status": "Job Running", "percentCompletion": 50,
    }
    assert calls == [("POST", "/ads/insights/reports", {
        "params": {"accountId": "meta-connection", "adAccountId": "act-1"},
        "body": {
            "objectId": "act-1", "level": "ad", "fields": "ad_id,spend,actions",
            "actionAttributionWindows": ["7d_click", "1d_view"],
            "useUnifiedAttributionSetting": True,
            "fromDate": "2026-10-01", "toDate": "2026-10-31",
            "accountId": "meta-connection", "adAccountId": "act-1",
        },
    }), ("GET", "/ads/insights/reports/report-1", {
        "params": {"accountId": "meta-connection", "adAccountId": "act-1"},
        "body": None,
    })]


def test_meta_draft_preview_injects_selected_workspace_and_ad_account(monkeypatch):
    seen = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(
        latehub, "meta_ads_request",
        lambda method, path, **kwargs: seen.append((method, path, kwargs)) or {"previews": []},
    )
    request = ads.MetaAdsRequestIn(
        ad_connection_id="meta-connection", ad_account_id="act-1",
        method="POST", path="/ads/preview",
        body={"creativeSpec": {"object_story_spec": {"page_id": "page-1"}}, "formats": ["INSTAGRAM_STORY"]},
    )

    assert ads.meta_ads_request(request, _ctx()) == {"previews": []}
    assert seen == [("POST", "/ads/preview", {
        "params": {"accountId": "meta-connection", "adAccountId": "act-1"},
        "body": {
            "creativeSpec": {"object_story_spec": {"page_id": "page-1"}},
            "formats": ["INSTAGRAM_STORY"],
            "accountId": "meta-connection",
            "adAccountId": "act-1",
        },
    })]


def test_meta_audience_membership_request_is_account_scoped_and_keeps_user_data_in_body(monkeypatch):
    seen = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(
        latehub, "meta_ads_request",
        lambda method, path, **kwargs: seen.append((method, path, kwargs)) or {"numReceived": 1},
    )
    request = ads.MetaAdsRequestIn(
        ad_connection_id="meta-connection", ad_account_id="act-1",
        method="DELETE", path="/ads/audiences/audience-1/users",
        body={"users": [{"email": "customer@example.com"}]},
    )

    assert ads.meta_ads_request(request, _ctx()) == {"numReceived": 1}
    assert seen == [("DELETE", "/ads/audiences/audience-1/users", {
        "params": {"accountId": "meta-connection", "adAccountId": "act-1"},
        "body": {"users": [{"email": "customer@example.com"}]},
    })]


def test_meta_campaign_tree_is_scoped_to_workspace_profile_and_selected_ad_account(monkeypatch):
    seen = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(ads.latehub, "ensure_profile", lambda workspace_id: f"profile-{workspace_id}")
    monkeypatch.setattr(
        latehub, "meta_ads_request",
        lambda method, path, **kwargs: seen.append((method, path, kwargs)) or {"campaigns": []},
    )
    request = ads.MetaAdsRequestIn(
        ad_connection_id="meta-connection", ad_account_id="act-1",
        method="GET", path="/ads/tree", query={"fromDate": "2026-10-01", "toDate": "2026-10-31"},
    )

    assert ads.meta_ads_request(request, _ctx()) == {"campaigns": []}
    assert seen == [("GET", "/ads/tree", {"params": {
        "fromDate": "2026-10-01",
        "toDate": "2026-10-31",
        "accountId": "meta-connection",
        "adAccountId": "act-1",
        "profileId": "profile-7",
    }, "body": None})]


def test_meta_campaign_tree_rejects_another_workspace_profile(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(ads.latehub, "ensure_profile", lambda _workspace_id: "profile-7")
    request = ads.MetaAdsRequestIn(
        ad_connection_id="meta-connection", ad_account_id="act-1",
        method="GET", path="/ads/tree", query={"profileId": "another-profile"},
    )

    with pytest.raises(HTTPException, match="profileId must match"):
        ads.meta_ads_request(request, _ctx())


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
    monkeypatch.setattr(ads.latehub, "ensure_profile", lambda _workspace_id: "profile-7")
    monkeypatch.setattr(ads.latehub, "ad_tracking_tags", lambda *_args: [{"id": "pixel-1"}])
    monkeypatch.setattr(
        latehub, "meta_ads_request",
        lambda method, path, **kwargs: calls.append((method, path, kwargs)) or {"events_received": 1},
    )
    request = ads.MetaAdsRequestIn(
        ad_connection_id="meta-connection", ad_account_id="act-1",
        method="POST", path="/events",
        body={"destinationId": "pixel-1", "events": [{"eventName": "Lead", "eventTime": 123}]},
    )

    assert ads.meta_ads_request(request, _ctx()) == {"events_received": 1}
    assert calls == [("POST", "/events", {
        "params": {},
        "body": {
            "profileId": "profile-7",
            "destinations": [{"accountId": "meta-connection", "destinationId": "pixel-1"}],
            "events": [{"eventName": "Lead", "eventTime": 123}],
        },
    })]


def test_meta_events_tool_rejects_pixel_outside_selected_account(monkeypatch):
    calls = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(ads.latehub, "ensure_profile", lambda _workspace_id: "profile-7")
    monkeypatch.setattr(ads.latehub, "ad_tracking_tags", lambda *_args: [{"id": "another-pixel"}])
    monkeypatch.setattr(latehub, "meta_ads_request", lambda *_args, **_kwargs: calls.append("called"))
    request = ads.MetaAdsRequestIn(
        ad_connection_id="meta-connection", ad_account_id="act-1",
        method="POST", path="/events",
        body={"destinationId": "pixel-1", "events": [{"eventName": "Lead", "eventTime": 123}]},
    )

    with pytest.raises(HTTPException, match="pixel available to the selected ad account"):
        ads.meta_ads_request(request, _ctx())
    assert calls == []


def test_meta_events_accept_only_destinations_for_selected_connection(monkeypatch):
    calls = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(ads.latehub, "ensure_profile", lambda _workspace_id: "profile-7")
    monkeypatch.setattr(ads.latehub, "ad_tracking_tags", lambda *_args: [{"id": "pixel-1"}])
    monkeypatch.setattr(
        latehub, "meta_ads_request",
        lambda method, path, **kwargs: calls.append((method, path, kwargs)) or {"events_received": 1},
    )
    request = ads.MetaAdsRequestIn(
        ad_connection_id="meta-connection", ad_account_id="act-1",
        method="POST", path="/events",
        body={
            "destinations": [{"accountId": "meta-connection", "destinationId": "pixel-1"}],
            "events": [{"eventName": "Purchase", "eventTime": 123}],
        },
    )

    assert ads.meta_ads_request(request, _ctx()) == {"events_received": 1}
    assert calls == [("POST", "/events", {
        "params": {},
        "body": {
            "profileId": "profile-7",
            "destinations": [{"accountId": "meta-connection", "destinationId": "pixel-1"}],
            "events": [{"eventName": "Purchase", "eventTime": 123}],
        },
    })]


def test_meta_lead_form_create_body_uses_provider_account_id_only(monkeypatch):
    calls = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(
        latehub, "meta_ads_request",
        lambda method, path, **kwargs: calls.append((method, path, kwargs)) or {"id": "form-1"},
    )
    request = ads.MetaAdsRequestIn(
        ad_connection_id="meta-connection", ad_account_id="act-1",
        method="POST", path="/ads/lead-forms",
        body={
            "name": "Test form",
            "privacyPolicyUrl": "https://example.com/privacy",
            "platformSpecificData": {"questions": [{"type": "EMAIL"}]},
        },
    )

    assert ads.meta_ads_request(request, _ctx()) == {"id": "form-1"}
    assert calls == [("POST", "/ads/lead-forms", {
        "params": {"accountId": "meta-connection", "adAccountId": "act-1"},
        "body": {
            "name": "Test form",
            "privacyPolicyUrl": "https://example.com/privacy",
            "platformSpecificData": {"questions": [{"type": "EMAIL"}]},
            "accountId": "meta-connection",
        },
    })]


def test_meta_pixel_create_is_scoped_to_selected_ad_account(monkeypatch):
    calls = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(
        latehub, "meta_ads_request",
        lambda method, path, **kwargs: calls.append((method, path, kwargs)) or {"id": "pixel-1"},
    )
    request = ads.MetaAdsRequestIn(
        ad_connection_id="meta-connection", ad_account_id="act-1",
        method="POST", path="/accounts/meta-connection/tracking-tags",
        body={"name": "Store pixel", "kind": "pixel"},
    )

    assert ads.meta_ads_request(request, _ctx()) == {"id": "pixel-1"}
    assert calls == [("POST", "/accounts/meta-connection/tracking-tags", {
        "params": {"accountId": "meta-connection", "adAccountId": "act-1"},
        "body": {"name": "Store pixel", "kind": "pixel", "adAccountId": "act-1"},
    })]


def test_meta_events_reject_empty_batches_before_provider_call(monkeypatch):
    calls = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(ads.latehub, "ensure_profile", lambda _workspace_id: "profile-7")
    monkeypatch.setattr(latehub, "meta_ads_request", lambda *_args, **_kwargs: calls.append("called"))
    request = ads.MetaAdsRequestIn(
        ad_connection_id="meta-connection", ad_account_id="act-1",
        method="POST", path="/events", body={"events": []},
    )

    with pytest.raises(HTTPException, match="at least one conversion event"):
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


def test_meta_campaign_maps_advantage_plus_features_and_multi_advertiser_separately(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {"content_type": "image/png"})
    monkeypatch.setattr(ads, "_asset_url", lambda *_args: "https://hooktrack.example/campaign-image.png")

    request, _ = ads._campaign_request(7, _campaign_values(
        meta_enhance_cta="OPT_IN",
        meta_image_brightness_and_contrast="OPT_OUT",
        meta_text_optimizations="OPT_IN",
        meta_multi_advertiser="OPT_OUT",
    ))

    assert request["creativeFeatures"] == {
        "enhance_cta": "OPT_IN",
        "image_brightness_and_contrast": "OPT_OUT",
        "text_optimizations": "OPT_IN",
    }
    assert request["multiAdvertiser"] == "OPT_OUT"
    assert "standard_enhancements" not in request["creativeFeatures"]


def test_meta_campaign_omits_default_advantage_plus_settings(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {"content_type": "image/png"})
    monkeypatch.setattr(ads, "_asset_url", lambda *_args: "https://hooktrack.example/campaign-image.png")

    request, _ = ads._campaign_request(7, _campaign_values())

    assert "creativeFeatures" not in request
    assert "multiAdvertiser" not in request


def test_non_meta_campaign_rejects_advantage_plus_settings(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "tiktok-connection"})

    with pytest.raises(HTTPException, match="only for Meta campaigns"):
        ads._campaign_request(7, _campaign_values(
            platform="tiktok",
            ad_connection_id="tiktok-connection",
            ad_account_id="advertiser-1",
            creative_type="video",
            meta_text_optimizations="OPT_OUT",
        ))


def test_meta_dynamic_creative_builds_image_asset_feed_from_workspace_assets(monkeypatch):
    seen = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(
        ads.db, "one",
        lambda query, params: seen.append((query, params)) or {"content_type": "image/png"},
    )
    monkeypatch.setattr(ads, "_asset_url", lambda _workspace_id, asset_id: f"https://hooktrack.example/{asset_id}.png")
    values = _campaign_values(
        media_asset_id=None,
        post_id=None,
        headline="",
        body="",
        link_url="",
        meta_creative_format="dynamic",
        meta_dynamic_media_type="image",
        meta_dynamic_media_asset_ids=[41, 42],
        meta_dynamic_bodies=["First primary text", "Second primary text"],
        meta_dynamic_titles=["First headline", "Second headline"],
        meta_dynamic_descriptions=["First description"],
        meta_dynamic_link_urls=["https://example.com/first", "https://example.com/second"],
        meta_dynamic_call_to_action_types=["LEARN_MORE", "SHOP_NOW"],
    )

    request, _ = ads._campaign_request(7, values)

    assert request["dynamicCreative"] == {
        "imageUrls": ["https://hooktrack.example/41.png", "https://hooktrack.example/42.png"],
        "callToActionTypes": ["LEARN_MORE", "SHOP_NOW"],
        "bodies": ["First primary text", "Second primary text"],
        "titles": ["First headline", "Second headline"],
        "descriptions": ["First description"],
        "linkUrls": ["https://example.com/first", "https://example.com/second"],
    }
    assert not {"imageUrl", "video", "headline", "body", "linkUrl", "callToAction"} & request.keys()
    assert [params for _query, params in seen] == [(41, 7), (42, 7)]


def test_meta_dynamic_creative_builds_video_asset_feed(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {"content_type": "video/mp4"})
    monkeypatch.setattr(ads, "_asset_url", lambda _workspace_id, asset_id: f"https://hooktrack.example/{asset_id}.mp4")

    request, _ = ads._campaign_request(7, _campaign_values(
        creative_type="video",
        media_asset_id=None,
        post_id=None,
        headline="",
        body="",
        link_url="",
        meta_creative_format="dynamic",
        meta_dynamic_media_type="video",
        meta_dynamic_media_asset_ids=[41],
        meta_dynamic_link_urls=["https://example.com/video"],
        meta_dynamic_call_to_action_types=["WATCH_MORE", "CALL_NOW"],
    ))

    assert request["dynamicCreative"] == {
        "videoUrls": ["https://hooktrack.example/41.mp4"],
        "callToActionTypes": ["WATCH_MORE", "CALL_NOW"],
        "linkUrls": ["https://example.com/video"],
    }


def test_meta_dynamic_creative_passes_optional_carousel_ad_format(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {"content_type": "image/png"})
    monkeypatch.setattr(ads, "_asset_url", lambda *_args: "https://hooktrack.example/41.png")

    request, _ = ads._campaign_request(7, _campaign_values(
        media_asset_id=None,
        post_id=None,
        headline="",
        body="",
        link_url="",
        meta_creative_format="dynamic",
        meta_dynamic_media_asset_ids=[41],
        meta_dynamic_ad_format="CAROUSEL_IMAGE",
        meta_dynamic_link_urls=["https://example.com"],
        meta_dynamic_call_to_action_types=["SHOP_NOW"],
    ))

    assert request["dynamicCreative"]["adFormat"] == "CAROUSEL_IMAGE"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"objective": "page_likes"}, "not supported with the page likes objective"),
        ({"meta_dynamic_media_type": "video", "creative_type": "image"}, "must match its image or video asset pool"),
        ({"meta_dynamic_ad_format": "CAROUSEL_IMAGE", "meta_dynamic_media_type": "video",
          "creative_type": "video"}, "ad format must match"),
    ],
)
def test_meta_dynamic_creative_rejects_incompatible_goal_or_format(monkeypatch, overrides, message):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {"content_type": "image/png"})
    monkeypatch.setattr(ads, "_asset_url", lambda *_args: "https://hooktrack.example/41.png")
    values = {
        "media_asset_id": None,
        "post_id": None,
        "headline": "",
        "body": "",
        "link_url": "",
        "meta_creative_format": "dynamic",
        "meta_dynamic_media_asset_ids": [41],
        "meta_dynamic_link_urls": ["https://example.com"],
        "meta_dynamic_call_to_action_types": ["LEARN_MORE"],
    }
    values.update(overrides)

    with pytest.raises(HTTPException, match=message):
        ads._campaign_request(7, _campaign_values(**values))


def test_meta_dynamic_creative_draft_does_not_require_single_media(monkeypatch):
    calls = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(ads.db, "one", lambda query, params: calls.append((query, params)) or {
        "id": 19, "status": "draft", "created_at": "2026-11-18T00:00:00Z",
    })
    body = ads.CampaignDraftIn(**_campaign_values(
        media_asset_id=None,
        post_id=None,
        headline="",
        body="",
        link_url="",
        meta_creative_format="dynamic",
        meta_dynamic_media_asset_ids=[41],
        meta_dynamic_link_urls=["https://example.com"],
        meta_dynamic_call_to_action_types=["LEARN_MORE"],
    ))

    result = ads.create_campaign_draft(body, _ctx())

    assert result["id"] == 19
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"meta_dynamic_media_asset_ids": []}, "between 1 and 10"),
        ({"meta_dynamic_media_asset_ids": [41, 41]}, "different uploaded asset"),
        ({"meta_dynamic_call_to_action_types": []}, "at least one call to action"),
        ({"meta_dynamic_link_urls": ["http://example.com"]}, "complete HTTPS URL"),
        ({"meta_dynamic_bodies": [""]}, "blank entries"),
        ({"meta_dynamic_call_to_action_types": ["UNSUPPORTED"]}, "supported Meta dynamic creative call-to-action"),
    ],
)
def test_meta_dynamic_creative_rejects_invalid_asset_pools_and_options(monkeypatch, overrides, message):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {"content_type": "image/png"})
    monkeypatch.setattr(ads, "_asset_url", lambda *_args: "https://hooktrack.example/41.png")
    values = {
        "media_asset_id": None,
        "post_id": None,
        "headline": "",
        "body": "",
        "link_url": "",
        "meta_creative_format": "dynamic",
        "meta_dynamic_media_asset_ids": [41],
        "meta_dynamic_link_urls": ["https://example.com"],
        "meta_dynamic_call_to_action_types": ["LEARN_MORE"],
    }
    values.update(overrides)

    with pytest.raises(HTTPException, match=message):
        ads._campaign_request(7, _campaign_values(**values))


def test_meta_dynamic_creative_limits_text_option_count():
    with pytest.raises(ValidationError):
        _campaign_values(
            meta_creative_format="dynamic",
            meta_dynamic_media_asset_ids=[41],
            meta_dynamic_call_to_action_types=["LEARN_MORE"],
            meta_dynamic_titles=["Headline"] * 6,
        )


def test_non_dynamic_campaign_still_requires_primary_text_headline_and_destination():
    with pytest.raises(ValidationError, match="Enter primary text"):
        _campaign_values(body="")
    with pytest.raises(ValidationError, match="Enter a campaign headline"):
        _campaign_values(headline="")
    with pytest.raises(ValidationError, match="Enter a landing page URL"):
        _campaign_values(link_url="")


def test_meta_dynamic_creative_validates_workspace_asset_type_and_lead_form_links(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {"content_type": "video/mp4"})
    with pytest.raises(HTTPException, match="require supported uploaded image"):
        ads._campaign_request(7, _campaign_values(
            media_asset_id=None,
            post_id=None,
            headline="",
            body="",
            link_url="",
            meta_creative_format="dynamic",
            meta_dynamic_media_asset_ids=[41],
            meta_dynamic_call_to_action_types=["LEARN_MORE"],
            meta_dynamic_link_urls=["https://example.com"],
        ))

    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {"content_type": "image/png"})
    monkeypatch.setattr(ads, "_asset_url", lambda *_args: "https://hooktrack.example/41.png")
    request, _ = ads._campaign_request(7, _campaign_values(
        objective="lead_generation",
        media_asset_id=None,
        post_id=None,
        headline="",
        body="",
        link_url="",
        meta_creative_format="dynamic",
        meta_lead_gen_form_id="form-123",
        meta_dynamic_media_asset_ids=[41],
        meta_dynamic_call_to_action_types=["SIGN_UP"],
    ))
    assert "linkUrls" not in request["dynamicCreative"]


def test_meta_carousel_campaign_maps_distinct_workspace_images_without_single_media(monkeypatch):
    seen = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(
        ads.db, "one",
        lambda query, params: seen.append((query, params)) or {"content_type": "image/jpeg"},
    )
    monkeypatch.setattr(ads, "_asset_url", lambda _workspace_id, asset_id: f"https://hooktrack.example/{asset_id}.jpg")
    values = _campaign_values(
        meta_creative_format="carousel",
        media_asset_id=None,
        post_id=None,
        meta_carousel_cards=[
            {"media_asset_id": 41, "headline": "First", "link_url": "https://example.com/first"},
            {"media_asset_id": 42, "description": "Second card"},
        ],
    )

    request, _ = ads._campaign_request(7, values)

    assert "imageUrl" not in request
    assert "video" not in request
    assert request["carouselCards"] == [
        {"imageUrl": "https://hooktrack.example/41.jpg", "headline": "First",
         "linkUrl": "https://example.com/first"},
        {"imageUrl": "https://hooktrack.example/42.jpg", "description": "Second card"},
    ]
    assert [params for _query, params in seen] == [(41, 7), (42, 7)]


@pytest.mark.parametrize(
    ("cards", "message"),
    [
        ([{"media_asset_id": 41}], "between 2 and 10"),
        ([{"media_asset_id": 41}, {"media_asset_id": 41}], "different image"),
    ],
)
def test_meta_carousel_validates_card_count_and_duplicate_assets(monkeypatch, cards, message):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})

    with pytest.raises(HTTPException, match=message):
        ads._campaign_request(7, _campaign_values(
            meta_creative_format="carousel",
            media_asset_id=None,
            post_id=None,
            meta_carousel_cards=cards,
        ))


def test_meta_carousel_rejects_non_image_and_cross_workspace_assets(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: None)
    with pytest.raises(HTTPException, match="not found in this workspace"):
        ads._campaign_request(7, _campaign_values(
            meta_creative_format="carousel",
            media_asset_id=None,
            post_id=None,
            meta_carousel_cards=[{"media_asset_id": 41}, {"media_asset_id": 42}],
        ))

    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {"content_type": "video/mp4"})
    with pytest.raises(HTTPException, match="JPEG or PNG"):
        ads._campaign_request(7, _campaign_values(
            meta_creative_format="carousel",
            media_asset_id=None,
            post_id=None,
            meta_carousel_cards=[{"media_asset_id": 41}, {"media_asset_id": 42}],
        ))


def test_non_meta_campaign_rejects_meta_carousel_fields(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "tiktok-connection"})

    with pytest.raises(HTTPException, match="only for Meta campaigns"):
        ads._campaign_request(7, _campaign_values(
            platform="tiktok",
            ad_connection_id="tiktok-connection",
            ad_account_id="advertiser-1",
            creative_type="video",
            media_asset_id=41,
            meta_creative_format="carousel",
            meta_carousel_cards=[{"media_asset_id": 42}, {"media_asset_id": 43}],
        ))


def test_meta_placement_assets_build_image_payload_scoped_to_workspace_assets(monkeypatch):
    seen = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(
        ads.db, "one",
        lambda query, params: seen.append((query, params)) or {"content_type": "image/png"},
    )
    monkeypatch.setattr(ads, "_asset_url", lambda _workspace_id, asset_id: f"https://hooktrack.example/{asset_id}.png")
    values = _campaign_values(
        media_asset_id=None,
        post_id=None,
        meta_creative_format="placement_assets",
        meta_placement_asset_type="image",
        meta_default_media_asset_id=41,
        meta_placement_rules=[
            {
                "media_asset_id": 42,
                "publisher_platforms": ["instagram"],
                "instagram_positions": ["story", "reels"],
                "headline": "Vertical headline",
                "body": "Vertical copy",
            },
        ],
    )

    request, _ = ads._campaign_request(7, values)

    assert "imageUrl" not in request
    assert "video" not in request
    assert request["placementAssets"] == {
        "defaultImageUrl": "https://hooktrack.example/41.png",
        "rules": [{
            "imageUrl": "https://hooktrack.example/42.png",
            "placements": {
                "publisherPlatforms": ["instagram"],
                "instagramPositions": ["story", "reels"],
            },
            "headline": "Vertical headline",
            "body": "Vertical copy",
        }],
    }
    assert [params for _query, params in seen] == [(41, 7), (42, 7)]


def test_meta_placement_assets_build_video_payload_and_validate_consistent_types(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {"content_type": "video/mp4"})
    monkeypatch.setattr(ads, "_asset_url", lambda _workspace_id, asset_id: f"https://hooktrack.example/{asset_id}.mp4")
    values = _campaign_values(
        creative_type="video",
        media_asset_id=None,
        post_id=None,
        meta_placement_asset_type="video",
        meta_creative_format="placement_assets",
        meta_default_media_asset_id=41,
        meta_placement_rules=[{
            "media_asset_id": 42,
            "publisher_platforms": ["facebook"],
            "facebook_positions": ["story"],
        }],
    )

    request, _ = ads._campaign_request(7, values)

    assert request["placementAssets"] == {
        "defaultVideoUrl": "https://hooktrack.example/41.mp4",
        "rules": [{
            "videoUrl": "https://hooktrack.example/42.mp4",
            "placements": {
                "publisherPlatforms": ["facebook"],
                "facebookPositions": ["story"],
            },
        }],
    }

    monkeypatch.setattr(ads.db, "one", lambda query, params: {
        "content_type": "image/png" if params[0] == 41 else "video/mp4",
    })
    with pytest.raises(HTTPException, match="All Meta placement assets must be supported image"):
        ads._campaign_request(7, _campaign_values(
            media_asset_id=None,
            post_id=None,
            meta_creative_format="placement_assets",
            meta_placement_asset_type="image",
            meta_default_media_asset_id=41,
            meta_placement_rules=[{
                "media_asset_id": 42,
                "publisher_platforms": ["facebook"],
                "facebook_positions": ["feed"],
            }],
        ))


@pytest.mark.parametrize(
    ("rule", "message"),
    [
        ({"media_asset_id": 42}, "at least one publisher platform or position"),
        ({"media_asset_id": 42, "instagram_positions": ["story"]}, "Add instagram to publisher platforms"),
    ],
)
def test_meta_placement_assets_require_valid_platform_selection(monkeypatch, rule, message):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    with pytest.raises(HTTPException, match=message):
        ads._campaign_request(7, _campaign_values(
            media_asset_id=None,
            post_id=None,
            meta_creative_format="placement_assets",
            meta_default_media_asset_id=41,
            meta_placement_rules=[rule],
        ))


def test_meta_placement_assets_require_matching_creative_media_mode(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})

    with pytest.raises(HTTPException, match="creative type must match"):
        ads._campaign_request(7, _campaign_values(
            creative_type="image",
            media_asset_id=None,
            post_id=None,
            meta_creative_format="placement_assets",
            meta_placement_asset_type="video",
            meta_default_media_asset_id=41,
            meta_placement_rules=[{
                "media_asset_id": 42,
                "publisher_platforms": ["facebook"],
                "facebook_positions": ["feed"],
            }],
        ))


def test_meta_campaign_maps_behavior_geo_and_custom_audience_targeting(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {"content_type": "image/png"})
    monkeypatch.setattr(ads, "_asset_url", lambda *_args: "https://hooktrack.example/ad.png")
    values = _campaign_values(
        countries=[],
        behaviors=[{"id": "behavior-1", "name": "Frequent travelers"}],
        cities=[{"key": "city-1", "radius": 17, "distance_unit": "kilometer"}],
        regions=[{"key": "region-1"}],
        zips=[{"key": "US:94304"}],
        metros=[{"key": "DMA:807"}],
        audience_include=["platform-audience-1"],
        audience_exclude=["platform-audience-2"],
    )

    request, _ = ads._campaign_request(7, values)

    assert "countries" not in request
    assert request["behaviors"] == [{"id": "behavior-1", "name": "Frequent travelers"}]
    assert request["cities"] == [{"key": "city-1", "radius": 17, "distance_unit": "kilometer"}]
    assert request["regions"] == [{"key": "region-1"}]
    assert request["zips"] == [{"key": "US:94304"}]
    assert request["metros"] == [{"key": "DMA:807"}]
    assert request["audienceInclude"] == ["platform-audience-1"]
    assert request["audienceExclude"] == ["platform-audience-2"]


def test_meta_campaign_allows_meta_minimum_age_of_thirteen(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {"content_type": "image/png"})
    monkeypatch.setattr(ads, "_asset_url", lambda *_args: "https://hooktrack.example/ad.png")

    request, _ = ads._campaign_request(7, _campaign_values(age_min=13, age_max=18))

    assert request["ageMin"] == 13
    assert request["ageMax"] == 18


def test_non_meta_campaign_rejects_meta_audience_targeting(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "tiktok-connection"})
    with pytest.raises(HTTPException, match="available only for Meta"):
        ads._campaign_request(7, _campaign_values(
            platform="tiktok", ad_connection_id="tiktok-connection", ad_account_id="advertiser-1",
            objective="traffic", creative_type="video", behaviors=[{"id": "1", "name": "Behavior"}],
        ))


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


def _messaging_call_values(**overrides):
    values = {
        "ad_connection_id": "meta-connection",
        "ad_account_id": "act-1",
        "ad_type": "messaging",
        "destination": "messenger",
        "campaign_name": "Spring conversations",
        "headline": "Talk to our team",
        "body": "Get help choosing the right product.",
        "description": "Chat with us",
        "media_asset_id": 41,
        "daily_budget": Decimal("25.00"),
        "countries": ["US", "CA"],
        "link_url": "",
        "phone_number": "",
        "whatsapp_phone_number": "",
        "welcome_text": "",
        "welcome_prefill_text": "",
    }
    values.update(overrides)
    return ads.MetaMessagingCallAdIn(**values).model_dump(mode="json")


def test_meta_messaging_payload_is_account_scoped_and_paused(monkeypatch):
    asset_queries = []
    monkeypatch.setattr(ads.db, "one", lambda query, params: (
        asset_queries.append((query, params)) or {"content_type": "image/png"}
    ))
    monkeypatch.setattr(ads, "_asset_url", lambda workspace_id, asset_id:
                        f"https://hooktrack.example/{workspace_id}/{asset_id}.png")

    path, payload = ads._messaging_call_payload(7, _messaging_call_values(
        destination="whatsapp",
        whatsapp_phone_number="+15551234567",
        welcome_text="How can we help?",
        welcome_prefill_text="I have a question",
    ), "meta-connection")

    assert path == "/ads/messaging"
    assert payload == {
        "accountId": "meta-connection",
        "adAccountId": "act-1",
        "name": "Spring conversations",
        "headline": "Talk to our team",
        "body": "Get help choosing the right product.",
        "budgetAmount": 25.0,
        "budgetType": "daily",
        "status": "PAUSED",
        "campaignStatus": "PAUSED",
        "countries": ["US", "CA"],
        "imageUrl": "https://hooktrack.example/7/41.png",
        "description": "Chat with us",
        "destination": "whatsapp",
        "whatsappPhoneNumber": "+15551234567",
        "welcomeMessage": {"text": "How can we help?", "prefillText": "I have a question"},
    }
    assert asset_queries[0][1] == (41, 7)


def test_meta_call_payload_uses_video_and_requires_https_destination(monkeypatch):
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {"content_type": "video/mp4"})
    monkeypatch.setattr(ads, "_asset_url", lambda *_args: "https://hooktrack.example/ad.mp4")

    path, payload = ads._messaging_call_payload(7, _messaging_call_values(
        ad_type="call",
        phone_number="+15551234567",
        link_url="https://example.com/contact",
        welcome_text="ignored when not provided",
        welcome_prefill_text="",
    ), "meta-connection")

    assert path == "/ads/call"
    assert payload["video"] == {"url": "https://hooktrack.example/ad.mp4"}
    assert payload["phoneNumber"] == "+15551234567"
    assert payload["linkUrl"] == "https://example.com/contact"
    assert payload["status"] == payload["campaignStatus"] == "PAUSED"
    assert "destination" not in payload


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"countries": ["U"]}, "ISO two-letter"),
        ({"countries": ["US", "US"]}, "only once"),
        ({"destination": "messenger", "whatsapp_phone_number": "+15551234567"}, "only be set for WhatsApp"),
        ({"destination": "whatsapp", "whatsapp_phone_number": "5551234567"}, "E.164"),
        ({"welcome_text": "Welcome"}, "both welcome message text and prefilled reply"),
        ({"ad_type": "call", "phone_number": "5551234567", "link_url": "tel:+15551234567"}, "E.164"),
        ({"ad_type": "call", "phone_number": "+15551234567", "link_url": "http://example.com"}, "HTTPS"),
    ],
)
def test_meta_messaging_and_call_payload_rejects_invalid_fields(monkeypatch, overrides, message):
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {"content_type": "image/jpeg"})
    monkeypatch.setattr(ads, "_asset_url", lambda *_args: "https://hooktrack.example/ad.jpg")

    with pytest.raises(HTTPException, match=message):
        ads._messaging_call_payload(7, _messaging_call_values(**overrides), "meta-connection")


def test_meta_messaging_call_route_uses_selected_account_and_surfaces_provider_error(monkeypatch):
    selected = []
    requests = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *args: selected.append(args) or {"id": "meta-connection"})
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: {"content_type": "image/png"})
    monkeypatch.setattr(ads, "_asset_url", lambda *_args: "https://hooktrack.example/ad.png")

    def fail_provider(method, path, **kwargs):
        requests.append((method, path, kwargs))
        raise latehub.LateHubError("Meta rejected the ad", 422)

    monkeypatch.setattr(ads.latehub, "meta_ads_request", fail_provider)
    request = ads.MetaMessagingCallAdIn(**_messaging_call_values())

    with pytest.raises(HTTPException) as error:
        ads.create_meta_messaging_call_ad(request, _ctx())

    assert error.value.status_code == 422
    assert error.value.detail == "Meta rejected the ad"
    assert selected == [(7, "meta-connection", "act-1", "meta")]
    assert requests[0][0:2] == ("POST", "/ads/messaging")
    assert requests[0][2]["body"]["status"] == requests[0][2]["body"]["campaignStatus"] == "PAUSED"


def _meta_rf_buy_request(**overrides):
    values = {
        "ad_connection_id": "meta-connection",
        "ad_account_id": "act-1",
        "workflow_id": "a" * 32,
        "prediction_id": "6234567890456",
        "campaign_name": "Spring reserved campaign",
        "headline": "The new collection",
        "body": "See what's new.",
        "link_url": "https://example.com/spring",
        "call_to_action": "LEARN_MORE",
        "media_asset_id": 41,
        "countries": ["US"],
        "age_min": 18,
        "age_max": 65,
        "placement": "both",
    }
    values.update(overrides)
    return ads.MetaRfBuyIn(**values)


def test_meta_reach_frequency_buy_uses_selected_account_signed_asset_and_reserved_shape(monkeypatch):
    selected = []
    asset_queries = []
    calls = []
    db_updates = []
    def one(query, params):
        if "meta_reach_frequency_workflows" in query:
            return {
                "workflow_id": "a" * 32,
                "ad_connection_id": "meta-connection",
                "ad_account_id": "act-1",
                "status": "reserved",
                "reserved_id": "6234567890456",
                "config_json": {
                    "targeting": {"countries": ["US"], "ageMin": 18, "ageMax": 65},
                    "placements": {
                        "publisherPlatforms": ["facebook", "instagram"],
                        "devicePlatforms": ["mobile", "desktop"],
                        "facebookPositions": ["feed"],
                        "instagramPositions": ["stream"],
                    },
                },
            }
        asset_queries.append((query, params))
        return {"content_type": "image/png"}
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *args: selected.append(args) or {"id": "meta-connection"})
    monkeypatch.setattr(ads.db, "one", one)
    monkeypatch.setattr(ads.db, "execute", lambda query, params: db_updates.append((query, params)) or 1)
    monkeypatch.setattr(ads, "_asset_url", lambda workspace_id, asset_id:
                        f"https://hooktrack.example/signed/{workspace_id}/{asset_id}")
    monkeypatch.setattr(
        ads.latehub, "meta_ads_request",
        lambda method, path, **kwargs: calls.append((method, path, kwargs)) or {"ad": {"status": "pending_review"}},
    )

    result = ads.buy_meta_reach_frequency_campaign(_meta_rf_buy_request(), _ctx())

    assert result == {"ad": {"status": "pending_review"}}
    assert selected == [(7, "meta-connection", "act-1", "meta")]
    assert asset_queries[0][1] == (41, 7)
    assert len(db_updates) == 2
    assert "'buy_pending'" in db_updates[0][0]
    assert "'bought'" in db_updates[1][0]
    assert calls == [("POST", "/ads/create", {"body": {
        "accountId": "meta-connection",
        "adAccountId": "act-1",
        "name": "Spring reserved campaign",
        "goal": "awareness",
        "buyingType": "RESERVED",
        "rfPredictionId": "6234567890456",
        "headline": "The new collection",
        "body": "See what's new.",
        "callToAction": "LEARN_MORE",
        "linkUrl": "https://example.com/spring",
        "countries": ["US"],
        "ageMin": 18,
        "ageMax": 65,
        "placements": {
            "publisherPlatforms": ["facebook", "instagram"],
            "devicePlatforms": ["mobile", "desktop"],
            "facebookPositions": ["feed"],
            "instagramPositions": ["stream"],
        },
        "imageUrl": "https://hooktrack.example/signed/7/41",
    }})]


@pytest.mark.parametrize(
    ("overrides", "content_type", "message"),
    [
        ({"link_url": "http://example.com"}, "image/png", "complete HTTPS"),
        ({"countries": ["US", "US"]}, "image/png", "only once"),
        ({"countries": ["USA"]}, "image/png", "ISO two-letter"),
        ({"age_min": 50, "age_max": 25}, "image/png", "Maximum age"),
        ({}, "application/pdf", "require an uploaded"),
    ],
)
def test_meta_reach_frequency_buy_rejects_invalid_input(monkeypatch, overrides, content_type, message):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    def one(query, _params):
        if "meta_reach_frequency_workflows" in query:
            return {
                "workflow_id": "a" * 32,
                "ad_connection_id": "meta-connection",
                "ad_account_id": "act-1",
                "status": "reserved",
                "reserved_id": "6234567890456",
                "config_json": {
                    "targeting": {"countries": ["US"], "ageMin": 18, "ageMax": 65},
                    "placements": {
                        "publisherPlatforms": ["facebook", "instagram"],
                        "devicePlatforms": ["mobile", "desktop"],
                        "facebookPositions": ["feed"],
                        "instagramPositions": ["stream"],
                    },
                },
            }
        return {"content_type": content_type}
    monkeypatch.setattr(ads.db, "one", one)
    monkeypatch.setattr(ads, "_asset_url", lambda *_args: "https://hooktrack.example/ad.png")

    with pytest.raises(HTTPException, match=message):
        ads.buy_meta_reach_frequency_campaign(_meta_rf_buy_request(**overrides), _ctx())


def test_meta_reach_frequency_buy_surfaces_provider_error(monkeypatch):
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    monkeypatch.setattr(ads.db, "one", lambda query, _params: {
        "workflow_id": "a" * 32,
        "ad_connection_id": "meta-connection",
        "ad_account_id": "act-1",
        "status": "reserved",
        "reserved_id": "6234567890456",
        "config_json": {
            "targeting": {"countries": ["US"], "ageMin": 18, "ageMax": 65},
            "placements": {
                "publisherPlatforms": ["facebook", "instagram"],
                "devicePlatforms": ["mobile", "desktop"],
                "facebookPositions": ["feed"],
                "instagramPositions": ["stream"],
            },
        },
    } if "meta_reach_frequency_workflows" in query else {"content_type": "video/mp4"})
    monkeypatch.setattr(ads, "_asset_url", lambda *_args: "https://hooktrack.example/ad.mp4")
    updates = []
    monkeypatch.setattr(ads.db, "execute", lambda query, params: updates.append((query, params)) or 1)

    def fail_provider(*_args, **_kwargs):
        raise latehub.LateHubError("Reservation expired", 409)

    monkeypatch.setattr(ads.latehub, "meta_ads_request", fail_provider)
    with pytest.raises(HTTPException) as error:
        ads.buy_meta_reach_frequency_campaign(_meta_rf_buy_request(), _ctx())

    assert error.value.status_code == 409
    assert error.value.detail == "Reservation expired"
    assert len(updates) == 2
    assert updates[1][1][0] == "unknown"


def test_meta_reach_frequency_quote_is_persisted_for_selected_account(monkeypatch):
    seen = []
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *args: seen.append(args) or {"id": "meta-connection"})
    monkeypatch.setattr(ads.secrets, "token_hex", lambda _length: "b" * 32)
    monkeypatch.setattr(
        ads.db, "one",
        lambda query, params: seen.append((query, params)) or {"workflow_id": "b" * 32, "status": "quote_ready"},
    )
    body = ads.MetaRfWorkflowCreateIn(
        ad_connection_id="meta-connection", ad_account_id="act-1",
        quote_id="quote-1", config={"budgetAmount": 500},
        quote={"predictionId": "quote-1", "status": "ready"},
    )

    assert ads.create_meta_reach_frequency_workflow(body, _ctx()) == {
        "workflow_id": "b" * 32, "status": "quote_ready",
    }
    assert seen[0] == (7, "meta-connection", "act-1", "meta")
    assert "INSERT INTO meta_reach_frequency_workflows" in seen[1][0]
    assert seen[1][1][:5] == ("b" * 32, 7, "meta-connection", "act-1", "quote-1")


def test_meta_reach_frequency_reserve_saves_provider_reservation_id(monkeypatch):
    saved = {
        "workflow_id": "c" * 32,
        "ad_connection_id": "meta-connection",
        "ad_account_id": "act-1",
        "status": "quote_ready",
        "quote_id": "quote-1",
        "quote_json": {"status": "ready"},
    }
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: saved)
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    updates = []
    monkeypatch.setattr(ads.db, "execute", lambda query, params: updates.append((query, params)) or 1)
    calls = []
    monkeypatch.setattr(
        ads.latehub, "meta_ads_request",
        lambda method, path, **kwargs: calls.append((method, path, kwargs)) or {
            "prediction": {"predictionId": "reserved-1", "status": "ready"},
        },
    )

    result = ads.reserve_meta_reach_frequency_workflow("c" * 32, _ctx())

    assert result == {
        "workflow_id": "c" * 32, "status": "reserved",
        "prediction": {"predictionId": "reserved-1", "status": "ready"},
    }
    assert calls == [("POST", "/ads/rf-predictions/quote-1/reserve", {
        "body": {"accountId": "meta-connection", "adAccountId": "act-1"},
    })]
    assert len(updates) == 2
    assert "'reserve_pending'" in updates[0][0]
    assert "'reserved'" in updates[1][0]
    assert updates[1][1][0] == "reserved-1"


def test_meta_reach_frequency_release_uncertain_result_is_not_retried(monkeypatch):
    saved = {
        "workflow_id": "d" * 32,
        "ad_connection_id": "meta-connection",
        "ad_account_id": "act-1",
        "status": "reserved",
        "reserved_id": "reserved-1",
    }
    monkeypatch.setattr(ads.db, "one", lambda *_args, **_kwargs: saved)
    monkeypatch.setattr(ads, "_selected_ad_account", lambda *_args: {"id": "meta-connection"})
    updates = []
    monkeypatch.setattr(ads.db, "execute", lambda query, params: updates.append((query, params)) or 1)

    def fail_provider(*_args, **_kwargs):
        raise latehub.LateHubError("Timed out", None)

    monkeypatch.setattr(ads.latehub, "meta_ads_request", fail_provider)
    with pytest.raises(HTTPException) as error:
        ads.release_meta_reach_frequency_workflow("d" * 32, _ctx())

    assert error.value.status_code == 502
    assert len(updates) == 2
    assert "'release_pending'" in updates[0][0]
    assert updates[1][1][0] == "unknown"
