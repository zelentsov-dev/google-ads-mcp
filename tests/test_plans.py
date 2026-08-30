from __future__ import annotations

from typing import Any, cast

import pytest
from google.ads.googleads.client import GoogleAdsClient
from google.auth.credentials import AnonymousCredentials

from google_ads_mcp.errors import NotSupportedError, ValidationError
from google_ads_mcp.plans import (
    PlanBuilder,
    _ad_group_state,
    _campaign_state,
    _find_integer,
    _find_parent_campaign_budget,
    _https_urls,
    _positive_keyword_criterion,
    _query_items,
    _require_paused_parent,
    _targeting_rows,
    state_queries,
)
from google_ads_mcp.write_adapter import GoogleAdsWriteAdapter
from google_ads_mcp.write_models import WriteKind, WritePlan

CUSTOMER = "1234567890"
ASSET = f"customers/{CUSTOMER}/assets/1"


def _payloads() -> dict[str, dict[str, Any]]:
    return {
        "search_campaign_create": {
            "name": "Search",
            "dailyBudgetMicros": "1000000",
            "adGroupName": "Group",
            "cpcBidMicros": "100000",
            "keywords": ["one"],
            "keywordMatchType": "PHRASE",
            "finalUrl": "https://example.com",
            "headlines": ["One", "Two", "Three"],
            "descriptions": ["Description one", "Description two"],
            "containsEuPoliticalAdvertising": False,
        },
        "performance_max_campaign_create": {
            "name": "PMax",
            "dailyBudgetMicros": "1000000",
            "assetGroupName": "Group",
            "finalUrl": "https://example.com",
            "businessName": "Business",
            "headlines": ["One", "Two", "Three"],
            "longHeadlines": ["A long headline"],
            "descriptions": ["Description one", "Description two"],
            "landscapeImageAssets": [ASSET],
            "squareImageAssets": [ASSET],
            "logoAssets": [ASSET],
            "youtubeVideoAssets": [],
            "containsEuPoliticalAdvertising": True,
        },
        "app_campaign_create": {
            "name": "App",
            "dailyBudgetMicros": "1000000",
            "targetCpaMicros": "100000",
            "appId": "com.example.app",
            "appStore": "APPLE_APP_STORE",
            "adGroupName": "Group",
            "adName": "Ad",
            "headlines": ["One", "Two"],
            "descriptions": ["Description"],
            "imageAssets": [ASSET],
            "youtubeVideoAssets": [],
            "containsEuPoliticalAdvertising": False,
        },
        "campaign_update": {"campaignId": "1", "name": "New name"},
        "campaign_targeting": {
            "campaignId": "1",
            "geoTargetIds": ["2840"],
            "languageIds": ["1000"],
        },
        "campaign_status": {"campaignId": "1", "status": "PAUSED"},
        "campaign_budget": {"budgetId": "1", "dailyBudgetMicros": "1000000"},
        "campaign_bidding": {
            "campaignId": "1",
            "strategy": "TARGET_CPA",
            "targetCpaMicros": "100000",
        },
        "ad_group_create": {"campaignId": "1", "name": "Group", "cpcBidMicros": "100000"},
        "ad_group_update": {
            "adGroupId": "1",
            "name": "New group",
            "status": "PAUSED",
            "cpcBidMicros": "100000",
        },
        "keyword_create": {
            "level": "AD_GROUP",
            "adGroupId": "1",
            "text": "keyword",
            "matchType": "EXACT",
            "cpcBidMicros": "100000",
        },
        "negative_keyword_create": {
            "level": "CAMPAIGN",
            "campaignId": "1",
            "text": "negative",
            "matchType": "EXACT",
        },
        "criterion_update": {
            "level": "AD_GROUP",
            "adGroupId": "1",
            "criterionId": "2",
            "status": "PAUSED",
            "cpcBidMicros": "100000",
        },
        "asset_create": {"name": "Asset", "assetType": "TEXT", "text": "Asset text"},
        "asset_link": {
            "ownerType": "CAMPAIGN",
            "ownerId": "1",
            "assetResourceName": ASSET,
            "fieldType": "HEADLINE",
        },
        "asset_group_create": {
            "campaignId": "1",
            "name": "Group",
            "finalUrl": "https://example.com",
            "assetResourceNames": [ASSET],
            "fieldTypes": ["HEADLINE"],
        },
        "ad_create": {
            "adGroupId": "1",
            "name": "Ad",
            "adType": "RESPONSIVE_SEARCH_AD",
            "finalUrl": "https://example.com",
            "headlines": ["One", "Two", "Three"],
            "descriptions": ["Description one", "Description two"],
            "imageAssets": [],
            "youtubeVideoAssets": [],
        },
        "recommendation_apply": {
            "recommendationResourceName": f"customers/{CUSTOMER}/recommendations/1"
        },
        "recommendation_dismiss": {
            "recommendationResourceName": f"customers/{CUSTOMER}/recommendations/1"
        },
    }


def _targeting_before() -> dict[str, Any]:
    budget_resource = f"customers/{CUSTOMER}/campaignBudgets/1"
    return {
        "objects": [
            {
                "items": [
                    {
                        "campaign": {
                            "id": "1",
                            "status": "PAUSED",
                            "advertising_channel_type": "SEARCH",
                            "campaign_budget": budget_resource,
                            "geo_target_type_setting": {
                                "positive_geo_target_type": "PRESENCE_OR_INTEREST",
                                "negative_geo_target_type": "PRESENCE",
                            },
                        },
                        "campaign_budget": {
                            "resource_name": budget_resource,
                            "amount_micros": "1000000",
                        },
                    }
                ]
            },
            {"items": []},
            {
                "items": [
                    {
                        "geo_target_constant": {
                            "id": "2840",
                            "status": "ENABLED",
                        }
                    }
                ]
            },
            {
                "items": [
                    {
                        "language_constant": {
                            "id": "1000",
                            "targetable": True,
                        }
                    }
                ]
            },
        ]
    }


