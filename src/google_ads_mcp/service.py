from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from google_ads_mcp.adapter import AdapterSearchResult, ReadAdapter
from google_ads_mcp.config import AccountsConfig, Profile, normalize_customer_id
from google_ads_mcp.constants import MAX_CHANGE_EVENT_DAYS, MAX_PAGE_ITEMS
from google_ads_mcp.cursors import CursorStore
from google_ads_mcp.dates import validate_date_range
from google_ads_mcp.errors import AdapterError, ValidationError
from google_ads_mcp.gaql import validate_gaql
from google_ads_mcp.models import ResponseEnvelope, Status, ToolResponse
from google_ads_mcp.reports import PINNED_CAMPAIGN_TYPES, build_report_query, reports_catalog
from google_ads_mcp.security import redact_untrusted, stable_fingerprint

_CAMPAIGN_STATUSES = frozenset({"ENABLED", "PAUSED", "REMOVED", "UNKNOWN", "UNSPECIFIED"})
_RESOURCE_TYPES: dict[str, tuple[str, tuple[str, ...]]] = {
    "status": (
        "campaign",
        (
            "campaign.id",
            "campaign.name",
            "campaign.status",
            "campaign.serving_status",
            "campaign.primary_status",
        ),
    ),
    "budget": (
        "campaign",
        (
            "campaign.id",
            "campaign.campaign_budget",
            "campaign_budget.id",
            "campaign_budget.name",
            "campaign_budget.amount_micros",
            "campaign_budget.total_amount_micros",
            "campaign_budget.status",
        ),
    ),
    "bidding": (
        "campaign",
        (
            "campaign.id",
            "campaign.bidding_strategy_type",
            "campaign.bidding_strategy",
            "campaign.manual_cpc.enhanced_cpc_enabled",
            "campaign.target_cpa.target_cpa_micros",
            "campaign.target_roas.target_roas",
        ),
    ),
    "ad_groups": (
        "ad_group",
        (
            "campaign.id",
            "ad_group.id",
            "ad_group.name",
            "ad_group.status",
            "ad_group.type",
            "ad_group.cpc_bid_micros",
        ),
    ),
    "ads": (
        "ad_group_ad",
        (
            "campaign.id",
            "ad_group.id",
            "ad_group_ad.ad.id",
            "ad_group_ad.status",
            "ad_group_ad.ad.type",
            "ad_group_ad.ad.name",
        ),
    ),
    "assets": (
        "campaign_asset",
        (
            "campaign.id",
            "campaign_asset.asset",
            "campaign_asset.field_type",
            "campaign_asset.status",
            "campaign_asset.primary_status",
        ),
    ),
    "criteria": (
        "campaign_criterion",
        (
            "campaign.id",
            "campaign_criterion.criterion_id",
            "campaign_criterion.type",
            "campaign_criterion.status",
            "campaign_criterion.negative",
        ),
    ),
    "conversions": (
        "campaign_conversion_goal",
        (
            "campaign.id",
            "campaign_conversion_goal.category",
            "campaign_conversion_goal.origin",
            "campaign_conversion_goal.biddable",
        ),
    ),
}


@dataclass(frozen=True, slots=True)
class _CustomerContext:
    currency_code: str | None
    time_zone: str | None
    test_account: bool
    expires_at: float


