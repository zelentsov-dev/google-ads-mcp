from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest
from google.ads.googleads.client import GoogleAdsClient
from google.auth.credentials import AnonymousCredentials

from google_ads_mcp import write_adapter as write_adapter_module
from google_ads_mcp.config import ADCAuth, Profile
from google_ads_mcp.errors import AdapterError, SecurityError
from google_ads_mcp.write_adapter import (
    ALLOWED_WRITE_SERVICES,
    FORBIDDEN_WRITE_SERVICES,
    GoogleAdsWriteAdapter,
)
from google_ads_mcp.write_models import MutationItem, WritePlan


def _profile() -> Profile:
    return Profile(
        name="operator",
        login_customer_id=None,
        default_customer_id="1234567890",
        auth=ADCAuth(type="adc", developer_token_env="TOKEN"),
        allow_writes=True,
    )


def _plan(items: tuple[MutationItem, ...] | None = None) -> WritePlan:
    return WritePlan(
        profile="operator",
        customer_id="1234567890",
        policy_id="safe",
        kind="campaign_status",
        related_key="campaign:1",
        before={},
        after={"status": "PAUSED"},
        items=items
        or (
            MutationItem(
                "campaign_operation",
                "update",
                {"resource_name": "customers/1234567890/campaigns/1", "status": "PAUSED"},
                ("status",),
                "campaign",
            ),
        ),
        readback_queries=("SELECT campaign.id FROM campaign LIMIT 1",),
        monetary_delta={"spendMicros": "0"},
        risk={"spendAffecting": False},
    )


