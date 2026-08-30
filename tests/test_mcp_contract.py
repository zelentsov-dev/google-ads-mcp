from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult

from google_ads_mcp import server as server_module
from google_ads_mcp.errors import PublicError
from google_ads_mcp.server import create_mcp


def _split(result: Any) -> tuple[list[Any], dict[str, Any]]:
    assert isinstance(result, CallToolResult)
    assert result.structuredContent is not None
    return list(result.content), result.structuredContent


def test_tools_list_matches_golden(accounts_config: Any, fake_adapter: Any) -> None:
    async def inspect() -> None:
        server = create_mcp(config=accounts_config, adapter=fake_adapter)
        tools = await server.list_tools()
        actual = [tool.name for tool in tools]
        expected = json.loads(Path("api-contract/tools-v0.2.json").read_text())["tools"]
        assert sorted(actual) == expected
        golden = json.loads(Path("tests/golden/tools-list.json").read_text())
        output_contracts = []
        projected_tools = []
        for tool in tools:
            output_schema = tool.outputSchema or {}
            output_contracts.append(
                {
                    "required": output_schema.get("required", []),
                    "optional": sorted(
                        set(output_schema.get("properties", {}))
                        - set(output_schema.get("required", []))
                    ),
                    "statuses": output_schema.get("properties", {})
                    .get("status", {})
                    .get("enum", []),
                }
            )
            projected_tools.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputProperties": sorted(tool.inputSchema.get("properties", {})),
                    "inputRequired": tool.inputSchema.get("required", []),
                }
            )
        assert all(contract == output_contracts[0] for contract in output_contracts)
        assert {
            "responseContract": output_contracts[0],
            "tools": projected_tools,
        } == golden
        assert all(tool.outputSchema and tool.outputSchema["type"] == "object" for tool in tools)
        account_scoped = {
            tool.name for tool in tools if "customerId" in tool.inputSchema.get("properties", {})
        }
        assert "auth_check" in account_scoped
        assert "operations_apply" not in account_scoped
        assert "server_info" not in account_scoped

    asyncio.run(inspect())


def test_forecast_schema_requires_typed_keyword_match_types(
    accounts_config: Any, fake_adapter: Any
) -> None:
    async def inspect() -> None:
        tools = await create_mcp(config=accounts_config, adapter=fake_adapter).list_tools()
        forecast = next(tool for tool in tools if tool.name == "forecast_run")
        keyword_schema = forecast.inputSchema["$defs"]["ForecastKeyword"]

        assert forecast.inputSchema["properties"]["keywords"]["items"] == {
            "$ref": "#/$defs/ForecastKeyword"
        }
        assert keyword_schema["required"] == ["text", "matchType"]
        assert keyword_schema["properties"]["matchType"]["enum"] == [
            "EXACT",
            "PHRASE",
            "BROAD",
        ]

    asyncio.run(inspect())


def test_bounded_geo_and_criterion_schemas_fail_closed(
    accounts_config: Any, fake_adapter: Any
) -> None:
    async def inspect() -> None:
        tools = await create_mcp(config=accounts_config, adapter=fake_adapter).list_tools()
        for name in ("keyword_ideas", "forecast_run"):
            schema = next(tool for tool in tools if tool.name == name).inputSchema[
                "properties"
            ]["geoTargetIds"]
            assert schema["minItems"] == 1
            assert schema["maxItems"] == 20
            assert schema["uniqueItems"] is True
        criterion = next(
            tool for tool in tools if tool.name == "criterion_update_preview"
        ).inputSchema
        assert criterion["properties"]["level"]["const"] == "AD_GROUP"
        assert "campaignId" not in criterion["properties"]

    asyncio.run(inspect())


def test_tool_call_returns_text_and_structured_content(
    accounts_config: Any, fake_adapter: Any
) -> None:
    async def call() -> None:
        server = create_mcp(config=accounts_config, adapter=fake_adapter)
        content, structured = _split(
            await server.call_tool(
                "reports_catalog",
                {"profile": "test-read-only", "customerId": "2222222222"},
            )
        )
        assert content[0].type == "text"
        assert content[0].text.startswith("ok:")
        assert len(content[0].text) < 160
        assert structured["status"] == "ok"

    asyncio.run(call())


