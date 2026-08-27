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
        expected = json.loads(Path("api-contract/tools-v0.1.json").read_text())["tools"]
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
        assert all(
            "customerId" in tool.inputSchema.get("properties", {})
            for tool in tools
            if tool.name != "customers_list"
        )
        assert (
            "customerId"
            not in next(tool for tool in tools if tool.name == "customers_list").inputSchema[
                "properties"
            ]
        )

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


def test_credential_shaped_identifiers_never_echo(
    accounts_config: Any, fake_adapter: Any
) -> None:
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


def test_schema_validation_errors_never_echo_input(
    accounts_config: Any, fake_adapter: Any
) -> None:
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
        server = create_mcp(config=accounts_config, adapter=fake_adapter)
        today = datetime.now(UTC).date().isoformat()
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
        }
        for tool_name, arguments in calls.items():
            _, structured = _split(await server.call_tool(tool_name, arguments))
            assert structured["status"] in {"ok", "partial"}

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

    class Server:
        def run(self, transport: str) -> None:
            calls.append(transport)

    monkeypatch.setattr(server_module, "create_mcp", lambda config_path=None: Server())
    server_module.serve_stdio("/synthetic/config.json")
    assert calls == ["stdio"]


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
