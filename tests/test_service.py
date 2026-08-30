from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

import pytest

from google_ads_mcp.adapter import AdapterSearchResult
from google_ads_mcp.errors import AdapterError, ValidationError
from google_ads_mcp.service import GoogleAdsService

T = TypeVar("T")


def _run(awaitable: Awaitable[T]) -> T:
    return asyncio.run(awaitable)


def test_discovery_customer_and_account_tools(accounts_config: Any, fake_adapter: Any) -> None:
    service = GoogleAdsService(accounts_config, fake_adapter)
    fake_adapter.result = AdapterSearchResult(({"customer": {"id": "2222222222"}},), 1, False)
    auth = _run(service.auth_check("test-read-only", "2222222222"))
    assert auth["items"] == [{"authenticated": True, "readOnly": True}]
    customers = _run(service.customers_list("test-read-only"))
    assert customers["items"][0]["customerId"] == "2222222222"
    customer = _run(service.customer_get("test-read-only", "2222222222"))
    assert customer["context"]["currencyCode"] == "USD"
    hierarchy = _run(service.customer_hierarchy("test-read-only", "2222222222"))
    assert hierarchy["status"] == "ok"
    health = _run(service.account_health("test-read-only", "2222222222"))
    assert health["evidence"]["limitations"]


def test_customer_context_is_cached(accounts_config: Any, fake_adapter: Any) -> None:
    service = GoogleAdsService(accounts_config, fake_adapter)
    _run(service.reports_catalog("test-read-only", "2222222222"))
    _run(service.reports_catalog("test-read-only", "2222222222"))
    assert sum("customer.currency_code" in query for query in fake_adapter.queries) == 1


def test_customer_and_hierarchy_cursors(accounts_config: Any, fake_adapter: Any) -> None:
    async def accessible_customers(_: Any) -> tuple[str, ...]:
        return tuple(f"customers/{index:010d}" for index in range(205))

    fake_adapter.accessible_customers = accessible_customers
    service = GoogleAdsService(accounts_config, fake_adapter)
    customers = _run(service.customers_list("test-read-only"))
    assert len(customers["items"]) == 200
    customers_next = _run(service.customers_list("test-read-only", cursor=customers["nextCursor"]))
    assert len(customers_next["items"]) == 5

    fake_adapter.result = AdapterSearchResult(
        tuple({"customer_client": {"id": str(index)}} for index in range(205)), 205, False
    )
    hierarchy = _run(service.customer_hierarchy("test-read-only", "2222222222"))
    assert len(hierarchy["items"]) == 200
    hierarchy_next = _run(
        service.customer_hierarchy("test-read-only", "2222222222", cursor=hierarchy["nextCursor"])
    )
    assert len(hierarchy_next["items"]) == 5


def test_customer_discovery_is_truncated_at_one_thousand(
    accounts_config: Any, fake_adapter: Any
) -> None:
    async def accessible_customers(_: Any) -> tuple[str, ...]:
        return tuple(f"customers/{index:010d}" for index in range(1_005))

    fake_adapter.accessible_customers = accessible_customers
    response = _run(
        GoogleAdsService(accounts_config, fake_adapter).customers_list("test-read-only")
    )
    assert response["status"] == "partial"
    assert response["truncated"] is True
    assert any("1000" in item for item in response["evidence"]["limitations"])


def test_campaign_query_unknown_enum_and_pagination(
    accounts_config: Any, fake_adapter: Any
) -> None:
    fake_adapter.result = AdapterSearchResult(
        tuple(
            {
                "campaign": {
                    "id": str(index),
                    "name": f"untrusted {index}",
                    "advertising_channel_type": (
                        {"value": "999", "recognized": False}
                        if index == 0
                        else "FUTURE_CHANNEL"
                        if index == 1
                        else "SEARCH"
                    ),
                }
            }
            for index in range(205)
        ),
        205,
        False,
    )
    service = GoogleAdsService(accounts_config, fake_adapter)
    first = _run(
        service.campaigns_query(
            "test-read-only",
            "2222222222",
            statuses=["enabled"],
            channel_types=["SEARCH"],
        )
    )
    assert len(first["items"]) == 200
    assert first["nextCursor"]
    assert first["items"][0]["campaign"]["advertising_channel_type"] == {
        "value": "999",
        "recognized": False,
    }
    assert first["items"][1]["campaign"]["advertising_channel_type"] == {
        "value": "FUTURE_CHANNEL",
        "recognized": False,
    }
    assert "campaign.status IN ('ENABLED')" in fake_adapter.queries[0]
    assert "campaign.advertising_channel_type IN ('SEARCH')" in fake_adapter.queries[0]
    assert "campaign.start_date_time" in fake_adapter.queries[0]
    assert "campaign.end_date_time" in fake_adapter.queries[0]
    assert "campaign.start_date," not in fake_adapter.queries[0]
    assert "campaign.end_date," not in fake_adapter.queries[0]
    assert "unsupported_specialization" in first["evidence"]["limitations"]
    second = _run(
        service.campaigns_query(
            "test-read-only",
            "2222222222",
            statuses=["enabled"],
            channel_types=["SEARCH"],
            cursor=first["nextCursor"],
        )
    )
    assert len(second["items"]) == 5
    assert "nextCursor" not in second