def test_public_errors_are_sanitized(accounts_config: Any, fake_adapter: Any) -> None:
    async def call() -> None:
        server = create_mcp(config=accounts_config, adapter=fake_adapter)
        _, structured = _split(
            await server.call_tool(
                "gaql_search",
                {
                    "profile": "test-read-only",
                    "customerId": "2222222222",
                    "query": "SELECT customer_user_access.email FROM customer_user_access LIMIT 1",
                },
            )
        )
        assert structured["status"] == "unavailable"
        rendered = json.dumps(structured)
        assert "customer_user_access.email" not in rendered
        assert structured["items"] == []

    asyncio.run(call())


def test_credential_shaped_identifiers_never_echo(accounts_config: Any, fake_adapter: Any) -> None:
    async def call() -> None:
        raw_profile = "synthetic_SECRET_PROFILE_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        raw_customer = "synthetic_SECRET_CUSTOMER_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        server = create_mcp(config=accounts_config, adapter=fake_adapter)
        content, structured = _split(
            await server.call_tool(
                "auth_check",
                {"profile": raw_profile, "customerId": raw_customer},
            )
        )
        rendered = json.dumps(structured) + "".join(str(item) for item in content)
        assert raw_profile not in rendered
        assert raw_customer not in rendered
        assert structured["context"]["profile"] is None
        assert structured["context"]["customerId"] is None

    asyncio.run(call())


def test_schema_validation_errors_never_echo_input(accounts_config: Any, fake_adapter: Any) -> None:
    async def call() -> None:
        raw_profile = "GOC" + "SPX-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
        raw_customer = "sk_" + "live_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
        server = create_mcp(config=accounts_config, adapter=fake_adapter)
        content, structured = _split(
            await server.call_tool(
                "auth_check",
                {"profile": [raw_profile], "customerId": [raw_customer]},
            )
        )
        rendered = json.dumps(structured) + "".join(str(item) for item in content)
        assert raw_profile not in rendered
        assert raw_customer not in rendered
        assert structured["error"]["code"] == "invalid_request"
        assert structured["context"]["profile"] is None
        assert structured["context"]["customerId"] is None

    asyncio.run(call())


