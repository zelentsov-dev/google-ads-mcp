from __future__ import annotations

import secrets
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from google_ads_mcp.adapter import ReadAdapter
from google_ads_mcp.config import AccountsConfig, Profile
from google_ads_mcp.constants import RECEIPT_TTL_SECONDS
from google_ads_mcp.errors import AdapterError, BlockedError, ValidationError
from google_ads_mcp.journal import OperationJournal, OperationRecord, resolve_state_dir
from google_ads_mcp.models import ResponseEnvelope, ToolResponse
from google_ads_mcp.plans import PlanBuilder, aggregate_budget_query, state_queries
from google_ads_mcp.policies import PolicyFile, WritePolicy, load_policies
from google_ads_mcp.receipts import (
    ReceiptPayload,
    ReceiptSigner,
    load_or_create_installation_key,
    receipt_hash,
    value_fingerprint,
)
from google_ads_mcp.service import GoogleAdsService
from google_ads_mcp.write_adapter import WriteAdapter
from google_ads_mcp.write_models import (
    ApplyResult,
    MutationItem,
    ReadbackResult,
    WriteKind,
    WritePlan,
)

_DIRECT_FIELDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "campaign_budget_operation": (
        "campaign_budget",
        (
            "campaign_budget.resource_name",
            "campaign_budget.name",
            "campaign_budget.amount_micros",
            "campaign_budget.explicitly_shared",
            "campaign_budget.delivery_method",
            "campaign_budget.status",
        ),
    ),
    "campaign_operation": (
        "campaign",
        (
            "campaign.resource_name",
            "campaign.name",
            "campaign.status",
            "campaign.campaign_budget",
            "campaign.advertising_channel_type",
            "campaign.advertising_channel_sub_type",
            "campaign.start_date_time",
            "campaign.end_date_time",
            "campaign.final_url_suffix",
            "campaign.geo_target_type_setting.positive_geo_target_type",
            "campaign.geo_target_type_setting.negative_geo_target_type",
            "campaign.contains_eu_political_advertising",
            "campaign.bidding_strategy_type",
            "campaign.network_settings.target_google_search",
            "campaign.network_settings.target_search_network",
            "campaign.network_settings.target_content_network",
            "campaign.network_settings.target_partner_search_network",
            "campaign.app_campaign_setting.app_id",
            "campaign.app_campaign_setting.app_store",
            "campaign.app_campaign_setting.bidding_strategy_goal_type",
            "campaign.manual_cpc.enhanced_cpc_enabled",
            "campaign.target_cpa.target_cpa_micros",
            "campaign.target_roas.target_roas",
        ),
    ),
    "ad_group_operation": (
        "ad_group",
        (
            "ad_group.resource_name",
            "ad_group.campaign",
            "ad_group.name",
            "ad_group.status",
            "ad_group.type",
            "ad_group.cpc_bid_micros",
            "ad_group.target_cpa_micros",
            "ad_group.target_roas",
        ),
    ),
    "ad_group_criterion_operation": (
        "ad_group_criterion",
        (
            "ad_group_criterion.resource_name",
            "ad_group_criterion.ad_group",
            "ad_group_criterion.status",
            "ad_group_criterion.negative",
            "ad_group_criterion.keyword.text",
            "ad_group_criterion.keyword.match_type",
            "ad_group_criterion.cpc_bid_micros",
        ),
    ),
    "campaign_criterion_operation": (
        "campaign_criterion",
        (
            "campaign_criterion.resource_name",
            "campaign_criterion.campaign",
            "campaign_criterion.status",
            "campaign_criterion.negative",
            "campaign_criterion.keyword.text",
            "campaign_criterion.keyword.match_type",
            "campaign_criterion.location.geo_target_constant",
            "campaign_criterion.language.language_constant",
        ),
    ),
    "asset_operation": (
        "asset",
        (
            "asset.resource_name",
            "asset.name",
            "asset.type",
            "asset.text_asset.text",
            "asset.youtube_video_asset.youtube_video_id",
        ),
    ),
    "campaign_asset_operation": (
        "campaign_asset",
        (
            "campaign_asset.resource_name",
            "campaign_asset.campaign",
            "campaign_asset.asset",
            "campaign_asset.status",
            "campaign_asset.field_type",
        ),
    ),
    "ad_group_asset_operation": (
        "ad_group_asset",
        (
            "ad_group_asset.resource_name",
            "ad_group_asset.ad_group",
            "ad_group_asset.asset",
            "ad_group_asset.status",
            "ad_group_asset.field_type",
        ),
    ),
    "asset_group_operation": (
        "asset_group",
        (
            "asset_group.resource_name",
            "asset_group.campaign",
            "asset_group.name",
            "asset_group.status",
            "asset_group.final_urls",
        ),
    ),
    "asset_group_asset_operation": (
        "asset_group_asset",
        (
            "asset_group_asset.resource_name",
            "asset_group_asset.asset_group",
            "asset_group_asset.asset",
            "asset_group_asset.status",
            "asset_group_asset.field_type",
        ),
    ),
    "ad_group_ad_operation": (
        "ad_group_ad",
        (
            "ad_group_ad.resource_name",
            "ad_group_ad.ad_group",
            "ad_group_ad.status",
            "ad_group_ad.ad.name",
            "ad_group_ad.ad.type",
            "ad_group_ad.ad.final_urls",
            "ad_group_ad.ad.responsive_search_ad.headlines",
            "ad_group_ad.ad.responsive_search_ad.descriptions",
            "ad_group_ad.ad.app_ad.headlines",
            "ad_group_ad.ad.app_ad.descriptions",
            "ad_group_ad.ad.app_ad.images",
            "ad_group_ad.ad.app_ad.youtube_videos",
        ),
    ),
}


