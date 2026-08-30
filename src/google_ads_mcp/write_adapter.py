from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any, Protocol, cast

from google_ads_mcp.adapter import GoogleAdsReadAdapter
from google_ads_mcp.config import Profile
from google_ads_mcp.constants import API_VERSION, MAX_MUTATION_ITEMS, WRITE_DEADLINE_SECONDS
from google_ads_mcp.errors import AdapterError, SecurityError
from google_ads_mcp.write_models import (
    ApplyItemResult,
    ApplyResult,
    MutationItem,
    ValidationResult,
    WritePlan,
)

ALLOWED_WRITE_SERVICES = frozenset({"GoogleAdsService"})
FORBIDDEN_WRITE_SERVICES = frozenset(
    {
        "AccountBudgetProposalService",
        "AccountLinkService",
        "BatchJobService",
        "BillingSetupService",
        "ConversionAdjustmentUploadService",
        "ConversionUploadService",
        "CustomerClientLinkService",
        "CustomerManagerLinkService",
        "CustomerUserAccessInvitationService",
        "CustomerUserAccessService",
        "DataLinkService",
        "OfflineUserDataJobService",
        "UserListService",
    }
)

_OPERATIONS: dict[str, tuple[str, str, frozenset[str]]] = {
    "campaign_budget_operation": (
        "CampaignBudgetOperation",
        "CampaignBudget",
        frozenset(
            {"resource_name", "name", "amount_micros", "delivery_method", "explicitly_shared"}
        ),
    ),
    "campaign_operation": (
        "CampaignOperation",
        "Campaign",
        frozenset(
            {
                "resource_name",
                "name",
                "campaign_budget",
                "status",
                "advertising_channel_type",
                "advertising_channel_sub_type",
                "network_settings",
                "start_date_time",
                "end_date_time",
                "final_url_suffix",
                "geo_target_type_setting",
                "manual_cpc",
                "maximize_conversions",
                "maximize_conversion_value",
                "target_cpa",
                "target_roas",
                "app_campaign_setting",
                "selective_optimization",
                "optimization_goal_setting",
                "contains_eu_political_advertising",
            }
        ),
    ),
    "ad_group_operation": (
        "AdGroupOperation",
        "AdGroup",
        frozenset(
            {
                "resource_name",
                "campaign",
                "name",
                "status",
                "type_",
                "cpc_bid_micros",
                "cpm_bid_micros",
                "cpv_bid_micros",
                "target_cpa_micros",
                "target_roas",
            }
        ),
    ),
    "ad_group_criterion_operation": (
        "AdGroupCriterionOperation",
        "AdGroupCriterion",
        frozenset(
            {
                "resource_name",
                "ad_group",
                "status",
                "negative",
                "keyword",
                "location",
                "language",
                "cpc_bid_micros",
                "final_urls",
            }
        ),
    ),
    "campaign_criterion_operation": (
        "CampaignCriterionOperation",
        "CampaignCriterion",
        frozenset(
            {"resource_name", "campaign", "negative", "keyword", "location", "language", "status"}
        ),
    ),
    "asset_operation": (
        "AssetOperation",
        "Asset",
        frozenset(
            {
                "resource_name",
                "name",
                "text_asset",
                "youtube_video_asset",
                "image_asset",
                "final_urls",
            }
        ),
    ),
    "campaign_asset_operation": (
        "CampaignAssetOperation",
        "CampaignAsset",
        frozenset({"resource_name", "campaign", "asset", "field_type", "status"}),
    ),
    "ad_group_asset_operation": (
        "AdGroupAssetOperation",
        "AdGroupAsset",
        frozenset({"resource_name", "ad_group", "asset", "field_type", "status"}),
    ),
    "asset_group_operation": (
        "AssetGroupOperation",
        "AssetGroup",
        frozenset(
            {"resource_name", "campaign", "name", "final_urls", "final_mobile_urls", "status"}
        ),
    ),
    "asset_group_asset_operation": (
        "AssetGroupAssetOperation",
        "AssetGroupAsset",
        frozenset({"resource_name", "asset_group", "asset", "field_type", "status"}),
    ),
    "ad_group_ad_operation": (
        "AdGroupAdOperation",
        "AdGroupAd",
        frozenset({"resource_name", "ad_group", "status", "ad"}),
    ),
}


class WriteAdapter(Protocol):
    async def validate(self, profile: Profile, plan: WritePlan) -> ValidationResult: ...

    async def apply(self, profile: Profile, plan: WritePlan) -> ApplyResult: ...