def test_campaign_query_validation(accounts_config: Any, fake_adapter: Any) -> None:
    service = GoogleAdsService(accounts_config, fake_adapter)
    with pytest.raises(ValidationError, match="status"):
        _run(service.campaigns_query("test-read-only", "2222222222", statuses=["invalid"]))
    with pytest.raises(ValidationError, match="channel"):
        _run(service.campaigns_query("test-read-only", "2222222222", channel_types=["BAD VALUE"]))
    with pytest.raises(ValidationError, match="pageSize"):
        _run(service.campaigns_query("test-read-only", "2222222222", page_size=201))


@pytest.mark.parametrize(
    "resource_type",
    ["status", "budget", "bidding", "ad_groups", "ads", "assets", "criteria", "conversions"],
)
def test_campaign_inventory_matrix(
    accounts_config: Any, fake_adapter: Any, resource_type: str
) -> None:
    fake_adapter.result = AdapterSearchResult(({"campaign": {"id": "123"}},), 1, False)
    service = GoogleAdsService(accounts_config, fake_adapter)
    response = _run(
        service.campaign_inventory("test-read-only", "2222222222", "123", resource_type)
    )
    assert response["status"] == "ok"
    assert "campaign.id = 123" in fake_adapter.queries[0]


def test_campaign_inventory_rejects_unsafe_inputs(accounts_config: Any, fake_adapter: Any) -> None:
    service = GoogleAdsService(accounts_config, fake_adapter)
    with pytest.raises(ValidationError, match="digits"):
        _run(service.campaign_inventory("test-read-only", "2222222222", "1 OR 1", "ads"))
    with pytest.raises(ValidationError, match="resourceType"):
        _run(service.campaign_inventory("test-read-only", "2222222222", "1", "mutations"))


def test_metadata_and_catalog(accounts_config: Any, fake_adapter: Any) -> None:
    service = GoogleAdsService(accounts_config, fake_adapter)
    metadata = _run(
        service.resource_metadata("test-read-only", "2222222222", ["campaign.id", "metrics.clicks"])
    )
    assert len(metadata["items"]) == 2
    catalog = _run(service.reports_catalog("test-read-only", "2222222222"))
    assert any(item["name"] == "search_terms" for item in catalog["items"])


def test_v020_info_profiles_and_analytics(accounts_config: Any, fake_adapter: Any) -> None:
    service = GoogleAdsService(accounts_config, fake_adapter)
    info = _run(service.server_info())
    assert info["items"][0]["readTools"] == 22
    profiles = _run(service.profiles_list())
    assert profiles["items"][0]["allowWrites"] is False

    fake_adapter.result = AdapterSearchResult(({"campaign": {"id": "1"}},), 1, False)
    diagnostics = _run(service.campaign_diagnostics("test-read-only", "2222222222", "1"))
    assert diagnostics["status"] == "ok"
    search_terms = _run(
        service.search_terms_report(
            "test-read-only", "2222222222", "2026-01-01", "2026-01-02"
        )
    )
    assert search_terms["status"] == "ok"
    assets = _run(
        service.asset_performance_report(
            "test-read-only", "2222222222", "2026-01-01", "2026-01-02"
        )
    )
    assert assets["status"] == "ok"

    goals = _run(service.conversion_goals_list("test-read-only", "2222222222"))
    assert "attribution truth" in goals["evidence"]["limitations"][0]
    recommendations = _run(
        service.recommendations_list("test-read-only", "2222222222", ["1", "2"])
    )
    assert "campaign.id IN (1, 2)" in fake_adapter.queries[-1]
    assert "recommendation.impact" not in fake_adapter.queries[-1]
    assert "proposals" in recommendations["evidence"]["limitations"][0]


def test_v020_analytics_validation_and_empty_diagnostics(
    accounts_config: Any, fake_adapter: Any
) -> None:
    service = GoogleAdsService(accounts_config, fake_adapter)
    missing = _run(service.campaign_diagnostics("test-read-only", "2222222222", "1"))
    assert missing["status"] == "partial"
    with pytest.raises(ValidationError, match="campaignId"):
        _run(service.campaign_diagnostics("test-read-only", "2222222222", "bad"))
    with pytest.raises(ValidationError, match="campaignIds"):
        _run(service.recommendations_list("test-read-only", "2222222222", ["bad"]))