class GoogleAdsService:
    def __init__(
        self,
        config: AccountsConfig,
        adapter: ReadAdapter,
        *,
        cursors: CursorStore | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._adapter = adapter
        self._cursors = cursors or CursorStore()
        self._clock = clock
        self._context_cache: dict[tuple[str, str], _CustomerContext] = {}
        self._context_lock = threading.Lock()

    def _profile(self, name: str) -> Profile:
        if not name:
            raise ValidationError("profile is required")
        return self._config.profile(name)

    @staticmethod
    def _customer_id(value: str) -> str:
        normalized = normalize_customer_id(value, field="customerId", required=True)
        assert normalized is not None
        return normalized

    async def _customer_context(self, profile: Profile, customer_id: str) -> _CustomerContext:
        key = (profile.name, customer_id)
        now = self._clock()
        with self._context_lock:
            cached = self._context_cache.get(key)
            if cached is not None and cached.expires_at > now:
                return cached
        result = await self._adapter.search(
            profile,
            customer_id,
            "SELECT customer.id, customer.currency_code, customer.time_zone, customer.test_account "
            "FROM customer LIMIT 1",
        )
        customer = result.items[0].get("customer", {}) if result.items else {}
        context = _CustomerContext(
            currency_code=_optional_string(customer.get("currency_code")),
            time_zone=_optional_string(customer.get("time_zone")),
            test_account=bool(customer.get("test_account", False)),
            expires_at=now + 300.0,
        )
        with self._context_lock:
            self._context_cache[key] = context
        return context

    async def _envelope(
        self,
        profile: Profile,
        customer_id: str | None,
        *,
        status: Status = "ok",
        items: list[dict[str, Any]] | None = None,
        limitations: list[str] | None = None,
        partial: bool = False,
        date_from: str | None = None,
        date_to: str | None = None,
        data_through: str | None = None,
        next_cursor: str | None = None,
        truncated: bool = False,
        resolve_context: bool = True,
    ) -> ToolResponse:
        currency_code: str | None = None
        time_zone: str | None = None
        if customer_id is not None and resolve_context:
            context = await self._customer_context(profile, customer_id)
            currency_code = context.currency_code
            time_zone = context.time_zone
        return ResponseEnvelope(
            status=status,
            profile=profile.name,
            login_customer_id=profile.login_customer_id,
            customer_id=customer_id,
            currency_code=currency_code,
            time_zone=time_zone,
            date_from=date_from,
            date_to=date_to,
            data_through=data_through,
            partial=partial,
            limitations=list(limitations or ()),
            items=list(items or ()),
            next_cursor=next_cursor,
            truncated=truncated,
        ).as_dict()

    async def _paged(
        self,
        *,
        profile: Profile,
        customer_id: str | None,
        binding: str,
        result: AdapterSearchResult | None = None,
        cursor: str | None = None,
        page_size: int = MAX_PAGE_ITEMS,
        date_from: str | None = None,
        date_to: str | None = None,
        data_through: str | None = None,
        limitations: list[str] | None = None,
    ) -> ToolResponse:
        if not 1 <= page_size <= MAX_PAGE_ITEMS:
            raise ValidationError(f"pageSize must be between 1 and {MAX_PAGE_ITEMS}")
        if cursor is not None:
            cursor_page = self._cursors.page(
                cursor, profile=profile.name, binding=binding, page_size=page_size
            )
            return await self._envelope(
                profile,
                customer_id,
                status=cursor_page.status,
                items=cursor_page.items,
                next_cursor=cursor_page.next_cursor,
                date_from=date_from,
                date_to=date_to,
                data_through=data_through,
                limitations=list(cursor_page.limitations),
                partial=cursor_page.partial,
                truncated=cursor_page.truncated,
            )
        assert result is not None
        all_items = list(result.items)
        page = all_items[:page_size]
        next_cursor = self._cursors.create(
            profile=profile.name,
            binding=binding,
            items=all_items,
            offset=page_size,
            status="partial" if result.truncated else "ok",
            partial=result.truncated,
            truncated=result.truncated,
            limitations=tuple(
                list(limitations or ())
                + (["Result exceeded 1000 rows; narrow the filters"] if result.truncated else [])
            ),
        )
        return await self._envelope(
            profile,
            customer_id,
            status="partial" if result.truncated else "ok",
            items=page,
            next_cursor=next_cursor,
            truncated=result.truncated,
            partial=result.truncated,
            date_from=date_from,
            date_to=date_to,
            data_through=data_through,
            limitations=list(limitations or ())
            + (["Result exceeded 1000 rows; narrow the filters"] if result.truncated else []),
        )

    async def auth_check(self, profile: str, customer_id: str) -> ToolResponse:
        selected = self._profile(profile)
        customer = self._customer_id(customer_id)
        result = await self._adapter.search(
            selected, customer, "SELECT customer.id FROM customer LIMIT 1"
        )
        return await self._envelope(
            selected,
            customer,
            items=[{"authenticated": bool(result.items), "readOnly": True}],
        )

    async def customers_list(
        self,
        profile: str,
        *,
        cursor: str | None = None,
        page_size: int = MAX_PAGE_ITEMS,
    ) -> ToolResponse:
        selected = self._profile(profile)
        binding = stable_fingerprint("customers_list", selected.name)
        result: AdapterSearchResult | None = None
        if cursor is None:
            names = await self._adapter.accessible_customers(selected)
            truncated = len(names) > 1_000
            bounded = names[:1_000]
            result = AdapterSearchResult(
                tuple(
                    {"resourceName": name, "customerId": name.rsplit("/", 1)[-1]}
                    for name in bounded
                ),
                len(names),
                truncated,
            )
        return await self._paged(
            profile=selected,
            customer_id=None,
            binding=binding,
            result=result,
            cursor=cursor,
            page_size=page_size,
        )

    async def customer_get(self, profile: str, customer_id: str) -> ToolResponse:
        selected = self._profile(profile)
        customer = self._customer_id(customer_id)
        result = await self._adapter.search(
            selected,
            customer,
            "SELECT customer.id, customer.descriptive_name, customer.currency_code, "
            "customer.time_zone, "
            "customer.manager, customer.test_account, customer.status FROM customer LIMIT 1",
        )
        return await self._envelope(selected, customer, items=list(result.items))

    async def customer_hierarchy(
        self,
        profile: str,
        customer_id: str,
        *,
        cursor: str | None = None,
        page_size: int = MAX_PAGE_ITEMS,
    ) -> ToolResponse:
        selected = self._profile(profile)
        customer = self._customer_id(customer_id)
        result = None
        if cursor is None:
            result = await self._adapter.search(
                selected,
                customer,
                "SELECT customer_client.client_customer, customer_client.id, "
                "customer_client.descriptive_name, "
                "customer_client.level, customer_client.manager, customer_client.hidden, "
                "customer_client.status, customer_client.currency_code, customer_client.time_zone "
                "FROM customer_client LIMIT 1000",
            )
        return await self._paged(
            profile=selected,
            customer_id=customer,
            binding=stable_fingerprint("customer_hierarchy", selected.name, customer),
            result=result,
            cursor=cursor,
            page_size=page_size,
        )

    async def account_health(self, profile: str, customer_id: str) -> ToolResponse:
        selected = self._profile(profile)
        customer = self._customer_id(customer_id)
        result = await self._adapter.search(
            selected,
            customer,
            "SELECT customer.id, customer.status, customer.manager, customer.test_account, "
            "customer.optimization_score, customer.auto_tagging_enabled, "
            "customer.tracking_url_template "
            "FROM customer LIMIT 1",
        )
        limitations = [
            "Health is read-only configuration evidence, not a guarantee of delivery "
            "or profitability"
        ]
        return await self._envelope(
            selected, customer, items=list(result.items), limitations=limitations
        )

    async def campaigns_query(
        self,
        profile: str,
        customer_id: str,
        *,
        statuses: list[str] | None = None,
        channel_types: list[str] | None = None,
        cursor: str | None = None,
        page_size: int = MAX_PAGE_ITEMS,
    ) -> ToolResponse:
        selected = self._profile(profile)
        customer = self._customer_id(customer_id)
        normalized_statuses = sorted({str(value).upper() for value in statuses or ()})
        invalid_statuses = sorted(set(normalized_statuses) - _CAMPAIGN_STATUSES)
        if invalid_statuses:
            raise ValidationError(f"Unsupported campaign status: {invalid_statuses[0]}")
        normalized_types = sorted({str(value).upper() for value in channel_types or ()})
        for channel_type in normalized_types:
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", channel_type):
                raise ValidationError("Invalid advertising channel type")
        filters: list[str] = []
        if normalized_statuses:
            values = ", ".join(f"'{value}'" for value in normalized_statuses)
            filters.append(f"campaign.status IN ({values})")
        if normalized_types:
            values = ", ".join(f"'{value}'" for value in normalized_types)
            filters.append(f"campaign.advertising_channel_type IN ({values})")
        where = f" WHERE {' AND '.join(filters)}" if filters else ""
        query = (
            "SELECT campaign.id, campaign.name, campaign.status, campaign.serving_status, "
            "campaign.primary_status, campaign.advertising_channel_type, "
            "campaign.advertising_channel_sub_type, campaign.start_date, campaign.end_date, "
            "campaign.campaign_budget, campaign.bidding_strategy_type "
            f"FROM campaign{where} LIMIT 1000"
        )
        binding = stable_fingerprint(
            "campaigns_query",
            selected.name,
            customer,
            ",".join(normalized_statuses),
            ",".join(normalized_types),
        )
        result = None if cursor else await self._adapter.search(selected, customer, query)
        response = await self._paged(
            profile=selected,
            customer_id=customer,
            binding=binding,
            result=result,
            cursor=cursor,
            page_size=page_size,
        )
        for item in response["items"]:
            campaign = item.get("campaign", {})
            if isinstance(campaign, dict) and "advertising_channel_type" in campaign:
                raw_value = campaign["advertising_channel_type"]
                if isinstance(raw_value, dict) and raw_value.get("recognized") is False:
                    value = str(raw_value.get("value"))
                else:
                    value = str(raw_value)
                    campaign["advertising_channel_type"] = {
                        "value": value,
                        "recognized": value in PINNED_CAMPAIGN_TYPES,
                    }
                if value not in PINNED_CAMPAIGN_TYPES:
                    response["status"] = "partial"
                    response["evidence"]["partial"] = True
                    response["evidence"]["limitations"].append("unsupported_specialization")
        return response

    async def campaign_inventory(
        self,
        profile: str,
        customer_id: str,
        campaign_id: str,
        resource_type: str,
        *,
        cursor: str | None = None,
        page_size: int = MAX_PAGE_ITEMS,
    ) -> ToolResponse:
        selected = self._profile(profile)
        customer = self._customer_id(customer_id)
        if not campaign_id.isascii() or not campaign_id.isdigit():
            raise ValidationError("campaignId must contain digits only")
        try:
            resource, fields = _RESOURCE_TYPES[resource_type]
        except KeyError as exc:
            raise ValidationError(f"Unsupported resourceType: {resource_type}") from exc
        query = (
            f"SELECT {', '.join(fields)} FROM {resource} "
            f"WHERE campaign.id = {campaign_id} LIMIT 1000"
        )
        binding = stable_fingerprint(
            "campaign_inventory", selected.name, customer, campaign_id, resource_type
        )
        result = None if cursor else await self._adapter.search(selected, customer, query)
        return await self._paged(
            profile=selected,
            customer_id=customer,
            binding=binding,
            result=result,
            cursor=cursor,
            page_size=page_size,
        )

    async def resource_metadata(
        self, profile: str, customer_id: str, field_names: list[str]
    ) -> ToolResponse:
        selected = self._profile(profile)
        customer = self._customer_id(customer_id)
        items = list(await self._adapter.field_metadata(selected, field_names))
        return await self._envelope(selected, customer, items=items)

    async def reports_catalog(self, profile: str, customer_id: str) -> ToolResponse:
        selected = self._profile(profile)
        customer = self._customer_id(customer_id)
        return await self._envelope(selected, customer, items=reports_catalog())

    async def report_run(
        self,
        profile: str,
        customer_id: str,
        report: str,
        date_from: str,
        date_to: str,
        *,
        campaign_ids: list[str] | None = None,
        segments: list[str] | None = None,
        cursor: str | None = None,
        page_size: int = MAX_PAGE_ITEMS,
    ) -> ToolResponse:
        selected = self._profile(profile)
        customer = self._customer_id(customer_id)
        definition, query = build_report_query(
            report,
            date_from=date_from,
            date_to=date_to,
            campaign_ids=campaign_ids,
            segments=segments,
        )
        binding = stable_fingerprint(
            "report_run",
            selected.name,
            customer,
            report,
            date_from,
            date_to,
            ",".join(campaign_ids or ()),
            ",".join(segments or ()),
        )
        try:
            result = None if cursor else await self._adapter.search(selected, customer, query)
        except AdapterError as exc:
            if exc.status != "not_supported":
                raise
            return await self._envelope(
                selected,
                customer,
                status="not_supported",
                date_from=date_from,
                date_to=date_to,
                limitations=[exc.message],
            )
        limitations = [
            "dataThrough is the requested upper bound; Google reporting and conversion "
            "adjustments can lag"
        ]
        context = await self._customer_context(selected, customer)
        if definition.modeled_conversions:
            limitations.append(
                "Conversion metrics can include Google modeled or attributed conversions"
            )
        if result is not None and not result.items and context.test_account:
            limitations.append(
                "Test account reports can be empty because test accounts do not serve ads"
            )
        return await self._paged(
            profile=selected,
            customer_id=customer,
            binding=binding,
            result=result,
            cursor=cursor,
            page_size=page_size,
            date_from=date_from,
            date_to=date_to,
            data_through=date_to,
            limitations=limitations,
        )

    async def change_events_query(
        self,
        profile: str,
        customer_id: str,
        date_from: str,
        date_to: str,
        *,
        include_user_identity: bool = False,
        cursor: str | None = None,
        page_size: int = MAX_PAGE_ITEMS,
    ) -> ToolResponse:
        selected = self._profile(profile)
        customer = self._customer_id(customer_id)
        start, end = validate_date_range(date_from, date_to, max_days=MAX_CHANGE_EVENT_DAYS)
        today = datetime.now(UTC).date()
        if end > today or start < today - timedelta(days=MAX_CHANGE_EVENT_DAYS - 1):
            raise ValidationError("Change events must be within the most recent 30-day window")
        fields = [
            "change_event.change_date_time",
            "change_event.change_resource_type",
            "change_event.change_resource_name",
            "change_event.client_type",
            "change_event.resource_change_operation",
            "change_event.changed_fields",
        ]
        if include_user_identity:
            fields.append("change_event.user_email")
        query = (
            f"SELECT {', '.join(fields)} FROM change_event "
            f"WHERE change_event.change_date_time >= '{date_from} 00:00:00' "
            f"AND change_event.change_date_time <= '{date_to} 23:59:59' "
            "ORDER BY change_event.change_date_time DESC LIMIT 1000"
        )
        binding = stable_fingerprint(
            "change_events_query",
            selected.name,
            customer,
            date_from,
            date_to,
            str(include_user_identity),
        )
        result = None if cursor else await self._adapter.search(selected, customer, query)
        if result is not None and not include_user_identity:
            result = AdapterSearchResult(
                tuple(redact_untrusted(item, redact_email=True) for item in result.items),
                result.total_results,
                result.truncated,
            )
        return await self._paged(
            profile=selected,
            customer_id=customer,
            binding=binding,
            result=result,
            cursor=cursor,
            page_size=page_size,
            date_from=date_from,
            date_to=date_to,
        )

    async def gaql_validate(self, profile: str, customer_id: str, query: str) -> ToolResponse:
        selected = self._profile(profile)
        customer = self._customer_id(customer_id)
        validated = validate_gaql(query)
        return await self._envelope(
            selected,
            customer,
            items=[
                {
                    "valid": True,
                    "resource": validated.resource,
                    "fields": list(validated.fields),
                    "limit": validated.limit,
                    "hasServingMetrics": validated.has_metrics,
                    "predefinedDateRange": validated.predefined_date_range,
                }
            ],
        )

    async def gaql_search(self, profile: str, customer_id: str, query: str) -> ToolResponse:
        selected = self._profile(profile)
        customer = self._customer_id(customer_id)
        validated = validate_gaql(query)
        result = await self._adapter.search(selected, customer, validated.query)
        limitations = ["Safe GAQL does not provide cursors; narrow the query if truncated"]
        if validated.predefined_date_range is not None:
            limitations.append(
                "The absolute dates for the bounded predefined GAQL range are resolved "
                "by Google Ads in the customer time zone"
            )
        return await self._envelope(
            selected,
            customer,
            status="partial" if result.truncated else "ok",
            items=list(result.items),
            partial=result.truncated,
            truncated=result.truncated,
            date_from=validated.date_from,
            date_to=validated.date_to,
            limitations=limitations,
        )


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