def _ready_search_before() -> dict[str, Any]:
    budget_resource = f"customers/{CUSTOMER}/campaignBudgets/1"
    return {
        "objects": [
            {
                "items": [
                    {
                        "campaign": {
                            "id": "1",
                            "status": "PAUSED",
                            "advertising_channel_type": "SEARCH",
                            "campaign_budget": budget_resource,
                            "bidding_strategy_type": "MANUAL_CPC",
                            "manual_cpc": {"enhanced_cpc_enabled": False},
                            "geo_target_type_setting": {
                                "positive_geo_target_type": "PRESENCE",
                                "negative_geo_target_type": "PRESENCE",
                            },
                        },
                        "campaign_budget": {
                            "resource_name": budget_resource,
                            "amount_micros": "1000000",
                        },
                    }
                ]
            },
            {
                "items": [
                    {
                        "campaign_criterion": {
                            "criterion_id": "2840",
                            "resource_name": (
                                f"customers/{CUSTOMER}/campaignCriteria/1~2840"
                            ),
                            "type": "LOCATION",
                            "status": "ENABLED",
                            "negative": False,
                            "location": {
                                "geo_target_constant": "geoTargetConstants/2840"
                            },
                        }
                    },
                    {
                        "campaign_criterion": {
                            "criterion_id": "1000",
                            "resource_name": (
                                f"customers/{CUSTOMER}/campaignCriteria/1~1000"
                            ),
                            "type": "LANGUAGE",
                            "status": "ENABLED",
                            "negative": False,
                            "language": {
                                "language_constant": "languageConstants/1000"
                            },
                        }
                    },
                ]
            },
            {
                "items": [
                    {
                        "campaign": {"id": "1"},
                        "ad_group": {
                            "id": "10",
                            "status": "ENABLED",
                            "cpc_bid_micros": "250000",
                        }
                    }
                ]
            },
            {
                "items": [
                    {
                        "campaign": {"id": "1"},
                        "ad_group": {"id": "10", "status": "ENABLED"},
                        "ad_group_criterion": {
                            "criterion_id": "20",
                            "status": "ENABLED",
                            "negative": False,
                            "type": "KEYWORD",
                            "effective_cpc_bid_micros": "250000",
                            "effective_cpc_bid_source": "AD_GROUP",
                            "keyword": {
                                "text": "voice transcription app",
                                "match_type": "EXACT",
                            },
                        },
                    }
                ]
            },
            {
                "items": [
                    {
                        "campaign": {"id": "1"},
                        "ad_group": {"id": "10", "status": "ENABLED"},
                        "ad_group_ad": {
                            "status": "ENABLED",
                            "ad": {
                                "id": "30",
                                "type": "RESPONSIVE_SEARCH_AD",
                                "final_urls": ["https://example.com/landing"],
                            },
                            "policy_summary": {
                                "approval_status": "APPROVED",
                                "review_status": "REVIEWED",
                            },
                        },
                    }
                ]
            },
        ]
    }


def _campaign_bidding_before(*, status: str = "PAUSED") -> dict[str, Any]:
    budget_resource = f"customers/{CUSTOMER}/campaignBudgets/1"
    return {
        "objects": [
            {
                "items": [
                    {
                        "campaign": {
                            "id": "1",
                            "status": status,
                            "campaign_budget": budget_resource,
                        },
                        "campaign_budget": {
                            "resource_name": budget_resource,
                            "amount_micros": "1234",
                        },
                    }
                ]
            }
        ]
    }


def _criterion_update_before(*, parent_status: str = "PAUSED") -> dict[str, Any]:
    budget_resource = f"customers/{CUSTOMER}/campaignBudgets/1"
    return {
        "objects": [
            {
                "items": [
                    {
                        "ad_group_criterion": {
                            "criterion_id": "2",
                            "status": "PAUSED",
                            "negative": False,
                            "type": "KEYWORD",
                            "keyword": {"text": "keyword", "match_type": "EXACT"},
                            "effective_cpc_bid_micros": "100000",
                            "effective_cpc_bid_source": "AD_GROUP_CRITERION",
                        }
                    }
                ]
            },
            {
                "items": [
                    {
                        "ad_group": {"id": "1"},
                        "campaign": {
                            "id": "1",
                            "status": parent_status,
                            "campaign_budget": budget_resource,
                        },
                    }
                ]
            },
            {
                "items": [
                    {
                        "campaign": {
                            "id": "1",
                            "campaign_budget": budget_resource,
                        },
                        "campaign_budget": {
                            "resource_name": budget_resource,
                            "amount_micros": "1234",
                        },
                    }
                ]
            },
        ]
    }


def _ad_group_update_before(*, current_bid: object = "100000") -> dict[str, Any]:
    before = _criterion_update_before()
    before["objects"][0]["items"] = [
        {
            "ad_group": {
                "id": "1",
                "status": "PAUSED",
                "cpc_bid_micros": current_bid,
            }
        }
    ]
    return before


def _before_for(kind: str) -> dict[str, Any]:
    if kind == "campaign_targeting":
        return _targeting_before()
    if kind == "campaign_bidding":
        return _campaign_bidding_before()
    if kind == "criterion_update":
        return _criterion_update_before()
    return {"objects": []}


@pytest.mark.parametrize(
    "before",
    [
        {"objects": [{}]},
        {"objects": [{"items": ["malformed"]}]},
    ],
)
def test_query_evidence_shape_is_strict(before: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="malformed"):
        _query_items(before, 0, evidence="Synthetic")

    with pytest.raises(ValidationError, match="incomplete"):
        _query_items({"objects": []}, 0, evidence="Synthetic")