def test_every_public_tool_dispatches(accounts_config: Any, fake_adapter: Any) -> None:
    async def call() -> None:
        class FakeOperator:
            @staticmethod
            def response() -> dict[str, Any]:
                return {
                    "status": "ok",
                    "context": {
                        "profile": "test-read-only",
                        "loginCustomerId": "1111111111",
                        "customerId": "2222222222",
                        "currencyCode": "USD",
                        "timeZone": "Etc/UTC",
                        "apiVersion": "v25",
                        "generatedAt": "2026-08-28T00:00:00Z",
                        "contentTrust": "untrusted_data",
                    },
                    "evidence": {
                        "source": "synthetic",
                        "dateFrom": None,
                        "dateTo": None,
                        "dataThrough": None,
                        "partial": False,
                        "limitations": [],
                    },
                    "items": [],
                    "truncated": False,
                }

            async def preview(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
                return self.response()

            async def apply(self, receipt: str) -> dict[str, Any]:
                return self.response()

            async def inspect(self, operation_id: str) -> dict[str, Any]:
                return self.response()

            async def verify(self, operation_id: str) -> dict[str, Any]:
                return self.response()

            async def list(self, profile: str | None, limit: int) -> dict[str, Any]:
                return self.response()

        server = create_mcp(
            config=accounts_config,
            adapter=fake_adapter,
            operator_service=FakeOperator(),
        )
        today = datetime.now(UTC).date().isoformat()
        scoped = {
            "profile": "test-read-only",
            "customerId": "2222222222",
        }
        write_scoped = {**scoped, "policyId": "safe"}
        calls = {
            "auth_check": {"profile": "test-read-only", "customerId": "2222222222"},
            "customers_list": {"profile": "test-read-only"},
            "customer_get": {"profile": "test-read-only", "customerId": "2222222222"},
            "customer_hierarchy": {
                "profile": "test-read-only",
                "customerId": "2222222222",
            },
            "account_health": {"profile": "test-read-only", "customerId": "2222222222"},
            "campaigns_query": {"profile": "test-read-only", "customerId": "2222222222"},
            "campaign_inventory": {
                "profile": "test-read-only",
                "customerId": "2222222222",
                "campaignId": "1",
                "resourceType": "ads",
            },
            "resource_metadata": {
                "profile": "test-read-only",
                "customerId": "2222222222",
                "fieldNames": ["campaign.id"],
            },
            "reports_catalog": {
                "profile": "test-read-only",
                "customerId": "2222222222",
            },
            "report_run": {
                "profile": "test-read-only",
                "customerId": "2222222222",
                "report": "campaign_performance",
                "dateFrom": today,
                "dateTo": today,
            },
            "change_events_query": {
                "profile": "test-read-only",
                "customerId": "2222222222",
                "dateFrom": today,
                "dateTo": today,
            },
            "gaql_validate": {
                "profile": "test-read-only",
                "customerId": "2222222222",
                "query": "SELECT campaign.id FROM campaign LIMIT 1",
            },
            "gaql_search": {
                "profile": "test-read-only",
                "customerId": "2222222222",
                "query": "SELECT campaign.id FROM campaign LIMIT 1",
            },
            "server_info": {},
            "profiles_list": {},
            "campaign_diagnostics": {**scoped, "campaignId": "1"},
            "search_terms_report": {**scoped, "dateFrom": today, "dateTo": today},
            "asset_performance_report": {**scoped, "dateFrom": today, "dateTo": today},
            "conversion_goals_list": scoped,
            "recommendations_list": scoped,
            "keyword_ideas": {
                **scoped,
                "keywords": ["shoes"],
                "languageId": "1000",
                "geoTargetIds": ["2840"],
            },
            "forecast_run": {
                **scoped,
                "keywords": [{"text": "shoes", "matchType": "EXACT"}],
                "languageId": "1000",
                "geoTargetIds": ["2840"],
                "dateFrom": today,
                "dateTo": today,
                "dailyBudgetMicros": "1000000",
                "maxCpcBidMicros": "100000",
            },
            "search_campaign_create_preview": {
                **write_scoped,
                "name": "Search",
                "dailyBudgetMicros": "1000000",
                "adGroupName": "Group",
                "cpcBidMicros": "100000",
                "keywords": ["shoes"],
                "keywordMatchType": "BROAD",
                "finalUrl": "https://example.com",
                "headlines": ["Headline"],
                "descriptions": ["Description"],
                "containsEuPoliticalAdvertising": False,
            },
            "performance_max_campaign_create_preview": {
                **write_scoped,
                "name": "PMax",
                "dailyBudgetMicros": "1000000",
                "assetGroupName": "Assets",
                "finalUrl": "https://example.com",
                "businessName": "Business",
                "headlines": ["Headline"],
                "longHeadlines": ["Long headline"],
                "descriptions": ["Description"],
                "landscapeImageAssets": ["customers/2222222222/assets/1"],
                "squareImageAssets": ["customers/2222222222/assets/2"],
                "logoAssets": ["customers/2222222222/assets/3"],
                "containsEuPoliticalAdvertising": False,
            },
            "app_campaign_create_preview": {
                **write_scoped,
                "name": "App",
                "dailyBudgetMicros": "1000000",
                "targetCpaMicros": "100000",
                "appId": "com.example.app",
                "appStore": "APPLE_APP_STORE",
                "adGroupName": "Group",
                "adName": "Ad",
                "headlines": ["Headline"],
                "descriptions": ["Description"],
                "containsEuPoliticalAdvertising": False,
            },
            "campaign_update_preview": {**write_scoped, "campaignId": "1", "name": "New"},
            "campaign_targeting_preview": {
                **write_scoped,
                "campaignId": "1",
                "geoTargetIds": ["2840"],
                "languageIds": ["1000"],
            },
            "campaign_status_preview": {**write_scoped, "campaignId": "1", "status": "PAUSED"},
            "campaign_budget_preview": {
                **write_scoped,
                "budgetId": "1",
                "dailyBudgetMicros": "1000000",
            },
            "campaign_bidding_preview": {
                **write_scoped,
                "campaignId": "1",
                "strategy": "MANUAL_CPC",
            },
            "ad_group_create_preview": {
                **write_scoped,
                "campaignId": "1",
                "name": "Group",
            },
            "ad_group_update_preview": {
                **write_scoped,
                "adGroupId": "1",
                "name": "Group",
            },
            "keyword_create_preview": {
                **write_scoped,
                "adGroupId": "1",
                "text": "shoes",
                "matchType": "BROAD",
            },
            "negative_keyword_create_preview": {
                **write_scoped,
                "level": "CAMPAIGN",
                "campaignId": "1",
                "text": "free",
                "matchType": "BROAD",
            },
            "criterion_update_preview": {
                **write_scoped,
                "level": "AD_GROUP",
                "adGroupId": "1",
                "criterionId": "2",
                "status": "PAUSED",
            },
            "asset_create_preview": {
                **write_scoped,
                "name": "Text",
                "assetType": "TEXT",
                "text": "Headline",
            },
            "asset_link_preview": {
                **write_scoped,
                "ownerType": "CAMPAIGN",
                "ownerId": "1",
                "assetResourceName": "customers/2222222222/assets/1",
                "fieldType": "HEADLINE",
            },
            "asset_group_create_preview": {
                **write_scoped,
                "campaignId": "1",
                "name": "Group",
                "finalUrl": "https://example.com",
                "assetResourceNames": ["customers/2222222222/assets/1"],
                "fieldTypes": ["HEADLINE"],
            },
            "ad_create_preview": {
                **write_scoped,
                "adGroupId": "1",
                "name": "Ad",
                "adType": "RESPONSIVE_SEARCH_AD",
                "headlines": ["Headline"],
                "descriptions": ["Description"],
                "finalUrl": "https://example.com",
            },
            "recommendation_apply_preview": {
                **write_scoped,
                "recommendationResourceName": "customers/2222222222/recommendations/abc~1",
            },
            "recommendation_dismiss_preview": {
                **write_scoped,
                "recommendationResourceName": "customers/2222222222/recommendations/abc~1",
            },
            "operations_apply": {"receipt": "signed-receipt"},
            "operations_inspect": {"operationId": "op-1"},
            "operations_verify": {"operationId": "op-1"},
            "operations_list": {},
        }
        for tool_name, arguments in calls.items():
            _, structured = _split(await server.call_tool(tool_name, arguments))
            assert structured["status"] in {"ok", "partial"}
        assert len(calls) == 45

    asyncio.run(call())


def test_unexpected_tool_failure_is_constant(accounts_config: Any, fake_adapter: Any) -> None:
    async def call() -> None:
        async def fail(_: Any) -> tuple[str, ...]:
            raise RuntimeError("secret")

        fake_adapter.accessible_customers = fail
        server = create_mcp(config=accounts_config, adapter=fake_adapter)
        _, structured = _split(
            await server.call_tool("customers_list", {"profile": "test-read-only"})
        )
        assert structured["error"]["code"] == "internal_error"
        assert "secret" not in json.dumps(structured)

    asyncio.run(call())


def test_public_request_id_is_preserved_without_private_details() -> None:
    error = PublicError(
        code="google_ads_unavailable",
        message="Google Ads API rejected the read request.",
        request_id="public-request-id",
    )
    response = server_module._error_response(error, "profile", "1234567890")
    assert response["error"]["requestId"] == "public-request-id"
    assert error.as_dict()["error"]["requestId"] == "public-request-id"
    assert response["context"]["profile"] is None
    assert response["context"]["customerId"] is None
    assert "profile" not in json.dumps(response["evidence"])


def test_serve_stdio_delegates_to_stdio(monkeypatch: Any) -> None:
    calls: list[str] = []
    arguments: list[dict[str, Any]] = []
    policy_paths: list[str | None] = []

    class Server:
        def run(self, transport: str) -> None:
            calls.append(transport)

    def create(**kwargs: Any) -> Server:
        arguments.append(kwargs)
        return Server()

    monkeypatch.setattr(server_module, "create_mcp", create)
    monkeypatch.setattr(
        server_module,
        "load_policies",
        lambda path: policy_paths.append(path),
    )
    server_module.serve_stdio(
        "/synthetic/config.json",
        allow_writes=True,
        policy_path="/synthetic/policies.json",
        state_dir_path="/synthetic/state",
    )
    assert calls == ["stdio"]
    assert arguments[0]["allow_writes"] is True
    assert arguments[0]["state_dir"] == Path("/synthetic/state")
    arguments[0]["policy_loader"]()
    assert policy_paths == ["/synthetic/policies.json"]


def test_cancellation_crosses_output_boundary() -> None:
    class Cancellation(BaseException):
        pass

    async def cancel() -> dict[str, Any]:
        raise Cancellation

    with pytest.raises(Cancellation):
        asyncio.run(server_module._call(cancel))


def test_async_tool_cancellation_stops_adapter_work(
    accounts_config: Any, fake_adapter: Any
) -> None:
    async def exercise() -> None:
        started = asyncio.Event()
        cancelled = False

        async def wait_for_cancel(_: Any) -> tuple[str, ...]:
            nonlocal cancelled
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled = True
                raise
            return ()

        fake_adapter.accessible_customers = wait_for_cancel
        server = create_mcp(config=accounts_config, adapter=fake_adapter)
        task = asyncio.create_task(
            server.call_tool("customers_list", {"profile": "test-read-only"})
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled is True

    asyncio.run(exercise())