class OperatorService:
    def __init__(
        self,
        config: AccountsConfig,
        read_service: GoogleAdsService,
        read_adapter: ReadAdapter,
        write_adapter: WriteAdapter,
        *,
        allow_writes: bool,
        policy_loader: Callable[[], PolicyFile] = load_policies,
        state_dir: Path | None = None,
        journal: OperationJournal | None = None,
        signer: ReceiptSigner | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._config = config
        self._read_service = read_service
        self._read_adapter = read_adapter
        self._write_adapter = write_adapter
        self._allow_writes = allow_writes
        self._policy_loader = policy_loader
        self._state_dir = state_dir or resolve_state_dir()
        self._journal_value = journal
        self._signer_value = signer
        self._now = now
        self._builder = PlanBuilder()

    def _journal(self) -> OperationJournal:
        if self._journal_value is None:
            self._journal_value = OperationJournal(self._state_dir)
        return self._journal_value

    def _signer(self) -> ReceiptSigner:
        if self._signer_value is None:
            self._signer_value = ReceiptSigner(
                load_or_create_installation_key(self._state_dir / "installation.key")
            )
        return self._signer_value

    def _profile(self, name: str) -> Profile:
        return self._config.profile(name)

    def _require_runtime_writes(self, profile: Profile) -> None:
        if not self._allow_writes:
            raise BlockedError(
                "Write previews require serve --allow-writes",
                code="runtime_writes_disabled",
            )
        if not profile.allow_writes:
            raise BlockedError(
                "The selected profile has allowWrites=false",
                code="profile_writes_disabled",
            )

    async def _policy(
        self,
        *,
        profile: Profile,
        customer_id: str,
        policy_id: str,
        kind: WriteKind,
    ) -> tuple[WritePolicy, dict[str, Any]]:
        context = await self._read_service.customer_context(profile, customer_id)
        currency = context.get("currencyCode")
        if not isinstance(currency, str):
            raise BlockedError(
                "Customer currency could not be resolved",
                code="customer_context_unavailable",
            )
        policy = self._policy_loader().policy(policy_id)
        try:
            policy.require_active(
                profile=profile.name,
                customer_id=customer_id,
                currency_code=currency,
                kind=kind,
                now=self._now(),
            )
        except ValidationError as exc:
            raise BlockedError(exc.message, code=exc.code) from exc
        return policy, context

    async def _read_queries(
        self, profile: Profile, customer_id: str, queries: tuple[str, ...]
    ) -> dict[str, Any]:
        objects: list[dict[str, Any]] = []
        for index, query in enumerate(queries):
            result = await self._read_adapter.search(profile, customer_id, query)
            if result.truncated:
                raise BlockedError(
                    "Current state is truncated and cannot safely authorize a write",
                    code="current_state_partial",
                )
            objects.append({"index": index, "items": list(result.items)})
        return {"objects": objects}

    @staticmethod
    def _aggregate_budget(
        before: dict[str, Any], *, object_index: int, customer_id: str
    ) -> int:
        objects = before.get("objects")
        if not isinstance(objects, list) or object_index >= len(objects):
            raise BlockedError(
                "Aggregate campaign budget evidence is incomplete",
                code="current_state_partial",
            )
        current = objects[object_index]
        if not isinstance(current, dict) or not isinstance(current.get("items"), list):
            raise BlockedError(
                "Aggregate campaign budget evidence is malformed",
                code="current_state_partial",
            )
        values: dict[str, int] = {}
        for row in current["items"]:
            if not isinstance(row, dict):
                raise BlockedError(
                    "Aggregate campaign budget evidence is malformed",
                    code="current_state_partial",
                )
            campaign = row.get("campaign")
            budget = row.get("campaign_budget")
            if not isinstance(campaign, dict) or not isinstance(budget, dict):
                raise BlockedError(
                    "Aggregate campaign budget evidence is malformed",
                    code="current_state_partial",
                )
            identifier = str(campaign.get("id"))
            campaign_budget = campaign.get("campaign_budget")
            budget_resource = budget.get("resource_name")
            raw = budget.get("amount_micros")
            rendered = str(raw)
            if (
                not identifier.isascii()
                or not identifier.isdigit()
                or campaign.get("status") not in {"ENABLED", "PAUSED"}
                or not isinstance(campaign_budget, str)
                or not isinstance(budget_resource, str)
                or campaign_budget != budget_resource
                or not budget_resource.startswith(f"customers/{customer_id}/campaignBudgets/")
                or not rendered.isascii()
                or not rendered.isdigit()
                or identifier in values
            ):
                raise BlockedError(
                    "Aggregate campaign budget evidence is malformed or conflicting",
                    code="current_state_partial",
                )
            values[identifier] = int(rendered)
        return sum(values.values())

    @staticmethod
    def _enforce_policy(policy: WritePolicy, plan: WritePlan) -> WritePlan:
        campaign_id = plan.after.get("campaignId")
        if (
            campaign_id is not None
            and policy.campaign_ids
            and str(campaign_id) not in policy.campaign_ids
        ):
            raise BlockedError(
                "The campaign is outside the write policy allowlist",
                code="policy_campaign_scope_mismatch",
            )
        if (
            plan.kind == "campaign_status"
            and plan.after.get("status") == "ENABLED"
        ):
            if not policy.allow_campaign_enable:
                raise BlockedError(
                    "The policy does not permit enabling campaigns",
                    code="policy_enable_denied",
                )
            if str(campaign_id) not in policy.campaign_ids:
                raise BlockedError(
                    "The campaign is outside the enable policy allowlist",
                    code="policy_campaign_scope_mismatch",
                )
        try:
            policy.enforce_money(plan.monetary_delta, plan.after)
        except ValidationError as exc:
            raise BlockedError(exc.message, code=exc.code) from exc
        try:
            aggregate_index = plan.readback_queries.index(aggregate_budget_query())
        except ValueError as exc:
            raise BlockedError(
                "Aggregate campaign budget evidence is missing",
                code="current_state_partial",
            ) from exc
        current_aggregate = OperatorService._aggregate_budget(
            plan.before,
            object_index=aggregate_index,
            customer_id=plan.customer_id,
        )
        raw_delta = plan.monetary_delta.get("spendMicros")
        if raw_delta is None:
            current_budget = plan.after.get("currentCampaignDailyBudgetMicros")
            if plan.risk.get("spendAffecting") and current_budget is None:
                raise BlockedError(
                    "The campaign monetary exposure could not be bounded from current state",
                    code="monetary_impact_unknown",
                )
            delta = 0
        else:
            delta = int(raw_delta)
        projected = current_aggregate + max(delta, 0)
        if projected > policy.limits.max_aggregate_daily_budget_micros:
            raise BlockedError(
                "The projected aggregate daily budget exceeds the policy limit",
                code="policy_limit_exceeded",
            )
        after = dict(plan.after)
        if plan.risk.get("spendAffecting"):
            after["aggregateDailyBudgetMicros"] = str(projected)
        return replace(plan, after=after)

    async def preview(
        self,
        kind: WriteKind,
        profile_name: str,
        customer_id: str,
        policy_id: str,
        payload: dict[str, Any],
    ) -> ToolResponse:
        profile = self._profile(profile_name)
        customer = _customer_id(customer_id)
        self._require_runtime_writes(profile)
        policy, context = await self._policy(
            profile=profile,
            customer_id=customer,
            policy_id=policy_id,
            kind=kind,
        )
        queries = tuple(query for _, query in state_queries(kind, customer, payload))
        aggregate_query = aggregate_budget_query()
        if aggregate_query not in queries:
            queries += (aggregate_query,)
        before = await self._read_queries(profile, customer, queries)
        plan = self._builder.build(
            kind,
            profile=profile.name,
            customer_id=customer,
            policy_id=policy_id,
            payload=payload,
            before=before,
        )
        plan = replace(plan, readback_queries=queries)
        plan = self._enforce_policy(policy, plan)
        try:
            validation = await self._write_adapter.validate(profile, plan)
        except AdapterError as exc:
            if exc.status == "not_supported":
                return self._response(
                    profile,
                    customer,
                    context,
                    status="not_supported",
                    limitations=[exc.message],
                )
            raise
        if not validation.valid:
            raise BlockedError("Google Ads validate_only rejected the mutation plan")
        created_at = self._now().astimezone(UTC)
        expires_at = created_at + timedelta(seconds=RECEIPT_TTL_SECONDS)
        operation_id = str(uuid.uuid4())
        fingerprint = value_fingerprint(before)
        plan_digest = value_fingerprint(plan.as_dict())
        validation_digest = value_fingerprint(validation.as_dict())
        receipt_payload = ReceiptPayload(
            operation_id=operation_id,
            profile=profile.name,
            customer_id=customer,
            policy_id=policy_id,
            kind=kind,
            current_fingerprint=fingerprint,
            plan_digest=plan_digest,
            validation_digest=validation_digest,
            policy_fingerprint=policy.fingerprint,
            issued_at=int(created_at.timestamp()),
            expires_at=int(expires_at.timestamp()),
            nonce=secrets.token_urlsafe(18),
        )
        receipt = self._signer().sign(receipt_payload)
        record = self._journal().create_preview(
            operation_id=operation_id,
            receipt_hash_value=receipt_hash(receipt),
            plan=plan,
            current_fingerprint=fingerprint,
            plan_digest=plan_digest,
            validation_digest=validation_digest,
            policy_fingerprint=policy.fingerprint,
            validation=validation.as_dict(),
            created_at=created_at,
            expires_at=expires_at,
        )
        operation = record.public_dict()
        operation["receipt"] = receipt
        return self._response(profile, customer, context, operation=operation)

    async def apply(self, receipt: str) -> ToolResponse:
        if not self._allow_writes:
            raise BlockedError(
                "operations_apply requires serve --allow-writes",
                code="runtime_writes_disabled",
            )
        payload = self._signer().verify(receipt)
        now = self._now().astimezone(UTC)
        record = self._journal().by_receipt_hash(receipt_hash(receipt))
        self._verify_receipt_scope(payload, record)
        if record.receipt_used or record.state != "previewed":
            raise ValidationError(
                "The operation receipt has already been used", code="receipt_replayed"
            )
        if int(now.timestamp()) >= payload.expires_at:
            self._journal().invalidate(record.operation_id, "receipt_expired", now)
            raise BlockedError("The operation receipt has expired", code="receipt_expired")
        profile = self._profile(record.profile)
        self._require_runtime_writes(profile)
        policy, context = await self._policy(
            profile=profile,
            customer_id=record.customer_id,
            policy_id=record.policy_id,
            kind=cast(WriteKind, record.kind),
        )
        if payload.policy_fingerprint != policy.fingerprint:
            self._journal().invalidate(record.operation_id, "policy_changed", now)
            raise BlockedError(
                "The approved write policy changed after preview; create a new preview",
                code="receipt_drifted",
            )
        self._enforce_policy(policy, record.plan)
        current = await self._read_queries(
            profile, record.customer_id, record.plan.readback_queries
        )
        if value_fingerprint(current) != record.current_fingerprint:
            self._journal().invalidate(record.operation_id, "current_state_drift", now)
            raise BlockedError(
                "Current state drifted after preview; create a new preview",
                code="receipt_drifted",
            )
        record = self._journal().begin_apply(record.operation_id, receipt_hash(receipt), now)
        response: ApplyResult | None = None
        write_error: AdapterError | None = None
        try:
            response = await self._write_adapter.apply(profile, record.plan)
        except AdapterError as exc:
            write_error = exc
        readback = await self._readback(profile, record, response)
        state = self._resolve_state(response, write_error, readback)
        result = response.as_dict() if response is not None else {}
        if write_error is not None:
            result["errorCode"] = write_error.code
            if write_error.request_id is not None:
                result["requestId"] = write_error.request_id
        record = self._journal().transition(
            record.operation_id,
            state,
            verification=readback.as_dict(),
            result=result,
            now=self._now(),
        )
        return self._response(profile, record.customer_id, context, operation=record.public_dict())

    @staticmethod
    def _verify_receipt_scope(payload: ReceiptPayload, record: OperationRecord) -> None:
        if (
            payload.operation_id != record.operation_id
            or payload.profile != record.profile
            or payload.customer_id != record.customer_id
            or payload.policy_id != record.policy_id
            or payload.kind != record.kind
            or payload.current_fingerprint != record.current_fingerprint
            or payload.plan_digest != record.plan_digest
            or payload.validation_digest != record.validation_digest
            or payload.policy_fingerprint != record.policy_fingerprint
        ):
            raise BlockedError("The operation receipt scope does not match", code="invalid_receipt")

    async def _readback(
        self,
        profile: Profile,
        record: OperationRecord,
        response: ApplyResult | None,
    ) -> ReadbackResult:
        resource_names: dict[str, str] = {}
        if response is not None:
            resource_names = {
                item.correlation_id: item.resource_name
                for item in response.items
                if item.status == "applied" and item.resource_name is not None
            }
        temporary_names = {
            candidate: resource_names[item.correlation_id]
            for item in record.plan.items
            if isinstance((candidate := item.resource.get("resource_name")), str)
            and candidate.rsplit("/", 1)[-1].startswith("-")
            and item.correlation_id in resource_names
        }
        queries: list[tuple[MutationItem, str]] = []
        objects: list[dict[str, Any]] = []
        for item in record.plan.items:
            name = resource_names.get(item.correlation_id)
            if name is None and item.action == "update":
                candidate = item.resource.get("resource_name")
                if isinstance(candidate, str) and not candidate.rsplit("/", 1)[-1].startswith("-"):
                    name = candidate
            query = _direct_query(item.operation_type, name)
            if query is not None:
                queries.append((item, query))
            else:
                objects.append(
                    {
                        "correlationId": item.correlation_id,
                        "targetKnown": name is not None,
                        "matched": False,
                        "current": {},
                    }
                )
        if not queries:
            current = await self._read_queries(
                profile, record.customer_id, record.plan.readback_queries
            )
            if value_fingerprint(current) == record.current_fingerprint:
                state = "matched_before"
            else:
                state = "inconclusive"
            return ReadbackResult(state=cast(Any, state), current=current, objects=tuple(objects))
        matched = 0
        for mutation, query in queries:
            result = await self._read_adapter.search(profile, record.customer_id, query)
            current_item = (
                result.items[0] if len(result.items) == 1 and not result.truncated else None
            )
            is_match = current_item is not None and _matches_expected(
                current_item,
                expected=mutation.resource,
                operation_type=mutation.operation_type,
                action=mutation.action,
                update_mask=mutation.update_mask,
                temporary_names=temporary_names,
            )
            matched += int(is_match)
            objects.append(
                {
                    "correlationId": mutation.correlation_id,
                    "targetKnown": True,
                    "matched": is_match,
                    "current": current_item or {},
                }
            )
        if matched == len(record.plan.items) and len(queries) == len(record.plan.items):
            state = "matched_after"
        elif matched > 0:
            state = "partial"
        else:
            current = await self._read_queries(
                profile, record.customer_id, record.plan.readback_queries
            )
            state = (
                "matched_before"
                if value_fingerprint(current) == record.current_fingerprint
                else "inconclusive"
            )
        return ReadbackResult(state=cast(Any, state), objects=tuple(objects))

    @staticmethod
    def _resolve_state(
        response: ApplyResult | None,
        error: AdapterError | None,
        readback: ReadbackResult,
    ) -> Any:
        if readback.state == "matched_after" and response is not None:
            return "partial" if response.state == "partial" else "applied"
        if readback.state == "partial" or (response is not None and response.state == "partial"):
            return "partial"
        if (
            readback.state == "matched_before"
            and error is not None
            and error.code != "ambiguous_write"
        ):
            return "failed"
        if (
            response is not None
            and response.state == "failed"
            and readback.state == "matched_before"
        ):
            return "failed"
        return "committed_unverified"

    async def inspect(self, operation_id: str) -> ToolResponse:
        record = self._journal().inspect(operation_id)
        profile = self._profile(record.profile)
        return self._response(
            profile,
            record.customer_id,
            {"currencyCode": None, "timeZone": None},
            operation=record.public_dict(),
        )

    async def list(self, profile_name: str | None = None, limit: int = 50) -> ToolResponse:
        records = self._journal().list(limit=limit, profile=profile_name)
        if records:
            profile = self._profile(records[0].profile)
        elif profile_name is not None:
            profile = self._profile(profile_name)
        elif self._config.profiles:
            profile = self._config.profiles[0]
        else:
            raise ValidationError("At least one configured profile is required")
        return self._response(
            profile,
            None,
            {"currencyCode": None, "timeZone": None},
            items=[record.public_dict() for record in records],
        )

    async def verify(self, operation_id: str) -> ToolResponse:
        record = self._journal().inspect(operation_id)
        profile = self._profile(record.profile)
        context = await self._read_service.customer_context(profile, record.customer_id)
        if record.state not in {"applying", "partial", "committed_unverified"}:
            return self._response(
                profile, record.customer_id, context, operation=record.public_dict()
            )
        response = _apply_result_from_record(record)
        readback = await self._readback(profile, record, response)
        if readback.state == "matched_after":
            state = "applied"
        elif readback.state == "partial":
            state = "partial"
        else:
            state = "committed_unverified"
        record = self._journal().transition(
            operation_id,
            cast(Any, state),
            verification=readback.as_dict(),
            result=record.result,
            now=self._now(),
        )
        return self._response(profile, record.customer_id, context, operation=record.public_dict())

    @staticmethod
    def _response(
        profile: Profile,
        customer_id: str | None,
        context: dict[str, Any],
        *,
        status: Any = "ok",
        operation: dict[str, Any] | None = None,
        items: list[dict[str, Any]] | None = None,
        limitations: list[str] | None = None,
    ) -> ToolResponse:
        response = ResponseEnvelope(
            status=status,
            profile=profile.name,
            login_customer_id=profile.login_customer_id,
            customer_id=customer_id,
            currency_code=cast(str | None, context.get("currencyCode")),
            time_zone=cast(str | None, context.get("timeZone")),
            source="google_ads_mcp_operation_journal",
            limitations=list(limitations or ()),
            items=list(items or ()),
        ).as_dict()
        if operation is not None:
            response["operation"] = operation
        return response


def _customer_id(value: str) -> str:
    normalized = value.replace("-", "")
    if not normalized.isascii() or not normalized.isdigit() or len(normalized) != 10:
        raise ValidationError("customerId must contain exactly 10 digits")
    return normalized


def _walk(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if isinstance(value, dict):
        result.append(value)
        for nested in value.values():
            result.extend(_walk(nested))
    elif isinstance(value, list):
        for nested in value:
            result.extend(_walk(nested))
    return result


def _direct_query(operation_type: str, resource_name: str | None) -> str | None:
    definition = _DIRECT_FIELDS.get(operation_type)
    if definition is None or resource_name is None:
        return None
    escaped = resource_name.replace("\\", "\\\\").replace("'", "\\'")
    resource, fields = definition
    return (
        f"SELECT {', '.join(fields)} FROM {resource} "
        f"WHERE {resource}.resource_name = '{escaped}' LIMIT 1"
    )


def _matches_expected(
    current: dict[str, Any],
    expected: dict[str, Any],
    *,
    operation_type: str = "campaign_operation",
    action: str = "create",
    update_mask: tuple[str, ...] = (),
    temporary_names: dict[str, str] | None = None,
) -> bool:
    definition = _DIRECT_FIELDS.get(operation_type)
    if definition is None:
        return False
    root = current.get(definition[0])
    if not isinstance(root, dict):
        return False
    strategy_fields = {
        "manual_cpc": "MANUAL_CPC",
        "maximize_conversions": "MAXIMIZE_CONVERSIONS",
        "maximize_conversion_value": "MAXIMIZE_CONVERSION_VALUE",
        "target_cpa": "TARGET_CPA",
        "target_roas": "TARGET_ROAS",
    }
    selected = expected
    if action == "update":
        selected = {}
        for path in update_mask:
            parts = path.split(".")
            expected_cursor: Any = expected
            selected_cursor = selected
            for part in parts[:-1]:
                if not isinstance(expected_cursor, dict) or part not in expected_cursor:
                    return False
                expected_cursor = expected_cursor[part]
                candidate = selected_cursor.setdefault(part, {})
                if not isinstance(candidate, dict):
                    return False
                selected_cursor = candidate
            leaf = parts[-1]
            if not isinstance(expected_cursor, dict) or leaf not in expected_cursor:
                return False
            selected_cursor[leaf] = expected_cursor[leaf]

    def compare(expected_value: Any, current_value: Any) -> bool:
        if isinstance(expected_value, dict):
            if not isinstance(current_value, dict):
                return False
            for key, nested in expected_value.items():
                candidates = (key, key[:-1]) if key.endswith("_") else (key,)
                current_key = next(
                    (candidate for candidate in candidates if candidate in current_value),
                    None,
                )
                if current_key is None:
                    return False
                if not compare(nested, current_value[current_key]):
                    return False
            return True
        if isinstance(expected_value, list):
            if not isinstance(current_value, list) or len(expected_value) != len(current_value):
                return False
            return all(
                compare(expected_item, current_item)
                for expected_item, current_item in zip(expected_value, current_value, strict=True)
            )
        resolved = (
            temporary_names.get(expected_value, expected_value)
            if temporary_names is not None and isinstance(expected_value, str)
            else expected_value
        )
        return str(resolved) == str(current_value)

    checks = 0
    for key, value in selected.items():
        normalized = key[:-1] if key.endswith("_") else key
        if normalized == "resource_name" and action == "create":
            continue
        if (
            normalized == "name"
            and operation_type == "campaign_budget_operation"
            and action == "create"
            and selected.get("explicitly_shared") is False
        ):
            continue
        checks += 1
        if key in strategy_fields:
            if str(root.get("bidding_strategy_type")) != strategy_fields[key]:
                return False
            if not value:
                continue
        candidates = (key, normalized) if key.endswith("_") else (key,)
        current_key = next((candidate for candidate in candidates if candidate in root), None)
        if current_key is None or not compare(value, root[current_key]):
            return False
    return checks > 0


def _apply_result_from_record(record: OperationRecord) -> ApplyResult | None:
    state = record.result.get("state")
    if state not in {"applied", "failed", "partial", "committed_unverified"}:
        return None
    from google_ads_mcp.write_models import ApplyItemResult

    items = tuple(
        ApplyItemResult(
            correlation_id=str(item.get("correlationId")),
            status=cast(Any, item.get("status")),
            resource_name=(
                str(item["resourceName"]) if item.get("resourceName") is not None else None
            ),
            error_code=(str(item["errorCode"]) if item.get("errorCode") is not None else None),
        )
        for item in record.result.get("items", ())
        if isinstance(item, dict)
    )
    return ApplyResult(
        state=state,
        items=items,
        request_id=(
            str(record.result["requestId"]) if record.result.get("requestId") is not None else None
        ),
    )
