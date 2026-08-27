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
    customers_next = _run(
        service.customers_list("test-read-only", cursor=customers["nextCursor"])
    )
    assert len(customers_next["items"]) == 5

    fake_adapter.result = AdapterSearchResult(
        tuple({"customer_client": {"id": str(index)}} for index in range(205)), 205, False
    )
    hierarchy = _run(service.customer_hierarchy("test-read-only", "2222222222"))
    assert len(hierarchy["items"]) == 200
    hierarchy_next = _run(
        service.customer_hierarchy(
            "test-read-only", "2222222222", cursor=hierarchy["nextCursor"]
        )
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
        _run(
            service.campaigns_query(
                "test-read-only", "2222222222", channel_types=["BAD VALUE"]
            )
        )
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
        service.campaign_inventory(
            "test-read-only", "2222222222", "123", resource_type
        )
    )
    assert response["status"] == "ok"
    assert "campaign.id = 123" in fake_adapter.queries[0]


def test_campaign_inventory_rejects_unsafe_inputs(accounts_config: Any, fake_adapter: Any) -> None:
    service = GoogleAdsService(accounts_config, fake_adapter)
    with pytest.raises(ValidationError, match="digits"):
        _run(
            service.campaign_inventory(
                "test-read-only", "2222222222", "1 OR 1", "ads"
            )
        )
    with pytest.raises(ValidationError, match="resourceType"):
        _run(
            service.campaign_inventory(
                "test-read-only", "2222222222", "1", "mutations"
            )
        )


def test_metadata_and_catalog(accounts_config: Any, fake_adapter: Any) -> None:
    service = GoogleAdsService(accounts_config, fake_adapter)
    metadata = _run(
        service.resource_metadata(
            "test-read-only", "2222222222", ["campaign.id", "metrics.clicks"]
        )
    )
    assert len(metadata["items"]) == 2
    catalog = _run(service.reports_catalog("test-read-only", "2222222222"))
    assert any(item["name"] == "search_terms" for item in catalog["items"])


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
        async def search(
            self, profile: Any, customer_id: str, query: str
        ) -> AdapterSearchResult:
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
            "SELECT metrics.clicks FROM campaign "
            "WHERE segments.date DURING LAST_30_DAYS LIMIT 1",
        )
    )
    assert any("customer time zone" in item for item in bounded["evidence"]["limitations"])


def test_profile_and_customer_are_explicit(accounts_config: Any, fake_adapter: Any) -> None:
    service = GoogleAdsService(accounts_config, fake_adapter)
    with pytest.raises(ValidationError, match="profile"):
        _run(service.auth_check("", "2222222222"))
    with pytest.raises(Exception, match="10 digits"):
        _run(service.auth_check("test-read-only", "123"))
