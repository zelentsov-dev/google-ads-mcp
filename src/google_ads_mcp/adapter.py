from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

import yaml

from google_ads_mcp.config import ADCAuth, Profile
from google_ads_mcp.constants import (
    API_VERSION,
    MAX_INTERNAL_ROWS,
    MAX_READ_ATTEMPTS,
    REQUEST_DEADLINE_SECONDS,
)
from google_ads_mcp.errors import AdapterError, SecurityError
from google_ads_mcp.models import ForecastKeyword
from google_ads_mcp.normalization import normalize_row
from google_ads_mcp.secrets import SecretStore, SystemSecretStore
from google_ads_mcp.security import read_owner_only_text

_REQUEST_ID = re.compile(r"^[A-Za-z0-9_./=-]{1,128}$")
_SELECT_FIELDS = re.compile(r"^\s*SELECT\s+(.+?)\s+FROM\s+", re.IGNORECASE | re.DOTALL)
_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_.]*$", re.IGNORECASE)
_TRANSIENT_STATUS_NAMES = frozenset({"DEADLINE_EXCEEDED", "RESOURCE_EXHAUSTED", "UNAVAILABLE"})
_NOT_SUPPORTED_CODES = frozenset(
    {
        "BAD_FIELD_NAME",
        "PROHIBITED_FIELD_COMBINATION_IN_SELECT_CLAUSE",
        "PROHIBITED_FIELD_IN_ORDER_BY_CLAUSE",
        "PROHIBITED_FIELD_IN_SELECT_CLAUSE",
        "PROHIBITED_FIELD_IN_WHERE_CLAUSE",
        "PROHIBITED_FIELD_OR_SEGMENT_WITH_METRIC",
        "PROHIBITED_METRIC_IN_SELECT_OR_WHERE_CLAUSE",
        "PROHIBITED_RESOURCE_TYPE_IN_SELECT_CLAUSE",
        "PROHIBITED_RESOURCE_TYPE_IN_FROM_CLAUSE",
        "PROHIBITED_RESOURCE_TYPE_IN_WHERE_CLAUSE",
        "PROHIBITED_SEGMENT_IN_SELECT_OR_WHERE_CLAUSE",
        "PROHIBITED_SEGMENT_WITH_METRIC_IN_SELECT_OR_WHERE_CLAUSE",
        "REQUESTED_METRICS_FOR_MANAGER",
        "REQUIRED_SEGMENT_FIELD_MISSING",
        "UNRECOGNIZED_FIELD",
    }
)


def _silence_google_diagnostics() -> None:
    for name in (
        "google.ads.googleads",
        "google.ads.googleads.client",
        "google.ads.googleads.interceptors",
        "google.ads.googleads.interceptors.logging_interceptor",
    ):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = False
        logger.setLevel(logging.CRITICAL + 1)


@dataclass(frozen=True, slots=True)
class AdapterSearchResult:
    items: tuple[dict[str, Any], ...]
    total_results: int | None
    truncated: bool


class ReadAdapter(Protocol):
    async def search(
        self, profile: Profile, customer_id: str, query: str
    ) -> AdapterSearchResult: ...

    async def accessible_customers(self, profile: Profile) -> tuple[str, ...]: ...

    async def field_metadata(
        self, profile: Profile, field_names: list[str]
    ) -> tuple[dict[str, Any], ...]: ...

    async def validate_query(self, profile: Profile, customer_id: str, query: str) -> None: ...

    async def keyword_ideas(
        self,
        profile: Profile,
        customer_id: str,
        *,
        keywords: tuple[str, ...],
        page_url: str | None,
        language_id: str,
        geo_target_ids: tuple[str, ...],
    ) -> AdapterSearchResult: ...

    async def keyword_forecast(
        self,
        profile: Profile,
        customer_id: str,
        *,
        keywords: tuple[ForecastKeyword, ...],
        language_id: str,
        geo_target_ids: tuple[str, ...],
        date_from: str,
        date_to: str,
        daily_budget_micros: int,
        max_cpc_bid_micros: int,
        currency_code: str,
    ) -> tuple[dict[str, Any], ...]: ...