def test_authoritative_state_helpers_reject_every_incomplete_shape() -> None:
    campaign_cases = [
        {"objects": [{"items": [{}]}]},
        {
            "objects": [
                {
                    "items": [
                        {
                            "campaign": {"id": "2", "status": "PAUSED"},
                            "campaign_budget": {},
                        }
                    ]
                }
            ]
        },
        {
            "objects": [
                {
                    "items": [
                        {
                            "campaign": {
                                "id": "1",
                                "status": "PAUSED",
                                "campaign_budget": "customers/1/campaignBudgets/1",
                            },
                            "campaign_budget": {
                                "resource_name": "customers/1/campaignBudgets/2",
                                "amount_micros": "1",
                            },
                        }
                    ]
                }
            ]
        },
    ]
    for before in campaign_cases:
        with pytest.raises(ValidationError, match="resolve exactly once"):
            _campaign_state(before, "1")
    with pytest.raises(ValidationError, match="advertising channel"):
        _campaign_state(_campaign_bidding_before(), "1", require_channel=True)

    malformed_targeting = [
        {"campaign_criterion": "invalid"},
        {
            "campaign_criterion": {
                "criterion_id": "1",
                "resource_name": "customers/1/campaignCriteria/1~1",
                "type": "LOCATION",
                "status": "ENABLED",
                "negative": False,
            }
        },
        {
            "campaign_criterion": {
                "criterion_id": "1",
                "resource_name": "customers/1/campaignCriteria/1~1",
                "type": "LANGUAGE",
                "status": "ENABLED",
                "negative": False,
            }
        },
    ]
    for row in malformed_targeting:
        with pytest.raises(ValidationError, match="targeting evidence is malformed"):
            _targeting_rows({"objects": [{"items": []}, {"items": [row]}]})

    with pytest.raises(ValidationError, match="Ad group state"):
        _ad_group_state({"objects": [{"items": []}]}, "1")
    with pytest.raises(ValidationError, match="Ad group state"):
        _ad_group_state(
            {"objects": [{"items": [{"ad_group": {"id": "2", "status": "PAUSED"}}]}]},
            "1",
        )
    with pytest.raises(ValidationError, match="CPC evidence"):
        _ad_group_state(_ad_group_update_before(current_bid="invalid"), "1")
    with pytest.raises(ValidationError, match="Parent campaign state"):
        _require_paused_parent(
            {"objects": [{"items": []}, {"items": []}]}, "ad_group", "1"
        )
    with pytest.raises(ValidationError, match="Keyword criterion state"):
        _positive_keyword_criterion({"objects": [{"items": []}]}, "2")
    invalid_keyword = _criterion_update_before()
    invalid_keyword["objects"][0]["items"][0]["ad_group_criterion"]["negative"] = True
    with pytest.raises(ValidationError, match="positive keywords"):
        _positive_keyword_criterion(invalid_keyword, "2")
    with pytest.raises(ValidationError, match="duplicate final URLs"):
        _https_urls(["https://example.com", "https://example.com"])


@pytest.mark.parametrize("kind", sorted(_payloads()))
def test_all_typed_plans_round_trip(kind: str) -> None:
    payload = _payloads()[kind]
    queries = state_queries(cast(WriteKind, kind), CUSTOMER, payload)
    assert queries
    plan = PlanBuilder().build(
        cast(WriteKind, kind),
        profile="operator",
        customer_id=CUSTOMER,
        policy_id="policy",
        payload=payload,
        before=_before_for(kind),
    )
    assert 1 <= len(plan.items) <= 100
    assert plan.partial_failure is False
    assert WritePlan.from_dict(plan.as_dict()) == plan
    if kind.endswith("campaign_create"):
        assert plan.after["status"] == "PAUSED"
        campaign = next(item for item in plan.items if item.operation_type == "campaign_operation")
        assert campaign.resource["status"] == "PAUSED"
        budget = next(
            item for item in plan.items if item.operation_type == "campaign_budget_operation"
        )
        assert budget.resource["explicitly_shared"] is False
        assert "name" not in budget.resource
    if kind == "keyword_create":
        assert plan.after["bidMicros"] == payload["cpcBidMicros"]
    if kind in {"asset_link", "asset_group_create"}:
        assert plan.risk["spendAffecting"] is True


def test_campaign_targeting_is_atomic_presence_only_and_spend_bounded() -> None:
    payload = _payloads()["campaign_targeting"]
    plan = PlanBuilder().build(
        "campaign_targeting",
        profile="operator",
        customer_id=CUSTOMER,
        policy_id="policy",
        payload=payload,
        before=_targeting_before(),
    )
    assert plan.after == {
        "campaignId": "1",
        "geoTargetIds": ["2840"],
        "languageIds": ["1000"],
        "positiveGeoTargetType": "PRESENCE",
        "negativeGeoTargetType": "PRESENCE",
        "currentCampaignDailyBudgetMicros": "1000000",
    }
    assert plan.monetary_delta["spendMicros"] is None
    assert [item.correlation_id for item in plan.items] == [
        "campaign-geo-mode",
        "location-1",
        "language-1",
    ]
    assert plan.items[0].update_mask == (
        "geo_target_type_setting.positive_geo_target_type",
        "geo_target_type_setting.negative_geo_target_type",
    )
    assert plan.items[1].resource["location"] == {
        "geo_target_constant": "geoTargetConstants/2840"
    }
    assert plan.items[2].resource["language"] == {
        "language_constant": "languageConstants/1000"
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("geoTargetIds", "2840", "array"),
        ("geoTargetIds", [], "between"),
        ("geoTargetIds", ["2840", "2840"], "duplicates"),
        ("languageIds", [], "between"),
        ("languageIds", ["1000", "1000"], "duplicates"),
    ],
)
def test_campaign_targeting_rejects_invalid_id_sets(
    field: str, value: object, message: str
) -> None:
    payload = {**_payloads()["campaign_targeting"], field: value}
    with pytest.raises(ValidationError, match=message):
        state_queries("campaign_targeting", CUSTOMER, payload)