class GoogleAdsWriteAdapter:
    """Construct and execute only the explicitly allowlisted Google Ads mutations."""

    def __init__(
        self,
        *,
        client_factory: Callable[[Profile], Any] | None = None,
        read_adapter: GoogleAdsReadAdapter | None = None,
    ) -> None:
        self._read_adapter = read_adapter or GoogleAdsReadAdapter(client_factory=client_factory)

    def _client(self, profile: Profile) -> Any:
        return self._read_adapter.client_for_write_adapter(profile)

    @staticmethod
    async def _close(service: Any) -> None:
        transport = getattr(service, "transport", None)
        close = getattr(transport, "close", None)
        if not callable(close):
            return
        result = close()
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _validate_plan(plan: WritePlan) -> None:
        if not plan.items or len(plan.items) > MAX_MUTATION_ITEMS:
            raise SecurityError("A mutation plan must contain between 1 and 100 items")
        if plan.partial_failure and not plan.independent_items:
            raise SecurityError("Partial failure requires an explicitly independent batch")
        if plan.kind in {"recommendation_apply", "recommendation_dismiss"}:
            raise AdapterError(
                "Google Ads recommendations do not expose validate_only; "
                "safe apply is unavailable.",
                code="not_supported",
                status="not_supported",
            )
        for item in plan.items:
            if item.action not in {"create", "update"}:
                raise SecurityError("Remove operations are not supported")
            definition = _OPERATIONS.get(item.operation_type)
            if definition is None:
                raise SecurityError("The mutation plan contains a forbidden operation type")
            unknown = set(item.resource) - definition[2]
            if unknown:
                raise SecurityError("The mutation plan contains a forbidden resource field")
            if item.action != "update" and item.update_mask:
                raise SecurityError("Only update operations may contain an update mask")
            if item.action == "update" and not item.update_mask:
                raise SecurityError("Update operations require an explicit update mask")

    @staticmethod
    def _resource_message(client: Any, resource_type: str, item: MutationItem) -> Any:
        message = client.get_type(resource_type, version=API_VERSION)
        message_type = type(message)
        return message_type(item.resource)

    @classmethod
    def _mutate_operation(cls, client: Any, item: MutationItem) -> Any:
        operation_type, resource_type, _ = _OPERATIONS[item.operation_type]
        operation = client.get_type(operation_type, version=API_VERSION)
        resource = cls._resource_message(client, resource_type, item)
        client.copy_from(getattr(operation, item.action), resource)
        if item.update_mask:
            operation.update_mask.paths.extend(item.update_mask)
        mutate = client.get_type("MutateOperation", version=API_VERSION)
        client.copy_from(getattr(mutate, item.operation_type), operation)
        return mutate

    @classmethod
    def _request(cls, client: Any, plan: WritePlan, *, validate_only: bool) -> Any:
        request = client.get_type("MutateGoogleAdsRequest", version=API_VERSION)
        request.customer_id = plan.customer_id
        request.partial_failure = plan.partial_failure
        request.validate_only = validate_only
        request.response_content_type = client.enums.ResponseContentTypeEnum.MUTABLE_RESOURCE
        request.mutate_operations.extend(cls._mutate_operation(client, item) for item in plan.items)
        return request

    @staticmethod
    def _request_id(value: Any) -> str | None:
        raw = getattr(value, "request_id", None)
        return str(raw) if raw else None

    @staticmethod
    def _status_name(exc: BaseException) -> str | None:
        return GoogleAdsReadAdapter.grpc_status_name(exc)

    @classmethod
    def _write_error(cls, exc: BaseException) -> AdapterError:
        status = cls._status_name(exc)
        request_id = GoogleAdsReadAdapter.google_request_id(exc)
        if status in {"DEADLINE_EXCEEDED", "UNAVAILABLE", "CANCELLED", "INTERNAL", "UNKNOWN"}:
            return AdapterError(
                "The Google Ads write outcome is unknown and requires direct readback.",
                code="ambiguous_write",
                request_id=request_id,
            )
        sanitized = GoogleAdsReadAdapter.sanitize_google_exception(exc)
        sanitized.transient = False
        return sanitized

    async def validate(self, profile: Profile, plan: WritePlan) -> ValidationResult:
        self._validate_plan(plan)
        client = self._client(profile)
        service = client.get_service("GoogleAdsService", version=API_VERSION, is_async=True)
        try:
            request = self._request(client, plan, validate_only=True)
            try:
                response = await service.mutate(
                    request=request,
                    retry=None,
                    timeout=WRITE_DEADLINE_SECONDS,
                )
            except Exception as exc:
                raise self._write_error(exc) from None
            return ValidationResult(valid=True, request_id=self._request_id(response))
        finally:
            await self._close(service)

    @staticmethod
    def _response_item(response: Any, item: MutationItem) -> ApplyItemResult:
        result_field = item.operation_type.removesuffix("_operation") + "_result"
        raw = getattr(response, result_field, None)
        resource_name = getattr(raw, "resource_name", None)
        if not resource_name:
            return ApplyItemResult(
                correlation_id=item.correlation_id,
                status="failed",
                error_code="partial_failure",
            )
        return ApplyItemResult(
            correlation_id=item.correlation_id,
            status="applied",
            resource_name=str(resource_name),
        )

    async def apply(self, profile: Profile, plan: WritePlan) -> ApplyResult:
        self._validate_plan(plan)
        client = self._client(profile)
        service = client.get_service("GoogleAdsService", version=API_VERSION, is_async=True)
        try:
            request = self._request(client, plan, validate_only=False)
            try:
                async with asyncio.timeout(WRITE_DEADLINE_SECONDS):
                    response = await service.mutate(
                        request=request,
                        retry=None,
                        timeout=WRITE_DEADLINE_SECONDS,
                    )
            except TimeoutError as exc:
                raise AdapterError(
                    "The Google Ads write outcome is unknown and requires direct readback.",
                    code="ambiguous_write",
                ) from exc
            except Exception as exc:
                raise self._write_error(exc) from None
            raw_responses = tuple(response.mutate_operation_responses)
            items: list[ApplyItemResult] = []
            for index, item in enumerate(plan.items):
                if index >= len(raw_responses):
                    items.append(
                        ApplyItemResult(
                            correlation_id=item.correlation_id,
                            status="failed",
                            error_code="missing_operation_response",
                        )
                    )
                else:
                    items.append(self._response_item(raw_responses[index], item))
            applied = sum(item.status == "applied" for item in items)
            if applied == len(items):
                state = "applied"
            elif applied == 0:
                state = "failed"
            else:
                state = "partial"
            return ApplyResult(
                state=cast(Any, state),
                items=tuple(items),
                request_id=self._request_id(response),
            )
        finally:
            await self._close(service)