class GoogleAdsReadAdapter:
    """The only production boundary allowed to construct Google Ads service clients."""

    def __init__(
        self,
        *,
        client_factory: Callable[[Profile], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        secret_store: SecretStore | None = None,
    ) -> None:
        self._clock = clock
        self._sleeper = sleeper
        self._secret_store = secret_store or SystemSecretStore()
        self._client_factory = client_factory or self._build_client_from_store
        self._clients: dict[str, Any] = {}

    def _build_client_from_store(self, profile: Profile) -> Any:
        return self._build_client_with_store(profile, self._secret_store)

    @staticmethod
    def _build_client(profile: Profile) -> Any:
        return GoogleAdsReadAdapter._build_client_with_store(profile, SystemSecretStore())

    @staticmethod
    def _build_client_with_store(profile: Profile, secret_store: SecretStore) -> Any:
        from google.ads.googleads.client import GoogleAdsClient

        _silence_google_diagnostics()
        if isinstance(profile.auth, ADCAuth):
            if profile.auth.developer_token_keyring:
                developer_token = secret_store.get_developer_token(profile.name)
            else:
                env_name = profile.auth.developer_token_env
                developer_token = os.environ.get(env_name) if env_name is not None else None
            if not developer_token:
                source = (
                    "system secure storage"
                    if profile.auth.developer_token_keyring
                    else f"environment variable {profile.auth.developer_token_env}"
                )
                raise SecurityError(
                    f"Developer token source is unavailable: {source}",
                    code="developer_token_missing",
                )
            config: dict[str, Any] = {
                "developer_token": developer_token,
                "use_application_default_credentials": True,
                "use_proto_plus": True,
            }
            if profile.login_customer_id:
                config["login_customer_id"] = profile.login_customer_id
            client = GoogleAdsClient.load_from_dict(config, version=API_VERSION)
        else:
            yaml_text, _ = read_owner_only_text(profile.auth.path, label="Google Ads YAML")
            try:
                parsed = yaml.safe_load(yaml_text)
            except yaml.YAMLError as exc:
                raise SecurityError(
                    "The Google Ads YAML is invalid", code="invalid_google_ads_yaml"
                ) from exc
            if not isinstance(parsed, dict):
                raise SecurityError(
                    "The Google Ads YAML root must be an object",
                    code="invalid_google_ads_yaml",
                )
            config = cast(dict[str, Any], parsed)
            if profile.auth.developer_token_keyring:
                developer_token = secret_store.get_developer_token(profile.name)
                if not developer_token:
                    raise SecurityError(
                        "The configured developer token reference is unavailable",
                        code="developer_token_missing",
                    )
                config["developer_token"] = developer_token
            client = GoogleAdsClient.load_from_dict(config, version=API_VERSION)
            if profile.login_customer_id is not None:
                client.login_customer_id = profile.login_customer_id
        _silence_google_diagnostics()
        return client

    def _client(self, profile: Profile) -> Any:
        client = self._clients.get(profile.name)
        if client is None:
            client = self._client_factory(profile)
            self._clients[profile.name] = client
        return client

    def client_for_write_adapter(self, profile: Profile) -> Any:
        return self._client(profile)

    @classmethod
    def sanitize_google_exception(cls, exc: BaseException) -> AdapterError:
        return cls._sanitize_exception(exc)

    @staticmethod
    def google_request_id(exc: BaseException) -> str | None:
        return GoogleAdsReadAdapter._request_id(exc)

    @staticmethod
    def grpc_status_name(exc: BaseException) -> str | None:
        return GoogleAdsReadAdapter._grpc_status_name(exc)

    @staticmethod
    def _request_id(exc: BaseException) -> str | None:
        raw = getattr(exc, "request_id", None)
        if isinstance(raw, str) and _REQUEST_ID.fullmatch(raw):
            return raw
        return None

    @staticmethod
    def _grpc_status_name(exc: BaseException) -> str | None:
        code_method = getattr(exc, "code", None)
        if callable(code_method):
            try:
                status = code_method()
            except Exception:
                return None
            return getattr(status, "name", None) or str(status).rsplit(".", 1)[-1]
        error = getattr(exc, "error", None)
        nested_code = getattr(error, "code", None)
        if callable(nested_code):
            try:
                status = nested_code()
            except Exception:
                return None
            return getattr(status, "name", None) or str(status).rsplit(".", 1)[-1]
        return None

    @staticmethod
    def _google_error_codes(exc: BaseException) -> set[str]:
        result: set[str] = set()
        failure = getattr(exc, "failure", None)
        for error in getattr(failure, "errors", ()):
            error_code = getattr(error, "error_code", None)
            if error_code is None:
                continue
            raw_code = getattr(error_code, "_pb", error_code)
            descriptor = getattr(raw_code, "DESCRIPTOR", None)
            fields_by_name = getattr(descriptor, "fields_by_name", {})
            for field_name in fields_by_name:
                value = getattr(error_code, field_name, None)
                rendered = getattr(value, "name", None) or str(value).rsplit(".", 1)[-1]
                if rendered and rendered not in {"0", "UNSPECIFIED"}:
                    result.add(rendered)
        return result

    @classmethod
    def _sanitize_exception(cls, exc: BaseException) -> AdapterError:
        codes = cls._google_error_codes(exc)
        request_id = cls._request_id(exc)
        if codes & _NOT_SUPPORTED_CODES:
            return AdapterError(
                "The pinned Google Ads API does not support this resource or field combination.",
                code="not_supported",
                status="not_supported",
                request_id=request_id,
            )
        status_name = cls._grpc_status_name(exc)
        transient = status_name in _TRANSIENT_STATUS_NAMES
        if status_name in {"PERMISSION_DENIED", "UNAUTHENTICATED"}:
            return AdapterError(
                "Google Ads authentication or account authorization failed.",
                code="authorization_failed",
                request_id=request_id,
            )
        if status_name == "RESOURCE_EXHAUSTED":
            return AdapterError(
                "Google Ads quota is temporarily exhausted.",
                code="quota_exhausted",
                request_id=request_id,
                transient=True,
            )
        return AdapterError(
            "Google Ads API request failed.",
            request_id=request_id,
            transient=transient,
        )

    @staticmethod
    async def _close_service(service: Any) -> None:
        transport = getattr(service, "transport", None)
        close = getattr(transport, "close", None)
        if not callable(close):
            return
        result = close()
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _requested_fields(query: str) -> tuple[str, ...]:
        match = _SELECT_FIELDS.search(query)
        if match is None:
            return ()
        fields = tuple(field.strip() for field in match.group(1).split(","))
        return tuple(field for field in fields if _FIELD_NAME.fullmatch(field))

    async def _read(self, operation: Callable[[float], Awaitable[Any]]) -> Any:
        try:
            async with asyncio.timeout(REQUEST_DEADLINE_SECONDS):
                started = self._clock()
                for attempt in range(MAX_READ_ATTEMPTS):
                    remaining = REQUEST_DEADLINE_SECONDS - (self._clock() - started)
                    if remaining <= 0:
                        raise AdapterError(
                            "Google Ads read deadline exceeded.", code="deadline_exceeded"
                        )
                    try:
                        return await operation(remaining)
                    except (SecurityError, AdapterError):
                        raise
                    except Exception as exc:
                        sanitized = self._sanitize_exception(exc)
                        if not sanitized.transient or attempt + 1 >= MAX_READ_ATTEMPTS:
                            raise sanitized from None
                        remaining_after_failure = REQUEST_DEADLINE_SECONDS - (
                            self._clock() - started
                        )
                        if remaining_after_failure <= 0:
                            raise AdapterError(
                                "Google Ads read deadline exceeded.",
                                code="deadline_exceeded",
                            ) from None
                        delay = min(0.25 * (2**attempt), remaining_after_failure)
                        await self._sleeper(delay)
        except TimeoutError:
            raise AdapterError(
                "Google Ads read deadline exceeded.", code="deadline_exceeded"
            ) from None
        raise AssertionError("unreachable")

    async def search(self, profile: Profile, customer_id: str, query: str) -> AdapterSearchResult:
        requested_fields = self._requested_fields(query)

        async def operation(remaining_seconds: float) -> AdapterSearchResult:
            service = self._client(profile).get_service(
                "GoogleAdsService", version=API_VERSION, is_async=True
            )
            try:
                pager = await service.search(
                    customer_id=customer_id,
                    query=query,
                    retry=None,
                    timeout=remaining_seconds,
                )
                items: list[dict[str, Any]] = []
                async for row in pager:
                    if len(items) >= MAX_INTERNAL_ROWS:
                        break
                    items.append(normalize_row(row, requested_fields=requested_fields))
                total_raw = getattr(pager, "total_results_count", None)
                total_results = int(total_raw) if total_raw is not None else None
                truncated = len(items) >= MAX_INTERNAL_ROWS or (
                    total_results is not None and total_results > len(items)
                )
                return AdapterSearchResult(tuple(items), total_results, truncated)
            finally:
                await self._close_service(service)

        return cast(AdapterSearchResult, await self._read(operation))

    async def accessible_customers(self, profile: Profile) -> tuple[str, ...]:
        async def operation(remaining_seconds: float) -> tuple[str, ...]:
            service = self._client(profile).get_service(
                "CustomerService", version=API_VERSION, is_async=True
            )
            try:
                response = await service.list_accessible_customers(
                    retry=None, timeout=remaining_seconds
                )
                return tuple(str(name) for name in response.resource_names)
            finally:
                await self._close_service(service)

        return cast(tuple[str, ...], await self._read(operation))

    async def field_metadata(
        self, profile: Profile, field_names: list[str]
    ) -> tuple[dict[str, Any], ...]:
        if not field_names or len(field_names) > 200:
            raise SecurityError("fieldNames must contain between 1 and 200 entries")
        for field_name in field_names:
            if not re.fullmatch(r"[a-z][a-z0-9_.]{0,127}", field_name):
                raise SecurityError("Invalid field metadata name")
        quoted = ", ".join(f"'{field_name}'" for field_name in field_names)

        async def operation(remaining_seconds: float) -> tuple[dict[str, Any], ...]:
            service = self._client(profile).get_service(
                "GoogleAdsFieldService", version=API_VERSION, is_async=True
            )
            try:
                response = await service.search_google_ads_fields(
                    query=(
                        "SELECT name, category, data_type, selectable, selectable_with, "
                        "filterable, sortable "
                        f"WHERE name IN ({quoted})"
                    ),
                    retry=None,
                    timeout=remaining_seconds,
                )
                return tuple([normalize_row(item) async for item in response])
            finally:
                await self._close_service(service)

        return cast(tuple[dict[str, Any], ...], await self._read(operation))

    async def validate_query(self, profile: Profile, customer_id: str, query: str) -> None:
        async def operation(remaining_seconds: float) -> None:
            service = self._client(profile).get_service(
                "GoogleAdsService", version=API_VERSION, is_async=True
            )
            try:
                await service.search(
                    request={
                        "customer_id": customer_id,
                        "query": query,
                        "validate_only": True,
                    },
                    retry=None,
                    timeout=remaining_seconds,
                )
            finally:
                await self._close_service(service)

        await self._read(operation)

    async def keyword_ideas(
        self,
        profile: Profile,
        customer_id: str,
        *,
        keywords: tuple[str, ...],
        page_url: str | None,
        language_id: str,
        geo_target_ids: tuple[str, ...],
    ) -> AdapterSearchResult:
        async def operation(remaining_seconds: float) -> AdapterSearchResult:
            client = self._client(profile)
            service = client.get_service(
                "KeywordPlanIdeaService", version=API_VERSION, is_async=True
            )
            try:
                request = client.get_type("GenerateKeywordIdeasRequest", version=API_VERSION)
                request.customer_id = customer_id
                request.language = f"languageConstants/{language_id}"
                request.geo_target_constants.extend(
                    f"geoTargetConstants/{target_id}" for target_id in geo_target_ids
                )
                request.include_adult_keywords = False
                request.page_size = 200
                request.keyword_plan_network = (
                    client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH_AND_PARTNERS
                )
                if page_url is not None and keywords:
                    request.keyword_and_url_seed.url = page_url
                    request.keyword_and_url_seed.keywords.extend(keywords)
                elif page_url is not None:
                    request.url_seed.url = page_url
                else:
                    request.keyword_seed.keywords.extend(keywords)
                pager = await service.generate_keyword_ideas(
                    request=request,
                    retry=None,
                    timeout=remaining_seconds,
                )
                result: list[dict[str, Any]] = []
                truncated = False
                async for item in pager:
                    if len(result) >= 200:
                        truncated = True
                        break
                    result.append(normalize_row(item))
                return AdapterSearchResult(tuple(result), None, truncated)
            finally:
                await self._close_service(service)

        return cast(AdapterSearchResult, await self._read(operation))

    async def keyword_forecast(
        self,
        profile: Profile,
        customer_id: str,
        *,
        keywords: tuple[ForecastKeyword, ...],
        language_id: str,
        geo_target_ids: tuple[str, ...],
        date_from: str,
        date_to: str,
        daily_budget_micros: int,
        max_cpc_bid_micros: int,
        currency_code: str,
    ) -> tuple[dict[str, Any], ...]:
        async def operation(remaining_seconds: float) -> tuple[dict[str, Any], ...]:
            client = self._client(profile)
            service = client.get_service(
                "KeywordPlanIdeaService", version=API_VERSION, is_async=True
            )
            try:
                request = client.get_type(
                    "GenerateKeywordForecastMetricsRequest", version=API_VERSION
                )
                request.customer_id = customer_id
                request.currency_code = currency_code
                request.forecast_period.start_date = date_from
                request.forecast_period.end_date = date_to
                request.campaign.language_constants.append(f"languageConstants/{language_id}")
                request.campaign.geo_target_constants.extend(
                    f"geoTargetConstants/{target_id}" for target_id in geo_target_ids
                )
                strategy = request.campaign.bidding_strategy.manual_cpc_bidding_strategy
                strategy.daily_budget_micros = daily_budget_micros
                strategy.max_cpc_bid_micros = max_cpc_bid_micros
                ad_group = client.get_type("ForecastAdGroup", version=API_VERSION)
                for keyword in keywords:
                    ad_group.keywords.append(
                        {"text": keyword["text"], "match_type": keyword["matchType"]}
                    )
                request.campaign.ad_groups.append(ad_group)
                response = await service.generate_keyword_forecast_metrics(
                    request=request,
                    retry=None,
                    timeout=remaining_seconds,
                )
                return (normalize_row(response.campaign_forecast_metrics),)
            finally:
                await self._close_service(service)

        return cast(tuple[dict[str, Any], ...], await self._read(operation))