def test_campaign_targeting_requires_paused_empty_campaign_and_live_constants() -> None:
    payload = _payloads()["campaign_targeting"]
    existing = _targeting_before()
    existing["objects"][1]["items"] = [
        {
            "campaign_criterion": {
                "criterion_id": "2840",
                "resource_name": f"customers/{CUSTOMER}/campaignCriteria/1~2840",
                "type": "LOCATION",
                "status": "ENABLED",
                "negative": False,
                "location": {"geo_target_constant": "geoTargetConstants/2840"},
            }
        }
    ]
    cases: list[tuple[dict[str, Any], str]] = [(existing, "already has")]

    active = _targeting_before()
    active["objects"][0]["items"][0]["campaign"]["status"] = "ENABLED"
    cases.append((active, "PAUSED"))

    missing_geo = _targeting_before()
    missing_geo["objects"][2]["items"] = []
    cases.append((missing_geo, "geoTargetId"))

    disabled_geo = _targeting_before()
    disabled_geo["objects"][2]["items"][0]["geo_target_constant"]["status"] = "REMOVAL_PLANNED"
    cases.append((disabled_geo, "geoTargetId"))

    missing_language = _targeting_before()
    missing_language["objects"][3]["items"] = []
    cases.append((missing_language, "languageId"))

    untargetable_language = _targeting_before()
    untargetable_language["objects"][3]["items"][0]["language_constant"][
        "targetable"
    ] = False
    cases.append((untargetable_language, "languageId"))

    missing_parent = _targeting_before()
    missing_parent["objects"][0]["items"] = []
    cases.append((missing_parent, "resolve exactly once"))

    invalid_budget = _targeting_before()
    invalid_budget["objects"][0]["items"][0]["campaign_budget"]["amount_micros"] = "bad"
    cases.append((invalid_budget, "resolve exactly once"))

    zero_budget = _targeting_before()
    zero_budget["objects"][0]["items"][0]["campaign_budget"]["amount_micros"] = "0"
    cases.append((zero_budget, "resolve exactly once"))

    empty_criterion = _targeting_before()
    empty_criterion["objects"][1]["items"] = [{"campaign_criterion": {}}]
    cases.append((empty_criterion, "malformed"))

    for before, message in cases:
        with pytest.raises(ValidationError, match=message):
            PlanBuilder().build(
                "campaign_targeting",
                profile="operator",
                customer_id=CUSTOMER,
                policy_id="policy",
                payload=payload,
                before=before,
            )


def test_search_enable_readiness_accepts_only_complete_reviewed_state() -> None:
    plan = PlanBuilder().build(
        "campaign_status",
        profile="operator",
        customer_id=CUSTOMER,
        policy_id="policy",
        payload={"campaignId": "1", "status": "ENABLED"},
        before=_ready_search_before(),
    )
    assert plan.after["currentCampaignDailyBudgetMicros"] == "1000000"
    assert plan.after["bidMicros"] == "250000"
    assert plan.after["landingPageUrls"] == ["https://example.com/landing"]


def test_search_enable_readiness_fails_closed_for_each_missing_requirement() -> None:
    payload = {"campaignId": "1", "status": "ENABLED"}
    cases: list[tuple[dict[str, Any], type[Exception], str]] = []

    active = _ready_search_before()
    active["objects"][0]["items"][0]["campaign"]["status"] = "ENABLED"
    cases.append((active, ValidationError, "PAUSED"))

    pmax = _ready_search_before()
    pmax["objects"][0]["items"][0]["campaign"]["advertising_channel_type"] = (
        "PERFORMANCE_MAX"
    )
    cases.append((pmax, NotSupportedError, "Search"))

    automated_bidding = _ready_search_before()
    automated_bidding["objects"][0]["items"][0]["campaign"][
        "bidding_strategy_type"
    ] = "MAXIMIZE_CLICKS"
    cases.append((automated_bidding, NotSupportedError, "MANUAL_CPC"))

    enhanced_cpc = _ready_search_before()
    enhanced_cpc["objects"][0]["items"][0]["campaign"]["manual_cpc"][
        "enhanced_cpc_enabled"
    ] = True
    cases.append((enhanced_cpc, ValidationError, "Enhanced CPC"))

    unsafe_geo = _ready_search_before()
    unsafe_geo["objects"][0]["items"][0]["campaign"]["geo_target_type_setting"][
        "positive_geo_target_type"
    ] = "PRESENCE_OR_INTEREST"
    cases.append((unsafe_geo, ValidationError, "presence-only"))

    no_location = _ready_search_before()
    no_location["objects"][1]["items"] = no_location["objects"][1]["items"][1:]
    cases.append((no_location, ValidationError, "location and language"))

    no_language = _ready_search_before()
    no_language["objects"][1]["items"] = no_language["objects"][1]["items"][:1]
    cases.append((no_language, ValidationError, "location and language"))

    no_group = _ready_search_before()
    no_group["objects"][2]["items"] = []
    cases.append((no_group, ValidationError, "ad group"))

    malformed_group = _ready_search_before()
    malformed_group["objects"][2]["items"] = [{"campaign": {"id": "1"}}]
    cases.append((malformed_group, ValidationError, "ad group evidence is malformed"))

    duplicate_group = _ready_search_before()
    duplicate_group["objects"][2]["items"].append(
        {
            "campaign": {"id": "1"},
            "ad_group": {
                "id": "10",
                "status": "ENABLED",
                "cpc_bid_micros": "250000",
            },
        }
    )
    cases.append((duplicate_group, ValidationError, "ad group evidence contains duplicates"))

    no_keyword = _ready_search_before()
    no_keyword["objects"][3]["items"][0]["ad_group_criterion"]["negative"] = True
    cases.append((no_keyword, ValidationError, "positive keyword"))

    no_effective_bid = _ready_search_before()
    no_effective_bid["objects"][3]["items"][0]["ad_group_criterion"].pop(
        "effective_cpc_bid_micros"
    )
    cases.append((no_effective_bid, ValidationError, "effective CPC"))

    unknown_bid_source = _ready_search_before()
    unknown_bid_source["objects"][3]["items"][0]["ad_group_criterion"][
        "effective_cpc_bid_source"
    ] = "UNSPECIFIED"
    cases.append((unknown_bid_source, ValidationError, "authoritative"))

    malformed_extra_keyword = _ready_search_before()
    malformed_extra_keyword["objects"][3]["items"].append(
        {
            "campaign": {"id": "1"},
            "ad_group": {"id": "10", "status": "ENABLED"},
            "ad_group_criterion": {
                "criterion_id": "21",
                "status": "ENABLED",
                "negative": False,
                "type": "KEYWORD",
                "effective_cpc_bid_micros": "999999999",
                "effective_cpc_bid_source": "AD_GROUP_CRITERION",
                "keyword": {"match_type": "EXACT"},
            },
        }
    )
    cases.append((malformed_extra_keyword, ValidationError, "keyword evidence is malformed"))

    duplicate_keyword = _ready_search_before()
    duplicate_keyword["objects"][3]["items"].append(
        {
            "campaign": {"id": "1"},
            "ad_group": {"id": "10", "status": "ENABLED"},
            "ad_group_criterion": {
                "criterion_id": "20",
                "status": "ENABLED",
                "negative": False,
                "type": "KEYWORD",
                "effective_cpc_bid_micros": "250000",
                "effective_cpc_bid_source": "AD_GROUP",
                "keyword": {
                    "text": "voice transcription app",
                    "match_type": "EXACT",
                },
            },
        }
    )
    cases.append((duplicate_keyword, ValidationError, "keyword evidence contains duplicates"))

    pending_ad = _ready_search_before()
    pending_ad["objects"][4]["items"][0]["ad_group_ad"]["policy_summary"][
        "review_status"
    ] = "REVIEW_IN_PROGRESS"
    cases.append((pending_ad, ValidationError, "approved responsive search ad"))

    missing_final_url = _ready_search_before()
    missing_final_url["objects"][4]["items"][0]["ad_group_ad"]["ad"].pop(
        "final_urls"
    )
    cases.append((missing_final_url, ValidationError, "bounded HTTPS final URLs"))

    unsafe_final_url = _ready_search_before()
    unsafe_final_url["objects"][4]["items"][0]["ad_group_ad"]["ad"][
        "final_urls"
    ] = ["http://example.com"]
    cases.append((unsafe_final_url, ValidationError, "bounded HTTPS final URLs"))

    malformed_ad = _ready_search_before()
    malformed_ad["objects"][4]["items"] = [{"campaign": {"id": "1"}}]
    cases.append((malformed_ad, ValidationError, "Search ad evidence is malformed"))

    duplicate_ad = _ready_search_before()
    duplicate_ad["objects"][4]["items"].append(
        {
            "campaign": {"id": "1"},
            "ad_group": {"id": "10", "status": "ENABLED"},
            "ad_group_ad": {
                "status": "ENABLED",
                "ad": {
                    "id": "30",
                    "type": "RESPONSIVE_SEARCH_AD",
                    "final_urls": ["https://example.com/landing"],
                },
                "policy_summary": {
                    "approval_status": "APPROVED",
                    "review_status": "REVIEWED",
                },
            },
        }
    )
    cases.append((duplicate_ad, ValidationError, "Search ad evidence contains duplicates"))

    for before, error_type, message in cases:
        with pytest.raises(error_type, match=message):
            PlanBuilder().build(
                "campaign_status",
                profile="operator",
                customer_id=CUSTOMER,
                policy_id="policy",
                payload=payload,
                before=before,
            )