class Transport:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class Service:
    def __init__(self, response: Any = None, error: BaseException | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []
        self.transport = Transport()

    async def mutate(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class Client:
    def __init__(self, service: Service) -> None:
        self.real = GoogleAdsClient(
            AnonymousCredentials(), developer_token="synthetic", use_proto_plus=True
        )
        self.enums = self.real.enums
        self.service = service

    def get_type(self, *args: Any, **kwargs: Any) -> Any:
        return self.real.get_type(*args, **kwargs)

    def copy_from(self, *args: Any, **kwargs: Any) -> Any:
        return self.real.copy_from(*args, **kwargs)

    def get_service(self, name: str, **kwargs: Any) -> Service:
        assert name in ALLOWED_WRITE_SERVICES
        assert kwargs["is_async"] is True
        return self.service


class ReadBoundary:
    def __init__(self, client: Client) -> None:
        self.client = client

    def client_for_write_adapter(self, _: Profile) -> Client:
        return self.client


def _response(*resource_names: str) -> Any:
    client = GoogleAdsClient(
        AnonymousCredentials(), developer_token="synthetic", use_proto_plus=True
    )
    response = client.get_type("MutateGoogleAdsResponse", version="v25")
    for name in resource_names:
        item = client.get_type("MutateOperationResponse", version="v25")
        item.campaign_result.resource_name = name
        response.mutate_operation_responses.append(item)
    return response


def test_validate_and_apply_build_real_v25_protobuf() -> None:
    service = Service(_response("customers/1234567890/campaigns/1"))
    adapter = GoogleAdsWriteAdapter(read_adapter=cast(Any, ReadBoundary(Client(service))))
    validation = asyncio.run(adapter.validate(_profile(), _plan()))
    assert validation.valid is True
    assert service.calls[0]["request"].validate_only is True
    assert service.calls[0]["request"].partial_failure is False
    result = asyncio.run(adapter.apply(_profile(), _plan()))
    assert result.state == "applied"
    assert result.items[0].resource_name == "customers/1234567890/campaigns/1"
    assert service.calls[1]["request"].validate_only is False
    assert all(call["retry"] is None for call in service.calls)
    assert service.transport.closed is True


def test_partial_and_failed_results_are_item_level() -> None:
    first = _plan().items[0]
    second = replace(first, correlation_id="campaign-2")
    plan = replace(_plan((first, second)), partial_failure=True, independent_items=True)
    service = Service(_response("customers/1234567890/campaigns/1"))
    adapter = GoogleAdsWriteAdapter(read_adapter=cast(Any, ReadBoundary(Client(service))))
    result = asyncio.run(adapter.apply(_profile(), plan))
    assert result.state == "partial"
    assert [item.status for item in result.items] == ["applied", "failed"]
    service.response = _response()
    failed = asyncio.run(adapter.apply(_profile(), _plan()))
    assert failed.state == "failed"


@pytest.mark.parametrize(
    "plan",
    [
        replace(_plan(), items=()),
        replace(_plan(), items=_plan().items * 101),
        replace(_plan(), partial_failure=True),
        _plan((MutationItem("forbidden_operation", "create", {}),)),
        _plan((MutationItem("campaign_operation", "create", {"forbidden": True}),)),
        _plan(
            (
                MutationItem(
                    "campaign_operation",
                    cast(Any, "remove"),
                    {"resource_name": "customers/1234567890/campaigns/1", "name": "bad"},
                ),
            )
        ),
        _plan(
            (
                MutationItem(
                    "campaign_operation",
                    "create",
                    {"name": "bad"},
                    ("name",),
                ),
            )
        ),
        _plan(
            (
                MutationItem(
                    "campaign_operation",
                    "update",
                    {"resource_name": "customers/1234567890/campaigns/1"},
                ),
            )
        ),
    ],
)
def test_invalid_internal_plans_fail_before_rpc(plan: WritePlan) -> None:
    with pytest.raises(SecurityError):
        GoogleAdsWriteAdapter._validate_plan(plan)


def test_recommendations_fail_closed_without_validate_only() -> None:
    plan = replace(_plan(), kind="recommendation_apply")
    with pytest.raises(AdapterError) as error:
        GoogleAdsWriteAdapter._validate_plan(plan)
    assert error.value.status == "not_supported"


class Status:
    def __init__(self, name: str) -> None:
        self.name = name


class RpcError(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name
        self.request_id = "request-1"

    def code(self) -> Status:
        return Status(self.name)


@pytest.mark.parametrize(
    ("status", "code"),
    [
        ("DEADLINE_EXCEEDED", "ambiguous_write"),
        ("UNAVAILABLE", "ambiguous_write"),
        ("INVALID_ARGUMENT", "google_ads_unavailable"),
    ],
)
def test_write_errors_are_never_retried(status: str, code: str) -> None:
    service = Service(error=RpcError(status))
    adapter = GoogleAdsWriteAdapter(read_adapter=cast(Any, ReadBoundary(Client(service))))
    with pytest.raises(AdapterError) as error:
        asyncio.run(adapter.apply(_profile(), _plan()))
    assert error.value.code == code
    assert error.value.transient is False
    assert len(service.calls) == 1


def test_forbidden_service_set_is_disjoint() -> None:
    assert ALLOWED_WRITE_SERVICES.isdisjoint(FORBIDDEN_WRITE_SERVICES)
    assert "BillingSetupService" in FORBIDDEN_WRITE_SERVICES


def test_validate_error_unsupported_action_and_blank_partial_response() -> None:
    failed_validation = Service(error=RpcError("INVALID_ARGUMENT"))
    adapter = GoogleAdsWriteAdapter(
        read_adapter=cast(Any, ReadBoundary(Client(failed_validation)))
    )
    with pytest.raises(AdapterError):
        asyncio.run(adapter.validate(_profile(), _plan()))
    assert len(failed_validation.calls) == 1

    remove = MutationItem(
        "campaign_operation",
        cast(Any, "remove"),
        {"resource_name": "customers/1234567890/campaigns/1"},
    )
    with pytest.raises(SecurityError, match="Remove"):
        GoogleAdsWriteAdapter._validate_plan(_plan((remove,)))

    blank = _response("")
    service = Service(blank)
    adapter = GoogleAdsWriteAdapter(read_adapter=cast(Any, ReadBoundary(Client(service))))
    result = asyncio.run(adapter.apply(_profile(), _plan()))
    assert result.state == "failed"
    assert result.items[0].error_code == "partial_failure"


def test_close_variants_and_local_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[bool] = []

    class SyncTransport:
        @staticmethod
        def close() -> None:
            closed.append(True)

    asyncio.run(GoogleAdsWriteAdapter._close(SimpleNamespace(transport=object())))
    asyncio.run(GoogleAdsWriteAdapter._close(SimpleNamespace(transport=SyncTransport())))
    assert closed == [True]

    class TimedOut:
        async def __aenter__(self) -> None:
            raise TimeoutError

        async def __aexit__(self, *args: Any) -> None:
            return None

    monkeypatch.setattr(write_adapter_module.asyncio, "timeout", lambda _: TimedOut())
    service = Service(_response())
    adapter = GoogleAdsWriteAdapter(read_adapter=cast(Any, ReadBoundary(Client(service))))
    with pytest.raises(AdapterError) as error:
        asyncio.run(adapter.apply(_profile(), _plan()))
    assert error.value.code == "ambiguous_write"
    assert service.calls == []
