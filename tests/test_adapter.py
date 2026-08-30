from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from google.ads.googleads import client as google_client_module
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.v25.errors.types.errors import (
    ErrorCode,
    GoogleAdsError,
    GoogleAdsFailure,
)
from google.ads.googleads.v25.errors.types.query_error import QueryErrorEnum
from google.ads.googleads.v25.services.services.google_ads_field_service.async_client import (
    GoogleAdsFieldServiceAsyncClient,
)
from google.ads.googleads.v25.services.services.google_ads_service.async_client import (
    GoogleAdsServiceAsyncClient,
)
from google.auth.credentials import AnonymousCredentials

from google_ads_mcp import adapter as adapter_module
from google_ads_mcp.adapter import GoogleAdsReadAdapter
from google_ads_mcp.config import ADCAuth, GoogleAdsYamlAuth, Profile
from google_ads_mcp.errors import AdapterError, SecurityError


def _profile(auth: ADCAuth | GoogleAdsYamlAuth | None = None) -> Profile:
    return Profile(
        name="test",
        login_customer_id="1111111111",
        default_customer_id="2222222222",
        auth=auth or ADCAuth(type="adc", developer_token_env="TEST_TOKEN"),
        allow_writes=False,
    )


class Pager:
    def __init__(self, rows: list[dict[str, Any]], total: int | None = None) -> None:
        self.rows = rows
        self.total_results_count = total

    def __aiter__(self):
        async def rows():
            for row in self.rows:
                yield row

        return rows()