def test_search_enable_uses_highest_effective_keyword_bid() -> None:
    before = _ready_search_before()
    before["objects"][2]["items"].append(
        {
            "campaign": {"id": "1"},
            "ad_group": {
                "id": "11",
                "status": "PAUSED",
                "cpc_bid_micros": "250000",
            },
        }
    )
    before["objects"][3]["items"].append(
        {
            "campaign": {"id": "1"},
            "ad_group": {"id": "10", "status": "ENABLED"},
            "ad_group_criterion": {
                "criterion_id": "21",
                "status": "ENABLED",
                "negative": False,
                "type": "KEYWORD",
                "effective_cpc_bid_micros": "750000",
                "effective_cpc_bid_source": "AD_GROUP_CRITERION",
                "keyword": {
                    "text": "audio transcription app",
                    "match_type": "PHRASE",
                },
            },
        }
    )

    plan = PlanBuilder().build(
        "campaign_status",
        profile="operator",
        customer_id=CUSTOMER,
        policy_id="policy",
        payload={"campaignId": "1", "status": "ENABLED"},
        before=before,
    )

    assert plan.after["bidMicros"] == "750000"


@pytest.mark.parametrize(
    ("strategy", "extra", "field"),
    [
        ("MANUAL_CPC", {"enhancedCpcEnabled": False}, "manual_cpc"),
        ("TARGET_CPA", {"targetCpaMicros": "100"}, "target_cpa"),
    ],
)
def test_campaign_bidding_variants(strategy: str, extra: dict[str, Any], field: str) -> None:
    payload = {"campaignId": "1", "strategy": strategy, **extra}
    plan = PlanBuilder().build(
        "campaign_bidding",
        profile="operator",
        customer_id=CUSTOMER,
        policy_id="policy",
        payload=payload,
        before=_campaign_bidding_before(),
    )
    assert field in plan.items[0].resource


@pytest.mark.parametrize(
    "strategy",
    ["MAXIMIZE_CONVERSIONS", "MAXIMIZE_CONVERSION_VALUE", "TARGET_ROAS"],
)
def test_campaign_bidding_without_matching_policy_limit_is_not_supported(strategy: str) -> None:
    with pytest.raises(NotSupportedError, match="monetary limit"):
        PlanBuilder().build(
            "campaign_bidding",
            profile="operator",
            customer_id=CUSTOMER,
            policy_id="policy",
            payload={"campaignId": "1", "strategy": strategy, "targetRoas": 1.5},
            before=_campaign_bidding_before(),
        )


def test_campaign_bidding_rejects_enhanced_cpc_and_active_campaign() -> None:
    with pytest.raises(ValidationError, match="Enhanced CPC"):
        PlanBuilder().build(
            "campaign_bidding",
            profile="operator",
            customer_id=CUSTOMER,
            policy_id="policy",
            payload={
                "campaignId": "1",
                "strategy": "MANUAL_CPC",
                "enhancedCpcEnabled": True,
            },
            before=_campaign_bidding_before(),
        )
    with pytest.raises(ValidationError, match="PAUSED"):
        PlanBuilder().build(
            "campaign_bidding",
            profile="operator",
            customer_id=CUSTOMER,
            policy_id="policy",
            payload={"campaignId": "1", "strategy": "MANUAL_CPC"},
            before=_campaign_bidding_before(status="ENABLED"),
        )