def test_keyword_ideas_and_forecast(accounts_config: Any, fake_adapter: Any) -> None:
    service = GoogleAdsService(accounts_config, fake_adapter)
    ideas = _run(
        service.keyword_ideas(
            "test-read-only",
            "2222222222",
            keywords=["  shoes  "],
            page_url="https://example.com",
            language_id="1000",
            geo_target_ids=["2840"],
        )
    )
    assert ideas["items"] == [{"text": "idea"}]
    assert ideas["status"] == "ok"
    assert ideas["truncated"] is False
    assert fake_adapter.idea_calls[0]["keywords"] == ("shoes",)

    fake_adapter.idea_result = AdapterSearchResult(
        tuple({"text": f"idea-{index}"} for index in range(200)), None, True
    )
    truncated = _run(
        service.keyword_ideas(
            "test-read-only",
            "2222222222",
            keywords=["shoes"],
            page_url=None,
            language_id="1000",
            geo_target_ids=["2840"],
        )
    )
    assert truncated["status"] == "partial"
    assert truncated["evidence"]["partial"] is True
    assert truncated["truncated"] is True
    assert len(truncated["items"]) == 200
    assert "first 200" in truncated["evidence"]["limitations"][1]
    forecast = _run(
        service.forecast_run(
            "test-read-only",
            "2222222222",
            keywords=[{"text": " shoes ", "matchType": "EXACT"}],
            language_id="1000",
            geo_target_ids=["2840"],
            date_from="2026-01-01",
            date_to="2026-01-31",
            daily_budget_micros="1000000",
            max_cpc_bid_micros="100000",
        )
    )
    assert forecast["items"] == [{"clicks": 10.0}]
    assert fake_adapter.forecast_calls[0]["currency_code"] == "USD"
    assert fake_adapter.forecast_calls[0]["keywords"] == (
        {"text": "shoes", "matchType": "EXACT"},
    )


@pytest.mark.parametrize(
    "values",
    [
        {"keywords": None, "page_url": None, "language_id": "1000", "geo_target_ids": ["1"]},
        {"keywords": ["x" * 81], "page_url": None, "language_id": "1000", "geo_target_ids": ["1"]},
        {
            "keywords": ["x"],
            "page_url": "file:///tmp",
            "language_id": "1000",
            "geo_target_ids": ["1"],
        },
        {"keywords": ["x"], "page_url": None, "language_id": "bad", "geo_target_ids": ["1"]},
        {"keywords": ["x"], "page_url": None, "language_id": "1000", "geo_target_ids": []},
        {
            "keywords": ["x"],
            "page_url": None,
            "language_id": "1000",
            "geo_target_ids": ["1", "1"],
        },
        {
            "keywords": ["x"],
            "page_url": None,
            "language_id": "1000",
            "geo_target_ids": [str(index) for index in range(21)],
        },
        {
            "keywords": ["x"],
            "page_url": None,
            "language_id": "1000",
            "geo_target_ids": ["٢٨٤٠"],
        },
    ],
)
def test_keyword_ideas_validation(
    accounts_config: Any, fake_adapter: Any, values: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError):
        _run(
            GoogleAdsService(accounts_config, fake_adapter).keyword_ideas(
                "test-read-only", "2222222222", **values
            )
        )


@pytest.mark.parametrize(
    "change",
    [
        {"keywords": []},
        {"keywords": [{"text": "shoes"}]},
        {"keywords": [{"text": "shoes", "matchType": "INVALID"}]},
        {
            "keywords": [
                {"text": "shoes", "matchType": "EXACT"},
                {"text": " shoes ", "matchType": "EXACT"},
            ]
        },
        {"language_id": "bad"},
        {"daily_budget_micros": "0"},
        {"max_cpc_bid_micros": "bad"},
        {"geo_target_ids": []},
        {"geo_target_ids": ["2840", "2840"]},
        {"geo_target_ids": [str(index) for index in range(21)]},
        {"geo_target_ids": ["٢٨٤٠"]},
    ],
)
def test_forecast_validation(
    accounts_config: Any, fake_adapter: Any, change: dict[str, Any]
) -> None:
    values: dict[str, Any] = {
        "keywords": [{"text": "shoes", "matchType": "EXACT"}],
        "language_id": "1000",
        "geo_target_ids": ["2840"],
        "date_from": "2026-01-01",
        "date_to": "2026-01-31",
        "daily_budget_micros": "1000000",
        "max_cpc_bid_micros": "100000",
    }
    values.update(change)
    with pytest.raises(ValidationError):
        _run(
            GoogleAdsService(accounts_config, fake_adapter).forecast_run(
                "test-read-only", "2222222222", **values
            )
        )