class Transport:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class Service:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []
        self.transport = Transport()

    async def search(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response

    async def list_accessible_customers(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response

    async def search_google_ads_fields(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class Client:
    def __init__(self, services: dict[str, Service]) -> None:
        self.services = services
        self.requested: list[str] = []

    def get_service(self, name: str, version: str, *, is_async: bool) -> Service:
        self.requested.append(name)
        assert version == "v25"
        assert is_async is True
        return self.services[name]


def test_search_normalizes_and_truncates_without_unsupported_page_size() -> None:
    pager = Pager([{"campaign": {"id": 1, "name": "safe"}}], total=2)
    service = Service(pager)
    client = Client({"GoogleAdsService": service})
    adapter = GoogleAdsReadAdapter(client_factory=lambda _: client)
    result = asyncio.run(
        adapter.search(_profile(), "2222222222", "SELECT campaign.id FROM campaign LIMIT 1")
    )
    assert result.items[0]["campaign"]["id"] == "1"
    assert result.total_results == 2
    assert result.truncated
    assert client.requested == ["GoogleAdsService"]
    assert "page_size" not in service.calls[0]
    assert service.calls[0]["retry"] is None
    assert service.transport.closed


def test_pinned_v25_search_signatures_do_not_offer_page_size_keyword() -> None:
    assert "page_size" not in inspect.signature(GoogleAdsServiceAsyncClient.search).parameters
    assert (
        "page_size"
        not in inspect.signature(
            GoogleAdsFieldServiceAsyncClient.search_google_ads_fields
        ).parameters
    )


def test_search_marks_exact_internal_row_limit_as_truncated() -> None:
    pager = Pager([{"campaign": {"id": index}} for index in range(1_000)])
    client = Client({"GoogleAdsService": Service(pager)})
    adapter = GoogleAdsReadAdapter(client_factory=lambda _: client)
    result = asyncio.run(
        adapter.search(_profile(), "2222222222", "SELECT campaign.id FROM campaign LIMIT 1000")
    )
    assert len(result.items) == 1_000
    assert result.truncated


def test_accessible_customers_metadata_and_query_validation() -> None:
    customers = type("Customers", (), {"resource_names": ["customers/1"]})()
    customer_service = Service(customers)
    metadata_service = Service(Pager([{"name": "campaign.id"}]))
    query_service = Service(Pager([]))
    client = Client(
        {
            "CustomerService": customer_service,
            "GoogleAdsFieldService": metadata_service,
            "GoogleAdsService": query_service,
        }
    )
    adapter = GoogleAdsReadAdapter(client_factory=lambda _: client)
    assert asyncio.run(adapter.accessible_customers(_profile())) == ("customers/1",)
    metadata = asyncio.run(adapter.field_metadata(_profile(), ["campaign.id"]))
    assert metadata[0]["name"] == "campaign.id"
    asyncio.run(
        adapter.validate_query(_profile(), "2222222222", "SELECT campaign.id FROM campaign LIMIT 1")
    )
    assert query_service.calls[0]["request"]["validate_only"] is True
    assert "page_size" not in metadata_service.calls[0]
    with pytest.raises(SecurityError):
        asyncio.run(adapter.field_metadata(_profile(), []))
    with pytest.raises(SecurityError):
        asyncio.run(adapter.field_metadata(_profile(), ["bad field"]))


class Status:
    def __init__(self, name: str) -> None:
        self.name = name


class RpcFailureError(Exception):
    def __init__(self, status: str, request_id: str = "request-1") -> None:
        super().__init__("secret query must not escape")
        self._status = status
        self.request_id = request_id

    def code(self) -> Status:
        return Status(self._status)


def test_transient_retry_then_success() -> None:
    calls = [0]
    pager = Pager([{"customer": {"id": 1}}], total=1)

    class RetryingService(Service):
        async def search(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            calls[0] += 1
            if calls[0] == 1:
                raise RpcFailureError("UNAVAILABLE")
            return pager

    client = Client({"GoogleAdsService": RetryingService(pager)})
    delays: list[float] = []

    async def record_delay(delay: float) -> None:
        delays.append(delay)

    adapter = GoogleAdsReadAdapter(client_factory=lambda _: client, sleeper=record_delay)
    result = asyncio.run(
        adapter.search(_profile(), "2222222222", "SELECT customer.id FROM customer LIMIT 1")
    )
    assert result.items
    assert calls[0] == 2
    assert delays == [0.25]


@pytest.mark.parametrize(
    ("status", "code", "transient"),
    [
        ("PERMISSION_DENIED", "authorization_failed", False),
        ("UNAUTHENTICATED", "authorization_failed", False),
        ("RESOURCE_EXHAUSTED", "quota_exhausted", True),
        ("INVALID_ARGUMENT", "google_ads_unavailable", False),
    ],
)
def test_errors_are_sanitized(status: str, code: str, transient: bool) -> None:
    client = Client({"GoogleAdsService": Service(RpcFailureError(status))})
    adapter = GoogleAdsReadAdapter(client_factory=lambda _: client)
    with pytest.raises(AdapterError) as caught:
        asyncio.run(
            adapter.search(_profile(), "2222222222", "SELECT customer.id FROM customer LIMIT 1")
        )
    assert caught.value.code == code
    assert caught.value.transient is transient
    assert "secret" not in caught.value.message
    assert caught.value.request_id == "request-1"


def test_deadline_and_invalid_request_id() -> None:
    times = iter([0.0, 31.0])
    adapter = GoogleAdsReadAdapter(client_factory=lambda _: None, clock=lambda: next(times))
    with pytest.raises(AdapterError, match="deadline"):
        asyncio.run(
            adapter.search(_profile(), "2222222222", "SELECT customer.id FROM customer LIMIT 1")
        )
    assert GoogleAdsReadAdapter._request_id(RpcFailureError("BAD", "space id")) is None


def test_retry_sleep_cannot_cross_total_deadline() -> None:
    times = iter([0.0, 0.0, 29.9, 30.1])
    client = Client({"GoogleAdsService": Service(RpcFailureError("UNAVAILABLE"))})
    delays: list[float] = []

    async def advance_clock(delay: float) -> None:
        delays.append(delay)

    adapter = GoogleAdsReadAdapter(
        client_factory=lambda _: client,
        clock=lambda: next(times),
        sleeper=advance_clock,
    )
    with pytest.raises(AdapterError, match="deadline"):
        asyncio.run(
            adapter.search(_profile(), "2222222222", "SELECT customer.id FROM customer LIMIT 1")
        )
    assert delays == [pytest.approx(0.1)]


def test_asyncio_hard_deadline_bounds_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    client = Client({"GoogleAdsService": Service(RpcFailureError("UNAVAILABLE"))})

    async def blocked_sleep(_: float) -> None:
        await asyncio.sleep(60)

    monkeypatch.setattr(adapter_module, "REQUEST_DEADLINE_SECONDS", 0.01)
    adapter = GoogleAdsReadAdapter(client_factory=lambda _: client, sleeper=blocked_sleep)
    with pytest.raises(AdapterError, match="deadline"):
        asyncio.run(
            adapter.search(_profile(), "2222222222", "SELECT customer.id FROM customer LIMIT 1")
        )


def test_adc_requires_named_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_TOKEN", raising=False)
    with pytest.raises(SecurityError, match="TEST_TOKEN"):
        GoogleAdsReadAdapter._build_client(_profile())


def test_client_cache() -> None:
    client = Client({"CustomerService": Service(type("R", (), {"resource_names": []})())})
    calls: list[str] = []
    adapter = GoogleAdsReadAdapter(
        client_factory=lambda profile: calls.append(profile.name) or client
    )
    asyncio.run(adapter.accessible_customers(_profile()))
    asyncio.run(adapter.accessible_customers(_profile()))
    assert calls == ["test"]


def test_adc_and_yaml_client_providers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loaded: list[tuple[str, Any, str | None]] = []
    monkeypatch.setenv("TEST_TOKEN", "synthetic-local-token")
    monkeypatch.setattr(
        GoogleAdsClient,
        "load_from_dict",
        lambda config, version=None: loaded.append(("dict", config, version)) or "adc-client",
    )
    assert GoogleAdsReadAdapter._build_client(_profile()) == "adc-client"
    assert loaded[0][1]["developer_token"] == "synthetic-local-token"
    yaml = tmp_path / "google-ads.yaml"
    yaml.write_text("synthetic: true")
    yaml.chmod(0o600)
    yaml_client = type("YamlClient", (), {"login_customer_id": None})()
    monkeypatch.setattr(
        GoogleAdsClient,
        "load_from_dict",
        lambda config, version=None: loaded.append(("dict", config, version)) or yaml_client,
    )
    yaml_auth = GoogleAdsYamlAuth(type="googleAdsYaml", path=yaml)
    assert GoogleAdsReadAdapter._build_client(_profile(yaml_auth)) is yaml_client
    assert loaded[-1] == ("dict", {"synthetic": True}, "v25")
    assert yaml_client.login_customer_id == "1111111111"


def test_adc_and_yaml_keyring_token_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Secrets:
        def __init__(self, value: str | None) -> None:
            self.value = value

        def get_developer_token(self, profile: str) -> str | None:
            assert profile == "test"
            return self.value

        def set_developer_token(self, profile: str, token: str) -> None:
            raise AssertionError("not used")

    loaded: list[dict[str, Any]] = []
    monkeypatch.setattr(
        GoogleAdsClient,
        "load_from_dict",
        lambda config, version=None: loaded.append(config) or "adc-client",
    )
    adc = ADCAuth(type="adc", developer_token_env=None, developer_token_keyring=True)
    assert (
        GoogleAdsReadAdapter._build_client_with_store(_profile(adc), Secrets("keyring-token"))
        == "adc-client"
    )
    assert loaded[0]["developer_token"] == "keyring-token"
    with pytest.raises(SecurityError, match="secure storage"):
        GoogleAdsReadAdapter._build_client_with_store(_profile(adc), Secrets(None))

    yaml = tmp_path / "google-ads.yaml"
    yaml.write_text("synthetic: true")
    yaml.chmod(0o600)
    yaml_client = SimpleNamespace(developer_token="old", login_customer_id=None)
    monkeypatch.setattr(
        GoogleAdsClient,
        "load_from_dict",
        lambda config, version=None: loaded.append(config) or yaml_client,
    )
    yaml_auth = GoogleAdsYamlAuth(
        type="googleAdsYaml", path=yaml, developer_token_keyring=True
    )
    result = GoogleAdsReadAdapter._build_client_with_store(
        _profile(yaml_auth), Secrets("keyring-token")
    )
    assert result is yaml_client
    assert result.login_customer_id == "1111111111"
    assert loaded[-1]["developer_token"] == "keyring-token"


def test_real_google_ads_client_accepts_yaml_with_keyring_token_injected_before_load(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Secrets:
        def get_developer_token(self, profile: str) -> str | None:
            return "keyring-token"

        def set_developer_token(self, profile: str, token: str) -> None:
            raise AssertionError("not used")

    yaml = tmp_path / "google-ads.yaml"
    yaml.write_text(
        "client_id: synthetic-client\n"
        "client_secret: synthetic-secret\n"
        "refresh_token: synthetic-refresh\n"
        "use_proto_plus: true\n"
    )
    yaml.chmod(0o600)
    monkeypatch.setattr(
        google_client_module.oauth2,
        "get_credentials",
        lambda config: AnonymousCredentials(),
    )
    auth = GoogleAdsYamlAuth(type="googleAdsYaml", path=yaml, developer_token_keyring=True)
    client = GoogleAdsReadAdapter._build_client_with_store(_profile(auth), Secrets())
    assert isinstance(client, GoogleAdsClient)
    assert client.developer_token == "keyring-token"
    assert client.login_customer_id == "1111111111"


@pytest.mark.parametrize(
    "query_error",
    [
        QueryErrorEnum.QueryError.UNRECOGNIZED_FIELD,
        QueryErrorEnum.QueryError.PROHIBITED_METRIC_IN_SELECT_OR_WHERE_CLAUSE,
    ],
)
def test_real_v25_query_error_maps_to_not_supported(
    query_error: QueryErrorEnum.QueryError,
) -> None:
    failure = RpcFailureError("INVALID_ARGUMENT")
    failure.failure = GoogleAdsFailure(
        errors=[
            GoogleAdsError(
                error_code=ErrorCode(query_error=query_error),
                message="secret query details",
            )
        ]
    )
    sanitized = GoogleAdsReadAdapter._sanitize_exception(failure)
    assert sanitized.status == "not_supported"
    assert sanitized.code == "not_supported"
    assert "secret" not in sanitized.message


def test_malformed_google_error_envelope_is_still_sanitized() -> None:
    malformed = RpcFailureError("INVALID_ARGUMENT")
    malformed.failure = type(
        "Failure", (), {"errors": [type("Error", (), {"error_code": object()})()]}
    )()
    sanitized = GoogleAdsReadAdapter._sanitize_exception(malformed)
    assert sanitized.code == "google_ads_unavailable"
    assert "secret" not in sanitized.message


def test_cancellation_is_not_converted_or_retried() -> None:
    class Cancellation(BaseException):
        pass

    cancellation = Cancellation()
    client = Client({"GoogleAdsService": Service(cancellation)})
    adapter = GoogleAdsReadAdapter(client_factory=lambda _: client)
    with pytest.raises(Cancellation):
        asyncio.run(
            adapter.search(_profile(), "2222222222", "SELECT campaign.id FROM campaign LIMIT 1")
        )


def test_keyword_ideas_and_forecast_build_real_v25_requests() -> None:
    class KeywordService:
        def __init__(self) -> None:
            self.idea_requests: list[Any] = []
            self.forecast_requests: list[Any] = []
            self.idea_rows = [{"text": "idea"}]
            self.transport = Transport()

        async def generate_keyword_ideas(self, **kwargs: Any) -> Pager:
            self.idea_requests.append(kwargs["request"])
            return Pager(self.idea_rows)

        async def generate_keyword_forecast_metrics(self, **kwargs: Any) -> Any:
            self.forecast_requests.append(kwargs["request"])
            return SimpleNamespace(campaign_forecast_metrics={"clicks": 1.0})

    class KeywordClient:
        def __init__(self, service: KeywordService) -> None:
            self.real = GoogleAdsClient(
                AnonymousCredentials(), developer_token="synthetic", use_proto_plus=True
            )
            self.enums = self.real.enums
            self.service = service

        def get_type(self, *args: Any, **kwargs: Any) -> Any:
            return self.real.get_type(*args, **kwargs)

        def get_service(self, name: str, version: str, *, is_async: bool) -> KeywordService:
            assert name == "KeywordPlanIdeaService"
            assert version == "v25"
            assert is_async is True
            return self.service

    service = KeywordService()
    client = KeywordClient(service)
    adapter = GoogleAdsReadAdapter(client_factory=lambda _: client)
    ideas = asyncio.run(
        adapter.keyword_ideas(
            _profile(),
            "2222222222",
            keywords=("shoes",),
            page_url="https://example.com",
            language_id="1000",
            geo_target_ids=("2840",),
        )
    )
    assert ideas.items == ({"text": "idea"},)
    assert ideas.truncated is False
    idea_request = service.idea_requests[0]
    assert idea_request.keyword_and_url_seed.keywords == ["shoes"]
    assert idea_request.keyword_and_url_seed.url == "https://example.com"

    service.idea_rows = [{"text": f"idea-{index}"} for index in range(201)]
    truncated_ideas = asyncio.run(
        adapter.keyword_ideas(
            _profile(),
            "2222222222",
            keywords=("shoes",),
            page_url=None,
            language_id="1000",
            geo_target_ids=("2840",),
        )
    )
    assert len(truncated_ideas.items) == 200
    assert truncated_ideas.truncated is True

    forecast = asyncio.run(
        adapter.keyword_forecast(
            _profile(),
            "2222222222",
            keywords=(
                {"text": "shoes", "matchType": "EXACT"},
                {"text": "boots", "matchType": "PHRASE"},
            ),
            language_id="1000",
            geo_target_ids=("2840",),
            date_from="2026-01-01",
            date_to="2026-01-31",
            daily_budget_micros=1_000_000,
            max_cpc_bid_micros=100_000,
            currency_code="USD",
        )
    )
    assert forecast == ({"clicks": 1.0},)
    forecast_request = service.forecast_requests[0]
    assert forecast_request.currency_code == "USD"
    assert forecast_request.campaign.ad_groups[0].keywords[0].text == "shoes"
    assert forecast_request.campaign.ad_groups[0].keywords[0].match_type.name == "EXACT"
    assert forecast_request.campaign.ad_groups[0].keywords[1].text == "boots"
    assert forecast_request.campaign.ad_groups[0].keywords[1].match_type.name == "PHRASE"