def test_child_enable_bid_evidence_branches_are_fail_closed() -> None:
    ad_group = PlanBuilder().build(
        "ad_group_update",
        profile="operator",
        customer_id=CUSTOMER,
        policy_id="policy",
        payload={
            "adGroupId": "1",
            "status": "ENABLED",
            "cpcBidMicros": "100",
        },
        before=_ad_group_update_before(),
    )
    assert ad_group.after["bidMicros"] == "100"
    with pytest.raises(ValidationError, match="authoritative positive CPC"):
        PlanBuilder().build(
            "ad_group_update",
            profile="operator",
            customer_id=CUSTOMER,
            policy_id="policy",
            payload={"adGroupId": "1", "status": "ENABLED"},
            before=_ad_group_update_before(current_bid=None),
        )

    criterion = PlanBuilder().build(
        "criterion_update",
        profile="operator",
        customer_id=CUSTOMER,
        policy_id="policy",
        payload={
            "level": "AD_GROUP",
            "adGroupId": "1",
            "criterionId": "2",
            "status": "ENABLED",
            "cpcBidMicros": "100",
        },
        before=_criterion_update_before(),
    )
    assert criterion.after["bidMicros"] == "100"
    invalid_criterion = _criterion_update_before()
    invalid_criterion["objects"][0]["items"][0]["ad_group_criterion"][
        "effective_cpc_bid_source"
    ] = "UNSPECIFIED"
    with pytest.raises(ValidationError, match="authoritative positive effective CPC"):
        PlanBuilder().build(
            "criterion_update",
            profile="operator",
            customer_id=CUSTOMER,
            policy_id="policy",
            payload={
                "level": "AD_GROUP",
                "adGroupId": "1",
                "criterionId": "2",
                "status": "ENABLED",
            },
            before=invalid_criterion,
        )


def test_money_exposure_is_carried_for_schedule_status_and_bidding_plans() -> None:
    schedule = PlanBuilder().build(
        "campaign_update",
        profile="operator",
        customer_id=CUSTOMER,
        policy_id="policy",
        payload={"campaignId": "1", "startDateTime": "2026-09-01 00:00:00"},
        before=_campaign_bidding_before(),
    )
    enable_before = _ready_search_before()
    enable_before["objects"][0]["items"][0]["campaign_budget"]["amount_micros"] = "1234"
    enabled = PlanBuilder().build(
        "campaign_status",
        profile="operator",
        customer_id=CUSTOMER,
        policy_id="policy",
        payload={"campaignId": "1", "status": "ENABLED"},
        before=enable_before,
    )
    bidding = PlanBuilder().build(
        "campaign_bidding",
        profile="operator",
        customer_id=CUSTOMER,
        policy_id="policy",
        payload={"campaignId": "1", "strategy": "MANUAL_CPC"},
        before=_campaign_bidding_before(),
    )
    for plan in (schedule, enabled, bidding):
        assert plan.monetary_delta["spendMicros"] is None
        assert plan.after["currentCampaignDailyBudgetMicros"] == "1234"


def test_child_budget_is_scoped_to_exact_parent_and_only_required_for_spend() -> None:
    valid_before = {
        "objects": [
            {
                "items": [
                    {
                        "ad_group": {"id": "1"},
                        "campaign": {
                            "id": "2",
                            "campaign_budget": f"customers/{CUSTOMER}/campaignBudgets/20",
                        },
                    },
                    {
                        "campaign": {
                            "id": "3",
                            "campaign_budget": f"customers/{CUSTOMER}/campaignBudgets/30",
                        },
                        "campaign_budget": {
                            "resource_name": f"customers/{CUSTOMER}/campaignBudgets/30",
                            "amount_micros": "9999",
                        },
                    },
                    {
                        "campaign": {
                            "id": "2",
                            "campaign_budget": f"customers/{CUSTOMER}/campaignBudgets/20",
                        },
                        "campaign_budget": {
                            "resource_name": f"customers/{CUSTOMER}/campaignBudgets/20",
                            "amount_micros": "1234",
                        },
                    },
                ]
            }
        ]
    }
    name_only = PlanBuilder().build(
        "ad_group_update",
        profile="operator",
        customer_id=CUSTOMER,
        policy_id="policy",
        payload={"adGroupId": "1", "name": "Renamed"},
        before=valid_before,
    )
    paused_criterion = PlanBuilder().build(
        "criterion_update",
        profile="operator",
        customer_id=CUSTOMER,
        policy_id="policy",
        payload={
            "level": "AD_GROUP",
            "adGroupId": "1",
            "criterionId": "2",
            "status": "PAUSED",
        },
        before=_criterion_update_before(),
    )
    assert "currentCampaignDailyBudgetMicros" not in name_only.after
    assert "currentCampaignDailyBudgetMicros" not in paused_criterion.after

    spend_update = PlanBuilder().build(
        "ad_group_update",
        profile="operator",
        customer_id=CUSTOMER,
        policy_id="policy",
        payload={"adGroupId": "1", "cpcBidMicros": "100"},
        before=valid_before,
    )
    assert spend_update.after["currentCampaignDailyBudgetMicros"] == "1234"

    invalid_parent_budget = PlanBuilder().build(
        "ad_group_update",
        profile="operator",
        customer_id=CUSTOMER,
        policy_id="policy",
        payload={"adGroupId": "1", "cpcBidMicros": "100"},
        before={
            "objects": [
                {
                    "items": [
                        {
                            "ad_group": {"id": "1"},
                            "campaign": {
                                "id": "2",
                                "campaign_budget": (
                                    f"customers/{CUSTOMER}/campaignBudgets/20"
                                ),
                            },
                        },
                        {
                            "campaign": {"id": "2"},
                            "campaign_budget": {
                                "resource_name": f"customers/{CUSTOMER}/campaignBudgets/21",
                                "amount_micros": "9999",
                            },
                        },
                    ]
                }
            ]
        },
    )
    assert "currentCampaignDailyBudgetMicros" not in invalid_parent_budget.after