def test_report_run_and_test_account_limitation(accounts_config: Any, fake_adapter: Any) -> None:
    fake_adapter.test_account = True
    service = GoogleAdsService(accounts_config, fake_adapter)
    response = _run(
        service.report_run(
            "test-read-only",
            "2222222222",
            "campaign_performance",
            "2026-01-01",
            "2026-01-31",
        )
    )
    assert response["status"] == "ok"
    assert response["evidence"]["dateFrom"] == "2026-01-01"
    assert any("Test account" in item for item in response["evidence"]["limitations"])
    assert any("modeled" in item for item in response["evidence"]["limitations"])


def test_report_not_supported(accounts_config: Any, fake_adapter: Any) -> None:
    class NotSupported(type(fake_adapter)):
        async def search(self, profile: Any, customer_id: str, query: str) -> AdapterSearchResult:
            if "customer.currency_code" in query:
                return await super().search(profile, customer_id, query)
            raise AdapterError("unsupported", code="not_supported", status="not_supported")

    service = GoogleAdsService(accounts_config, NotSupported())
    response = _run(
        service.report_run(
            "test-read-only",
            "2222222222",
            "video_performance",
            "2026-01-01",
            "2026-01-02",
        )
    )
    assert response["status"] == "not_supported"
    assert not response["items"]


def test_truncated_typed_result(accounts_config: Any, fake_adapter: Any) -> None:
    fake_adapter.result = AdapterSearchResult(({"campaign": {"id": "1"}},), 2_000, True)
    service = GoogleAdsService(accounts_config, fake_adapter)
    response = _run(service.campaigns_query("test-read-only", "2222222222"))
    assert response["status"] == "partial"
    assert response["truncated"]
    assert any("1000" in item for item in response["evidence"]["limitations"])


def test_change_events_window_redaction(accounts_config: Any, fake_adapter: Any) -> None:
    today = datetime.now(UTC).date()
    start = today - timedelta(days=1)
    fake_adapter.result = AdapterSearchResult(
        (
            {
                "change_event": {
                    "user_email": "owner@example.com",
                    "client_type": "GOOGLE_ADS_WEB_CLIENT",
                }
            },
        ),
        1,
        False,
    )
    service = GoogleAdsService(accounts_config, fake_adapter)
    response = _run(
        service.change_events_query(
            "test-read-only", "2222222222", start.isoformat(), today.isoformat()
        )
    )
    assert response["items"][0]["change_event"]["user_email"] == "[REDACTED]"
    assert "change_event.user_email" not in fake_adapter.queries[0]
    exposed = _run(
        service.change_events_query(
            "test-read-only",
            "2222222222",
            start.isoformat(),
            today.isoformat(),
            include_user_identity=True,
        )
    )
    assert exposed["items"][0]["change_event"]["user_email"] == "owner@example.com"
    assert "change_event.user_email" in fake_adapter.queries[-1]
    old = today - timedelta(days=31)
    with pytest.raises(ValidationError, match="30 days"):
        _run(
            service.change_events_query(
                "test-read-only", "2222222222", old.isoformat(), today.isoformat()
            )
        )


def test_gaql_validate_and_search(accounts_config: Any, fake_adapter: Any) -> None:
    fake_adapter.result = AdapterSearchResult(({"campaign": {"id": "1"}},), 1, False)
    service = GoogleAdsService(accounts_config, fake_adapter)
    validated = _run(
        service.gaql_validate(
            "test-read-only",
            "2222222222",
            "SELECT campaign.id FROM campaign LIMIT 1",
        )
    )
    assert validated["items"][0]["valid"]
    searched = _run(
        service.gaql_search(
            "test-read-only",
            "2222222222",
            "SELECT campaign.id FROM campaign LIMIT 1",
        )
    )
    assert searched["items"]
    assert "does not provide cursors" in searched["evidence"]["limitations"][0]

    bounded = _run(
        service.gaql_search(
            "test-read-only",
            "2222222222",
            "SELECT metrics.clicks FROM campaign WHERE segments.date DURING LAST_30_DAYS LIMIT 1",
        )
    )
    assert any("customer time zone" in item for item in bounded["evidence"]["limitations"])


def test_profile_and_customer_are_explicit(accounts_config: Any, fake_adapter: Any) -> None:
    service = GoogleAdsService(accounts_config, fake_adapter)
    with pytest.raises(ValidationError, match="profile"):
        _run(service.auth_check("", "2222222222"))
    with pytest.raises(Exception, match="10 digits"):
        _run(service.auth_check("test-read-only", "123"))