def test_child_budget_resolution_fails_closed_for_incomplete_or_invalid_rows() -> None:
    budget_resource = f"customers/{CUSTOMER}/campaignBudgets/20"
    assert (
        _find_parent_campaign_budget(
            {"ad_group": {"id": "1"}, "campaign_budget": {"amount_micros": "invalid"}},
            "ad_group",
            "1",
        )
        is None
    )
    assert (
        _find_parent_campaign_budget(
            {"ad_group": {"id": "1"}, "campaign": {"id": "2"}},
            "ad_group",
            "1",
        )
        is None
    )
    assert (
        _find_parent_campaign_budget(
            [
                {
                    "ad_group": {"id": "1"},
                    "campaign": {"id": "2", "campaign_budget": budget_resource},
                },
                {
                    "campaign": {"id": "2", "campaign_budget": budget_resource},
                    "campaign_budget": {
                        "resource_name": budget_resource,
                        "amount_micros": "invalid",
                    },
                },
            ],
            "ad_group",
            "1",
        )
        is None
    )


def test_additional_plan_validation_and_optional_branches() -> None:
    with pytest.raises(ValidationError, match="must be text"):
        PlanBuilder().build(
            "asset_create",
            profile="operator",
            customer_id=CUSTOMER,
            policy_id="policy",
            payload={"name": 1, "assetType": "TEXT", "text": "text"},
            before={"objects": []},
        )
    with pytest.raises(ValidationError, match="safe characters"):
        PlanBuilder().build(
            "asset_create",
            profile="operator",
            customer_id=CUSTOMER,
            policy_id="policy",
            payload={"name": "bad\nname", "assetType": "TEXT", "text": "text"},
            before={"objects": []},
        )
    payload = _payloads()["search_campaign_create"]
    payload["finalUrl"] = "https://example.com/bad path"
    with pytest.raises(ValidationError, match="malformed"):
        PlanBuilder().build(
            "search_campaign_create",
            profile="operator",
            customer_id=CUSTOMER,
            policy_id="policy",
            payload=payload,
            before={"objects": []},
        )
    payload = _payloads()["search_campaign_create"]
    payload["headlines"] = []
    with pytest.raises(ValidationError, match="between"):
        PlanBuilder().build(
            "search_campaign_create",
            profile="operator",
            customer_id=CUSTOMER,
            policy_id="policy",
            payload=payload,
            before={"objects": []},
        )
    with pytest.raises(ValidationError, match="yyyy-MM-dd"):
        PlanBuilder().build(
            "campaign_update",
            profile="operator",
            customer_id=CUSTOMER,
            policy_id="policy",
            payload={"campaignId": "1", "startDateTime": 1},
            before={"objects": []},
        )
    with pytest.raises(ValidationError, match="ownerType"):
        state_queries(
            "asset_link",
            CUSTOMER,
            {"ownerType": "OTHER", "ownerId": "1", "assetResourceName": ASSET},
        )
    with pytest.raises(ValidationError, match="Unsupported"):
        PlanBuilder().build(
            cast(WriteKind, "unknown"),
            profile="operator",
            customer_id=CUSTOMER,
            policy_id="policy",
            payload={},
            before={},
        )

    ad_group = PlanBuilder().build(
        "ad_group_create",
        profile="operator",
        customer_id=CUSTOMER,
        policy_id="policy",
        payload={"campaignId": "1", "name": "Group", "cpcBidMicros": "10"},
        before={"objects": []},
    )
    assert ad_group.after["bidMicros"] == "10"
    ad_group_without_bid = PlanBuilder().build(
        "ad_group_create",
        profile="operator",
        customer_id=CUSTOMER,
        policy_id="policy",
        payload={"campaignId": "1", "name": "Group without bid"},
        before={"objects": []},
    )
    assert "bidMicros" not in ad_group_without_bid.after

    with pytest.raises(ValidationError, match="status"):
        PlanBuilder().build(
            "ad_group_update",
            profile="operator",
            customer_id=CUSTOMER,
            policy_id="policy",
            payload={"adGroupId": "1", "status": "REMOVED"},
            before={"objects": []},
        )
    with pytest.raises(ValidationError, match="matchType"):
        PlanBuilder().build(
            "keyword_create",
            profile="operator",
            customer_id=CUSTOMER,
            policy_id="policy",
            payload={
                "level": "AD_GROUP",
                "adGroupId": "1",
                "text": "keyword",
                "matchType": "INVALID",
            },
            before={"objects": []},
        )
    with pytest.raises(ValidationError, match="status"):
        PlanBuilder().build(
            "criterion_update",
            profile="operator",
            customer_id=CUSTOMER,
            policy_id="policy",
            payload={
                "level": "AD_GROUP",
                "adGroupId": "1",
                "criterionId": "2",
                "status": "REMOVED",
            },
            before=_criterion_update_before(),
        )
    with pytest.raises(ValidationError, match="AD_GROUP keywords"):
        PlanBuilder().build(
            "criterion_update",
            profile="operator",
            customer_id=CUSTOMER,
            policy_id="policy",
            payload={
                "level": "CAMPAIGN",
                "campaignId": "1",
                "criterionId": "2",
                "cpcBidMicros": "10",
            },
            before={"objects": []},
        )
    with pytest.raises(ValidationError, match="at least one"):
        PlanBuilder().build(
            "criterion_update",
            profile="operator",
            customer_id=CUSTOMER,
            policy_id="policy",
            payload={"level": "AD_GROUP", "adGroupId": "1", "criterionId": "2"},
            before=_criterion_update_before(),
        )
    assert _find_integer({"amount_micros": "invalid"}, "amount_micros") is None
    assert _find_integer([{}, {"amount_micros": "2"}], "amount_micros") == 2


@pytest.mark.parametrize(
    ("owner_type", "operation", "parent_field"),
    [
        ("CAMPAIGN", "campaign_asset_operation", "campaign.id"),
        ("AD_GROUP", "ad_group_asset_operation", "ad_group.id"),
        ("ASSET_GROUP", "asset_group_asset_operation", "asset_group.id"),
    ],
)
def test_asset_link_variants(owner_type: str, operation: str, parent_field: str) -> None:
    payload = {
        "ownerType": owner_type,
        "ownerId": "1",
        "assetResourceName": ASSET,
        "fieldType": "HEADLINE",
    }
    queries = state_queries("asset_link", CUSTOMER, payload)
    assert parent_field in queries[0][1].partition(" FROM ")[0]
    plan = PlanBuilder().build(
        "asset_link",
        profile="operator",
        customer_id=CUSTOMER,
        policy_id="policy",
        payload=payload,
        before={"objects": []},
    )
    assert plan.items[0].operation_type == operation


def test_remaining_typed_variants() -> None:
    builder = PlanBuilder()
    cases = [
        (
            "negative_keyword_create",
            {
                "level": "AD_GROUP",
                "adGroupId": "1",
                "text": "negative",
                "matchType": "BROAD",
            },
        ),
        (
            "criterion_update",
            {
                "level": "AD_GROUP",
                "adGroupId": "1",
                "criterionId": "2",
                "status": "ENABLED",
            },
        ),
        (
            "asset_create",
            {
                "name": "Video",
                "assetType": "YOUTUBE_VIDEO",
                "youtubeVideoId": "abc123",
            },
        ),
        (
            "ad_create",
            {
                "adGroupId": "1",
                "name": "App ad",
                "adType": "APP_AD",
                "headlines": ["One", "Two"],
                "descriptions": ["Description"],
                "imageAssets": [ASSET],
                "youtubeVideoAssets": [ASSET],
            },
        ),
    ]
    for kind, payload in cases:
        plan = builder.build(
            cast(WriteKind, kind),
            profile="operator",
            customer_id=CUSTOMER,
            policy_id="policy",
            payload=payload,
            before=(
                _criterion_update_before()
                if kind == "criterion_update"
                else {"objects": []}
            ),
        )
        assert plan.items


@pytest.mark.parametrize(
    ("kind", "change"),
    [
        ("search_campaign_create", {"dailyBudgetMicros": "0"}),
        ("search_campaign_create", {"keywordMatchType": "INVALID"}),
        ("search_campaign_create", {"finalUrl": "file:///tmp"}),
        ("performance_max_campaign_create", {"logoAssets": []}),
        ("performance_max_campaign_create", {"landscapeImageAssets": ["wrong"]}),
        ("app_campaign_create", {"appStore": "OTHER"}),
        ("app_campaign_create", {"imageAssets": [], "youtubeVideoAssets": []}),
        ("campaign_update", {"name": None}),
        ("campaign_update", {"name": None, "startDateTime": "2026-01-01"}),
        (
            "campaign_update",
            {
                "name": None,
                "startDateTime": "2026-01-02 00:00:00",
                "endDateTime": "2026-01-01 23:59:59",
            },
        ),
        ("campaign_status", {"status": "REMOVED"}),
        ("campaign_bidding", {"strategy": "OTHER"}),
        ("ad_group_update", {"name": None, "status": None, "cpcBidMicros": None}),
        ("keyword_create", {"level": "CAMPAIGN"}),
        ("negative_keyword_create", {"level": "OTHER"}),
        ("criterion_update", {"level": "OTHER"}),
        ("criterion_update", {"level": "CAMPAIGN", "cpcBidMicros": "1"}),
        ("asset_create", {"assetType": "IMAGE"}),
        ("asset_link", {"ownerType": "OTHER"}),
        ("asset_group_create", {"fieldTypes": []}),
        ("ad_create", {"adType": "OTHER"}),
    ],
)
def test_invalid_typed_plans_fail_closed(kind: str, change: dict[str, Any]) -> None:
    payload = _payloads()[kind]
    payload.update(change)
    with pytest.raises(ValidationError):
        PlanBuilder().build(
            cast(WriteKind, kind),
            profile="operator",
            customer_id=CUSTOMER,
            policy_id="policy",
            payload=payload,
            before=_before_for(kind),
        )


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("campaign_update", {"campaignId": "bad"}),
        ("ad_group_update", {"adGroupId": "bad"}),
        (
            "asset_link",
            {
                "ownerType": "CAMPAIGN",
                "ownerId": "1",
                "assetResourceName": "customers/9999999999/assets/1",
                "fieldType": "HEADLINE",
            },
        ),
    ],
)
def test_state_query_validation(kind: str, payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        state_queries(cast(WriteKind, kind), CUSTOMER, payload)


def test_recommendation_resource_name_accepts_composite_id() -> None:
    plan = PlanBuilder().build(
        "recommendation_apply",
        profile="operator",
        customer_id=CUSTOMER,
        policy_id="policy",
        payload={
            "recommendationResourceName": (
                f"customers/{CUSTOMER}/recommendations/abc~123"
            )
        },
        before={"objects": []},
    )
    assert plan.after["recommendationResourceName"].endswith("/abc~123")


@pytest.mark.parametrize(
    "kind",
    [
        "search_campaign_create",
        "performance_max_campaign_create",
        "app_campaign_create",
        "ad_group_create",
        "keyword_create",
        "negative_keyword_create",
        "asset_create",
        "asset_link",
        "asset_group_create",
        "ad_create",
    ],
)
def test_create_plans_reject_existing_exact_match(kind: str) -> None:
    with pytest.raises(ValidationError, match="already exists"):
        PlanBuilder().build(
            cast(WriteKind, kind),
            profile="operator",
            customer_id=CUSTOMER,
            policy_id="policy",
            payload=_payloads()[kind],
            before={"objects": [{"items": [{"exact_match": {"id": "1"}}]}]},
        )


@pytest.mark.parametrize("objects", [["malformed"], [{}]])
def test_create_plans_reject_malformed_exact_match_state(objects: list[object]) -> None:
    with pytest.raises(ValidationError, match="malformed"):
        PlanBuilder().build(
            "asset_create",
            profile="operator",
            customer_id=CUSTOMER,
            policy_id="policy",
            payload=_payloads()["asset_create"],
            before={"objects": objects},
        )


def test_every_supported_typed_plan_builds_real_v25_mutate_request() -> None:
    client = GoogleAdsClient(
        AnonymousCredentials(), developer_token="synthetic", use_proto_plus=True
    )
    for kind, payload in _payloads().items():
        if kind.startswith("recommendation_"):
            continue
        plan = PlanBuilder().build(
            cast(WriteKind, kind),
            profile="operator",
            customer_id=CUSTOMER,
            policy_id="policy",
            payload=payload,
            before=_before_for(kind),
        )
        GoogleAdsWriteAdapter._validate_plan(plan)
        request = GoogleAdsWriteAdapter._request(client, plan, validate_only=True)
        assert request.validate_only is True
        assert len(request.mutate_operations) == len(plan.items)
