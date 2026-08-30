from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from google_ads_mcp import journal as journal_module
from google_ads_mcp.adapter import AdapterSearchResult
from google_ads_mcp.config import AccountsConfig, parse_config
from google_ads_mcp.errors import AdapterError, BlockedError, SecurityError, ValidationError
from google_ads_mcp.journal import OperationJournal
from google_ads_mcp.operator import (
    OperatorService,
    _apply_result_from_record,
    _customer_id,
    _direct_query,
    _matches_expected,
    _walk,
)
from google_ads_mcp.plans import aggregate_budget_query
from google_ads_mcp.policies import (
    PolicyApproval,
    PolicyFile,
    PolicyLimits,
    WritePolicy,
)
from google_ads_mcp.receipts import (
    ReceiptSigner,
    receipt_hash,
    value_fingerprint,
)
from google_ads_mcp.service import GoogleAdsService
from google_ads_mcp.write_models import (
    ApplyItemResult,
    ApplyResult,
    MutationItem,
    ReadbackResult,
    ValidationResult,
    WriteKind,
    WritePlan,
)

NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)


def _config(allow_writes: bool = True) -> AccountsConfig:
    return parse_config(
        {
            "profiles": [
                {
                    "name": "operator",
                    "loginCustomerId": "1111111111",
                    "defaultCustomerId": "1234567890",
                    "auth": {"type": "adc", "developerTokenEnv": "SYNTHETIC_TOKEN"},
                    "allowWrites": allow_writes,
                }
            ]
        },
        path=Path("/synthetic/accounts.json"),
    )


def _policy(
    *,
    approved: bool = True,
    allow_enable: bool = False,
    permissions: frozenset[WriteKind] | None = None,
) -> WritePolicy:
    policy = WritePolicy(
        policy_id="safe",
        profile="operator",
        customer_id="1234567890",
        currency_code="USD",
        permissions=permissions
        or frozenset(
            {
                "campaign_status",
                "campaign_budget",
                "recommendation_apply",
            }
        ),
        allow_campaign_enable=allow_enable,
        campaign_ids=frozenset({"1"}) if allow_enable else frozenset(),
        limits=PolicyLimits(
            max_aggregate_daily_budget_micros=10_000_000,
            max_campaign_daily_budget_micros=5_000_000,
            max_spend_change_micros_per_operation=1_000_000,
            max_bid_micros=500_000,
        ),
        approval=PolicyApproval(
            status="pending",
            approved_at=None,
            expires_at=None,
            policy_fingerprint=None,
        ),
    )
    if not approved:
        return policy
    return replace(
        policy,
        approval=PolicyApproval(
            status="approved",
            approved_at=NOW,
            expires_at=NOW + timedelta(days=30),
            policy_fingerprint=policy.fingerprint,
        ),
    )


class FakeReadAdapter:
    def __init__(self) -> None:
        self.status = "ENABLED"
        self.truncated = False
        self.asset_created = False
        self.targeting_configured = True
        self.keyword_bid_micros = 100_000
        self.ad_group_bid_micros = 100_000
        self.queries: list[str] = []

    async def search(self, profile: Any, customer_id: str, query: str) -> AdapterSearchResult:
        self.queries.append(query)
        if (
            "campaign_budget.amount_micros" in query
            and ("FROM ad_group " in query or "FROM asset_group " in query)
        ):
            raise AdapterError(
                "campaign_budget fields are not selectable from child resources",
                status="not_supported",
                code="not_supported",
            )
        if "customer.currency_code" in query:
            return AdapterSearchResult(
                (
                    {
                        "customer": {
                            "id": customer_id,
                            "currency_code": "USD",
                            "time_zone": "Etc/UTC",
                            "test_account": True,
                        }
                    },
                ),
                1,
                False,
            )
        if "FROM geo_target_constant" in query:
            return AdapterSearchResult(
                ({"geo_target_constant": {"id": "2840", "status": "ENABLED"}},),
                1,
                False,
            )
        if "FROM language_constant" in query:
            return AdapterSearchResult(
                ({"language_constant": {"id": "1000", "targetable": True}},),
                1,
                False,
            )
        if any(
            marker in query
            for marker in (
                "campaign.name = '",
                "ad_group.name = '",
                "ad_group_criterion.keyword.text = '",
                "campaign_criterion.keyword.text = '",
                "asset.name = '",
                "asset_group.name = '",
                "ad_group_ad.ad.name = '",
            )
        ) or (".asset = '" in query and ".resource_name = '" not in query):
            return AdapterSearchResult((), 0, False)
        if "FROM campaign_criterion " in query:
            if "resource_name" in query and "campaignCriteria/1~2840" in query:
                return AdapterSearchResult(
                    (
                        {
                            "campaign_criterion": {
                                "resource_name": (
                                    f"customers/{customer_id}/campaignCriteria/1~2840"
                                ),
                                "campaign": f"customers/{customer_id}/campaigns/1",
                                "status": "ENABLED",
                                "negative": False,
                                "location": {
                                    "geo_target_constant": "geoTargetConstants/2840"
                                },
                            }
                        },
                    ),
                    1,
                    False,
                )
            if "resource_name" in query and "campaignCriteria/1~1000" in query:
                return AdapterSearchResult(
                    (
                        {
                            "campaign_criterion": {
                                "resource_name": (
                                    f"customers/{customer_id}/campaignCriteria/1~1000"
                                ),
                                "campaign": f"customers/{customer_id}/campaigns/1",
                                "status": "ENABLED",
                                "negative": False,
                                "language": {
                                    "language_constant": "languageConstants/1000"
                                },
                            }
                        },
                    ),
                    1,
                    False,
                )
            if self.targeting_configured:
                return AdapterSearchResult(
                    (
                        {
                            "campaign_criterion": {
                                "criterion_id": "2840",
                                "resource_name": (
                                    f"customers/{customer_id}/campaignCriteria/1~2840"
                                ),
                                "type": "LOCATION",
                                "status": "ENABLED",
                                "negative": False,
                                "location": {
                                    "geo_target_constant": "geoTargetConstants/2840"
                                },
                            }
                        },
                        {
                            "campaign_criterion": {
                                "criterion_id": "1000",
                                "resource_name": (
                                    f"customers/{customer_id}/campaignCriteria/1~1000"
                                ),
                                "type": "LANGUAGE",
                                "status": "ENABLED",
                                "negative": False,
                                "language": {
                                    "language_constant": "languageConstants/1000"
                                },
                            }
                        },
                    ),
                    2,
                    False,
                )
            return AdapterSearchResult((), 0, False)
        if "FROM ad_group_criterion " in query:
            return AdapterSearchResult(
                (
                    {
                        "ad_group_criterion": {
                            "criterion_id": "2",
                            "status": "PAUSED",
                            "negative": False,
                            "type": "KEYWORD",
                            "keyword": {"text": "keyword", "match_type": "EXACT"},
                            "effective_cpc_bid_micros": str(
                                self.keyword_bid_micros
                            ),
                            "effective_cpc_bid_source": "AD_GROUP_CRITERION",
                        }
                    },
                ),
                1,
                False,
            )
        if "FROM keyword_view " in query:
            return AdapterSearchResult(
                (
                    {
                        "campaign": {"id": "1"},
                        "ad_group": {
                            "id": "1",
                            "status": "ENABLED",
                            "cpc_bid_micros": str(self.ad_group_bid_micros),
                        },
                        "ad_group_criterion": {
                            "criterion_id": "2",
                            "status": "ENABLED",
                            "negative": False,
                            "type": "KEYWORD",
                            "effective_cpc_bid_micros": str(self.keyword_bid_micros),
                            "effective_cpc_bid_source": "AD_GROUP_CRITERION",
                            "keyword": {
                                "text": "voice transcription app",
                                "match_type": "EXACT",
                            },
                        },
                    },
                ),
                1,
                False,
            )
        if "FROM ad_group_ad " in query:
            return AdapterSearchResult(
                (
                    {
                        "campaign": {"id": "1"},
                        "ad_group": {"id": "1", "status": "ENABLED"},
                        "ad_group_ad": {
                            "status": "ENABLED",
                            "ad": {
                                "id": "3",
                                "type": "RESPONSIVE_SEARCH_AD",
                                "final_urls": ["https://example.com/landing"],
                            },
                            "policy_summary": {
                                "approval_status": "APPROVED",
                                "review_status": "REVIEWED",
                            },
                        },
                    },
                ),
                1,
                False,
            )
        if "FROM ad_group " in query and "WHERE campaign.id =" in query:
            return AdapterSearchResult(
                (
                    {
                        "campaign": {"id": "1"},
                        "ad_group": {
                            "id": "1",
                            "status": "ENABLED",
                            "cpc_bid_micros": str(self.ad_group_bid_micros),
                        }
                    },
                ),
                1,
                False,
            )
        if "FROM ad_group " in query:
            return AdapterSearchResult(
                (
                    {
                        "ad_group": {
                            "id": "1",
                            "resource_name": f"customers/{customer_id}/adGroups/1",
                            "name": "Group",
                            "status": "PAUSED",
                            "cpc_bid_micros": str(self.ad_group_bid_micros),
                        },
                        "campaign": {
                            "id": "1",
                            "status": self.status,
                            "campaign_budget": f"customers/{customer_id}/campaignBudgets/1",
                        },
                    },
                ),
                1,
                self.truncated,
            )
        if "FROM asset_group " in query:
            return AdapterSearchResult(
                (
                    {
                        "asset_group": {"id": "1", "name": "Group", "status": "PAUSED"},
                        "campaign": {
                            "id": "1",
                            "status": self.status,
                            "campaign_budget": f"customers/{customer_id}/campaignBudgets/1",
                        },
                    },
                ),
                1,
                self.truncated,
            )
        if "FROM campaign " in query:
            campaign = {
                "id": "1",
                "resource_name": f"customers/{customer_id}/campaigns/1",
                "name": "Campaign",
                "status": self.status,
                "campaign_budget": f"customers/{customer_id}/campaignBudgets/1",
            }
            if "campaign.advertising_channel_type" in query:
                campaign["advertising_channel_type"] = "SEARCH"
            if "campaign.bidding_strategy_type" in query:
                campaign["bidding_strategy_type"] = "MANUAL_CPC"
            if "campaign.manual_cpc.enhanced_cpc_enabled" in query:
                campaign["manual_cpc"] = {"enhanced_cpc_enabled": False}
            if "campaign.geo_target_type_setting" in query:
                campaign["geo_target_type_setting"] = {
                    "positive_geo_target_type": "PRESENCE",
                    "negative_geo_target_type": "PRESENCE",
                }
            return AdapterSearchResult(
                (
                    {
                        "campaign": campaign,
                        "campaign_budget": {
                            "resource_name": f"customers/{customer_id}/campaignBudgets/1",
                            "amount_micros": "1000000",
                        },
                    },
                ),
                1,
                self.truncated,
            )
        if "FROM recommendation" in query:
            return AdapterSearchResult(
                (
                    {
                        "recommendation": {
                            "resource_name": f"customers/{customer_id}/recommendations/1",
                            "type": "CAMPAIGN_BUDGET",
                            "dismissed": False,
                        }
                    },
                ),
                1,
                False,
            )
        if "FROM asset" in query and self.asset_created:
            return AdapterSearchResult(
                ({"asset": {"id": "1", "name": "New asset", "type": "TEXT"}},),
                1,
                False,
            )
        return AdapterSearchResult((), 0, False)

    async def accessible_customers(self, profile: Any) -> tuple[str, ...]:
        return ()

    async def field_metadata(
        self, profile: Any, field_names: list[str]
    ) -> tuple[dict[str, Any], ...]:
        return ()

    async def validate_query(self, profile: Any, customer_id: str, query: str) -> None:
        return None

    async def keyword_ideas(self, *args: Any, **kwargs: Any) -> AdapterSearchResult:
        return AdapterSearchResult((), 0, False)

    async def keyword_forecast(self, *args: Any, **kwargs: Any) -> tuple[dict[str, Any], ...]:
        return ()


class FakeWriteAdapter:
    def __init__(self, read: FakeReadAdapter) -> None:
        self.read = read
        self.validations = 0
        self.applies = 0
        self.error: AdapterError | None = None

    async def validate(self, profile: Any, plan: WritePlan) -> ValidationResult:
        self.validations += 1
        if plan.kind == "recommendation_apply":
            raise AdapterError(
                "validate_only unavailable", status="not_supported", code="not_supported"
            )
        return ValidationResult(valid=True, request_id="validation-request")

    async def apply(self, profile: Any, plan: WritePlan) -> ApplyResult:
        self.applies += 1
        if self.error is not None:
            raise self.error
        self.read.status = str(plan.after.get("status", self.read.status))
        if plan.kind == "campaign_targeting":
            self.read.targeting_configured = True
            return ApplyResult(
                state="applied",
                items=(
                    ApplyItemResult(
                        correlation_id="campaign-geo-mode",
                        status="applied",
                        resource_name="customers/1234567890/campaigns/1",
                    ),
                    ApplyItemResult(
                        correlation_id="location-1",
                        status="applied",
                        resource_name="customers/1234567890/campaignCriteria/1~2840",
                    ),
                    ApplyItemResult(
                        correlation_id="language-1",
                        status="applied",
                        resource_name="customers/1234567890/campaignCriteria/1~1000",
                    ),
                ),
                request_id="apply-request",
            )
        return ApplyResult(
            state="applied",
            items=(
                ApplyItemResult(
                    correlation_id="campaign",
                    status="applied",
                    resource_name="customers/1234567890/campaigns/1",
                ),
            ),
            request_id="apply-request",
        )


def _operator(
    tmp_path: Path,
    *,
    allow_writes: bool = True,
    profile_writes: bool = True,
    policy: WritePolicy | None = None,
    now: list[datetime] | None = None,
) -> tuple[OperatorService, FakeReadAdapter, FakeWriteAdapter, OperationJournal, list[WritePolicy]]:
    if os.name == "nt":
        pytest.skip("real write state fails closed without Windows ACL verification")
    config = _config(profile_writes)
    read = FakeReadAdapter()
    write = FakeWriteAdapter(read)
    journal = OperationJournal(tmp_path / "state")
    policies = [policy or _policy()]
    clock = now or [NOW]
    service = GoogleAdsService(config, read)
    operator = OperatorService(
        config,
        service,
        read,
        write,
        allow_writes=allow_writes,
        policy_loader=lambda: PolicyFile(Path("/synthetic/policies.json"), tuple(policies)),
        state_dir=tmp_path / "state",
        journal=journal,
        signer=ReceiptSigner(b"k" * 32),
        now=lambda: clock[0],
    )
    return operator, read, write, journal, policies


def _preview(operator: OperatorService, status: str = "PAUSED") -> dict[str, Any]:
    if status == "ENABLED":
        cast(FakeReadAdapter, operator._read_adapter).status = "PAUSED"
    return asyncio.run(
        operator.preview(
            "campaign_status",
            "operator",
            "1234567890",
            "safe",
            {"campaignId": "1", "status": status},
        )
    )


def test_preview_apply_readback_replay_inspect_and_list(tmp_path: Path) -> None:
    operator, read, write, journal, _ = _operator(tmp_path)
    preview = _preview(operator)
    operation = preview["operation"]
    assert operation["state"] == "previewed"
    assert operation["validation"]["valid"] is True
    receipt = operation["receipt"]
    stored = journal.inspect(operation["operationId"])
    assert any("campaign_budget.amount_micros" in query for query in stored.plan.readback_queries)
    assert stored.receipt_hash == receipt_hash(receipt)
    signed = operator._signer().verify(receipt)
    assert signed.plan_digest == stored.plan_digest == value_fingerprint(stored.plan.as_dict())
    assert signed.validation_digest == stored.validation_digest
    assert signed.policy_fingerprint == stored.policy_fingerprint
    assert "receipt" not in stored.public_dict()
    applied = asyncio.run(operator.apply(receipt))
    assert applied["operation"]["state"] == "applied"
    assert applied["operation"]["verification"]["state"] == "matched_after"
    assert write.validations == 1
    assert write.applies == 1
    query_count = len(read.queries)
    inspected = asyncio.run(operator.inspect(operation["operationId"]))
    assert inspected["operation"]["state"] == "applied"
    assert len(read.queries) == query_count
    listed = asyncio.run(operator.list("operator", 10))
    assert listed["items"][0]["operationId"] == operation["operationId"]
    with pytest.raises(ValidationError, match="used"):
        asyncio.run(operator.apply(receipt))


def test_targeting_preview_apply_and_direct_readback_are_plan_bound(tmp_path: Path) -> None:
    policy = _policy(permissions=frozenset({"campaign_targeting"}))
    operator, read, write, journal, _ = _operator(tmp_path, policy=policy)
    read.status = "PAUSED"
    read.targeting_configured = False
    preview = asyncio.run(
        operator.preview(
            "campaign_targeting",
            "operator",
            "1234567890",
            "safe",
            {
                "campaignId": "1",
                "geoTargetIds": ["2840"],
                "languageIds": ["1000"],
            },
        )
    )
    operation = preview["operation"]
    stored = journal.inspect(operation["operationId"])
    assert write.validations == 1
    assert stored.plan.kind == "campaign_targeting"
    assert stored.plan.after["currentCampaignDailyBudgetMicros"] == "1000000"
    applied = asyncio.run(operator.apply(operation["receipt"]))
    assert applied["operation"]["state"] == "applied"
    assert applied["operation"]["verification"]["state"] == "matched_after"
    assert write.applies == 1


@pytest.mark.parametrize(
    ("kind", "payload", "parent_query"),
    [
        (
            "ad_group_create",
            {"campaignId": "1", "name": "New group", "cpcBidMicros": "100000"},
            "FROM campaign WHERE campaign.id = 1 LIMIT 1",
        ),
        (
            "ad_group_update",
            {"adGroupId": "1", "status": "ENABLED"},
            "FROM ad_group WHERE ad_group.id = 1 LIMIT 1",
        ),
        (
            "ad_group_update",
            {"adGroupId": "1", "cpcBidMicros": "100000"},
            "FROM ad_group WHERE ad_group.id = 1 LIMIT 1",
        ),
        (
            "keyword_create",
            {
                "level": "AD_GROUP",
                "adGroupId": "1",
                "text": "new keyword",
                "matchType": "EXACT",
                "cpcBidMicros": "100000",
            },
            "FROM ad_group WHERE ad_group.id = 1 LIMIT 1",
        ),
        (
            "criterion_update",
            {
                "level": "AD_GROUP",
                "adGroupId": "1",
                "criterionId": "2",
                "status": "ENABLED",
            },
            "FROM ad_group WHERE ad_group.id = 1 LIMIT 1",
        ),
        (
            "criterion_update",
            {
                "level": "AD_GROUP",
                "adGroupId": "1",
                "criterionId": "2",
                "cpcBidMicros": "100000",
            },
            "FROM ad_group WHERE ad_group.id = 1 LIMIT 1",
        ),
        *[
            (
                "asset_link",
                {
                    "ownerType": owner_type,
                    "ownerId": "1",
                    "assetResourceName": "customers/1234567890/assets/1",
                    "fieldType": "HEADLINE",
                },
                parent_query,
            )
            for owner_type, parent_query in (
                ("CAMPAIGN", "FROM campaign WHERE campaign.id = 1 LIMIT 1"),
                ("AD_GROUP", "FROM ad_group WHERE ad_group.id = 1 LIMIT 1"),
                ("ASSET_GROUP", "FROM asset_group WHERE asset_group.id = 1 LIMIT 1"),
            )
        ],
        (
            "asset_group_create",
            {
                "campaignId": "1",
                "name": "New asset group",
                "finalUrl": "https://example.com",
                "assetResourceNames": ["customers/1234567890/assets/1"],
                "fieldTypes": ["HEADLINE"],
            },
            "FROM campaign WHERE campaign.id = 1 LIMIT 1",
        ),
    ],
)
def test_spend_affecting_child_previews_reach_validate_only_with_parent_budget(
    tmp_path: Path, kind: WriteKind, payload: dict[str, Any], parent_query: str
) -> None:
    policy = _policy(permissions=frozenset({kind}), allow_enable=True)
    operator, read, write, journal, _ = _operator(tmp_path, policy=policy)
    read.status = "PAUSED"

    result = asyncio.run(
        operator.preview(kind, "operator", "1234567890", "safe", payload)
    )

    operation = result["operation"]
    plan = journal.inspect(operation["operationId"]).plan
    assert operation["state"] == "previewed"
    assert write.validations == 1
    assert plan.risk["spendAffecting"] is True
    assert plan.after["currentCampaignDailyBudgetMicros"] == "1000000"
    assert plan.after["aggregateDailyBudgetMicros"] == "1000000"
    assert any(parent_query in query for query in read.queries)


def test_campaign_criterion_update_is_rejected_before_validate_only(tmp_path: Path) -> None:
    policy = _policy(permissions=frozenset({"criterion_update"}), allow_enable=True)
    operator, _, write, _, _ = _operator(tmp_path, policy=policy)
    with pytest.raises(ValidationError, match="AD_GROUP keywords"):
        asyncio.run(
            operator.preview(
                "criterion_update",
                "operator",
                "1234567890",
                "safe",
                {
                    "level": "CAMPAIGN",
                    "campaignId": "1",
                    "criterionId": "2",
                    "status": "ENABLED",
                },
            )
        )
    assert write.validations == 0


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("ad_group_update", {"adGroupId": "1", "status": "ENABLED"}),
        (
            "criterion_update",
            {
                "level": "AD_GROUP",
                "adGroupId": "1",
                "criterionId": "2",
                "status": "ENABLED",
            },
        ),
    ],
)
def test_child_enable_rejects_active_parent_before_validate_only(
    tmp_path: Path, kind: WriteKind, payload: dict[str, Any]
) -> None:
    policy = _policy(permissions=frozenset({kind}), allow_enable=True)
    operator, _, write, _, _ = _operator(tmp_path, policy=policy)
    with pytest.raises(ValidationError, match="PAUSED parent"):
        asyncio.run(
            operator.preview(kind, "operator", "1234567890", "safe", payload)
        )
    assert write.validations == 0


def test_drift_invalidates_receipt_before_rpc(tmp_path: Path) -> None:
    operator, read, write, journal, _ = _operator(tmp_path)
    preview = _preview(operator)
    read.status = "PAUSED"
    with pytest.raises(BlockedError, match="drifted"):
        asyncio.run(operator.apply(preview["operation"]["receipt"]))
    assert write.applies == 0
    assert journal.inspect(preview["operation"]["operationId"]).state == "expired"


def test_ambiguous_write_blocks_until_verify(tmp_path: Path) -> None:
    operator, read, write, _, _ = _operator(tmp_path)
    preview = _preview(operator)
    write.error = AdapterError("ambiguous", code="ambiguous_write")
    result = asyncio.run(operator.apply(preview["operation"]["receipt"]))
    assert result["operation"]["state"] == "committed_unverified"
    with pytest.raises(ValidationError, match="requires"):
        _preview(operator)
    still_pending = asyncio.run(operator.verify(preview["operation"]["operationId"]))
    assert still_pending["operation"]["state"] == "committed_unverified"
    with pytest.raises(ValidationError, match="unresolved customer write"):
        asyncio.run(
            operator.preview(
                "campaign_budget",
                "operator",
                "1234567890",
                "safe",
                {"budgetId": "2", "dailyBudgetMicros": "1000000"},
            )
        )
    read.status = "PAUSED"
    verified = asyncio.run(operator.verify(preview["operation"]["operationId"]))
    assert verified["operation"]["state"] == "applied"


def test_crash_after_applying_is_restart_recoverable(tmp_path: Path) -> None:
    operator, read, _, journal, _ = _operator(tmp_path)
    preview = _preview(operator)
    operation_id = preview["operation"]["operationId"]
    receipt = preview["operation"]["receipt"]
    journal.begin_apply(operation_id, receipt_hash(receipt), NOW)
    read.status = "PAUSED"
    recovered = asyncio.run(operator.verify(operation_id))
    assert recovered["operation"]["state"] == "applied"


def test_policy_is_reloaded_and_revocation_blocks_apply(tmp_path: Path) -> None:
    operator, _, write, _, policies = _operator(tmp_path)
    preview = _preview(operator)
    policies[0] = _policy(approved=False)
    with pytest.raises(BlockedError, match="approved"):
        asyncio.run(operator.apply(preview["operation"]["receipt"]))
    assert write.applies == 0


def test_changed_but_approved_policy_invalidates_plan_bound_receipt(tmp_path: Path) -> None:
    operator, _, write, journal, policies = _operator(tmp_path)
    preview = _preview(operator)
    changed = replace(
        _policy(approved=False),
        limits=replace(_policy().limits, max_bid_micros=400_000),
    )
    policies[0] = replace(
        changed,
        approval=PolicyApproval(
            status="approved",
            approved_at=NOW,
            expires_at=NOW + timedelta(days=30),
            policy_fingerprint=changed.fingerprint,
        ),
    )
    with pytest.raises(BlockedError, match="changed after preview"):
        asyncio.run(operator.apply(preview["operation"]["receipt"]))
    assert write.applies == 0
    assert journal.inspect(preview["operation"]["operationId"]).state == "expired"


def test_journal_plan_tamper_is_detected_before_write_rpc(tmp_path: Path) -> None:
    operator, _, write, journal, _ = _operator(tmp_path)
    preview = _preview(operator)
    operation_id = preview["operation"]["operationId"]
    stored = journal.inspect(operation_id)
    tampered = stored.plan.as_dict()
    tampered["items"][0]["resource"]["status"] = "ENABLED"
    with sqlite3.connect(journal.path) as connection:
        connection.execute(
            "UPDATE operations SET plan_json = ? WHERE operation_id = ?",
            (json.dumps(tampered), operation_id),
        )
    with pytest.raises(SecurityError, match="integrity"):
        asyncio.run(operator.apply(preview["operation"]["receipt"]))
    assert write.applies == 0


def test_runtime_profile_enable_and_partial_state_gates(tmp_path: Path) -> None:
    disabled, _, _, _, _ = _operator(tmp_path / "runtime", allow_writes=False)
    with pytest.raises(BlockedError, match="allow-writes"):
        _preview(disabled)
    profile_disabled, _, _, _, _ = _operator(tmp_path / "profile", profile_writes=False)
    with pytest.raises(BlockedError, match="allowWrites"):
        _preview(profile_disabled)
    enable_denied, _, _, _, _ = _operator(tmp_path / "enable")
    with pytest.raises(BlockedError, match="enabling"):
        _preview(enable_denied, "ENABLED")
    partial, read, _, _, _ = _operator(tmp_path / "partial")
    read.truncated = True
    with pytest.raises(BlockedError, match="truncated"):
        _preview(partial)


def test_campaign_policy_allowlist_blocks_preview_before_validate_only(tmp_path: Path) -> None:
    policy = _policy(allow_enable=True)
    operator, _, write, _, _ = _operator(tmp_path, policy=policy)

    with pytest.raises(BlockedError, match="allowlist"):
        asyncio.run(
            operator.preview(
                "campaign_status",
                "operator",
                "1234567890",
                "safe",
                {"campaignId": "2", "status": "PAUSED"},
            )
        )

    assert write.validations == 0


def test_receipt_expiry_and_invalid_scope_fail_closed(tmp_path: Path) -> None:
    clock = [NOW]
    operator, _, write, _, _ = _operator(tmp_path, now=clock)
    preview = _preview(operator)
    clock[0] = NOW + timedelta(minutes=11)
    with pytest.raises(BlockedError, match="expired"):
        asyncio.run(operator.apply(preview["operation"]["receipt"]))
    assert write.applies == 0
    assert operator._journal().inspect(preview["operation"]["operationId"]).state == "expired"
    with pytest.raises(ValidationError, match="signature"):
        asyncio.run(operator.apply(preview["operation"]["receipt"] + "x"))


def test_recommendation_preview_is_present_but_not_supported(tmp_path: Path) -> None:
    operator, _, _, _, _ = _operator(tmp_path)
    response = asyncio.run(
        operator.preview(
            "recommendation_apply",
            "operator",
            "1234567890",
            "safe",
            {"recommendationResourceName": ("customers/1234567890/recommendations/1")},
        )
    )
    assert response["status"] == "not_supported"
    assert "operation" not in response


def test_policy_aggregate_limit_and_terminal_verify(tmp_path: Path) -> None:
    pending = _policy(approved=False)
    pending = replace(
        pending,
        limits=replace(pending.limits, max_aggregate_daily_budget_micros=1),
    )
    policy = replace(
        pending,
        approval=PolicyApproval(
            status="approved",
            approved_at=NOW,
            expires_at=NOW + timedelta(days=30),
            policy_fingerprint=pending.fingerprint,
        ),
    )
    operator, _, _, _, _ = _operator(tmp_path / "limit", policy=policy)
    with pytest.raises(BlockedError, match="aggregate"):
        asyncio.run(
            operator.preview(
                "campaign_budget",
                "operator",
                "1234567890",
                "safe",
                {"budgetId": "1", "dailyBudgetMicros": "100"},
            )
        )
    terminal, _, _, _, _ = _operator(tmp_path / "terminal")
    preview = _preview(terminal)
    verified = asyncio.run(terminal.verify(preview["operation"]["operationId"]))
    assert verified["operation"]["state"] == "previewed"


@pytest.mark.parametrize("kind", ["asset_create", "campaign_budget"])
def test_policy_permission_is_checked_before_validate(kind: WriteKind, tmp_path: Path) -> None:
    operator, _, write, _, _ = _operator(tmp_path / kind)
    payload = (
        {"name": "Asset", "assetType": "TEXT", "text": "text"}
        if kind == "asset_create"
        else {"budgetId": "1", "dailyBudgetMicros": "100"}
    )
    if kind == "asset_create":
        with pytest.raises(BlockedError, match="permit"):
            asyncio.run(operator.preview(kind, "operator", "1234567890", "safe", payload))
        assert write.validations == 0


def test_keyword_bid_is_enforced_by_policy_before_validate(tmp_path: Path) -> None:
    pending = replace(
        _policy(approved=False),
        permissions=frozenset({"keyword_create"}),
        limits=replace(_policy().limits, max_bid_micros=50),
    )
    policy = replace(
        pending,
        approval=PolicyApproval(
            status="approved",
            approved_at=NOW,
            expires_at=NOW + timedelta(days=30),
            policy_fingerprint=pending.fingerprint,
        ),
    )
    operator, _, write, _, _ = _operator(tmp_path, policy=policy)
    with pytest.raises(BlockedError, match="bid"):
        asyncio.run(
            operator.preview(
                "keyword_create",
                "operator",
                "1234567890",
                "safe",
                {
                    "level": "AD_GROUP",
                    "adGroupId": "1",
                    "text": "keyword",
                    "matchType": "EXACT",
                    "cpcBidMicros": "100",
                },
            )
        )
    assert write.validations == 0


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("ad_group_update", {"adGroupId": "1", "status": "ENABLED"}),
        (
            "criterion_update",
            {
                "level": "AD_GROUP",
                "adGroupId": "1",
                "criterionId": "2",
                "status": "ENABLED",
            },
        ),
    ],
)
def test_status_only_child_enable_enforces_authoritative_current_bid(
    tmp_path: Path, kind: WriteKind, payload: dict[str, Any]
) -> None:
    pending = replace(
        _policy(
            approved=False,
            allow_enable=True,
            permissions=frozenset({kind}),
        ),
        limits=replace(_policy().limits, max_bid_micros=500_000),
    )
    policy = replace(
        pending,
        approval=PolicyApproval(
            status="approved",
            approved_at=NOW,
            expires_at=NOW + timedelta(days=30),
            policy_fingerprint=pending.fingerprint,
        ),
    )
    operator, read, write, _, _ = _operator(tmp_path, policy=policy)
    read.status = "PAUSED"
    read.ad_group_bid_micros = 999_999_999
    read.keyword_bid_micros = 999_999_999

    with pytest.raises(BlockedError, match="bid"):
        asyncio.run(
            operator.preview(kind, "operator", "1234567890", "safe", payload)
        )

    assert write.validations == 0


def test_campaign_enable_requires_current_budget_within_campaign_limit(tmp_path: Path) -> None:
    pending = replace(
        _policy(approved=False, allow_enable=True),
        limits=replace(_policy().limits, max_campaign_daily_budget_micros=1),
    )
    policy = replace(
        pending,
        approval=PolicyApproval(
            status="approved",
            approved_at=NOW,
            expires_at=NOW + timedelta(days=30),
            policy_fingerprint=pending.fingerprint,
        ),
    )
    operator, _, write, _, _ = _operator(tmp_path, policy=policy)
    with pytest.raises(BlockedError, match="campaign budget"):
        _preview(operator, "ENABLED")
    assert write.validations == 0


def test_campaign_enable_requires_current_bid_within_policy_limit(tmp_path: Path) -> None:
    pending = replace(
        _policy(approved=False, allow_enable=True),
        limits=replace(_policy().limits, max_bid_micros=50_000),
    )
    policy = replace(
        pending,
        approval=PolicyApproval(
            status="approved",
            approved_at=NOW,
            expires_at=NOW + timedelta(days=30),
            policy_fingerprint=pending.fingerprint,
        ),
    )
    operator, _, write, _, _ = _operator(tmp_path, policy=policy)

    with pytest.raises(BlockedError, match="bid"):
        _preview(operator, "ENABLED")

    assert write.validations == 0


def test_direct_readback_requires_observable_values_and_matching_strategy() -> None:
    assert not _matches_expected(
        {"campaign": {}}, {"resource_name": "customers/1234567890/campaigns/1"}
    )
    assert not _matches_expected(
        {"campaign": {"bidding_strategy_type": "MANUAL_CPC"}},
        {"maximize_conversions": {}},
    )
    assert _matches_expected(
        {
            "campaign": {
                "bidding_strategy_type": "TARGET_CPA",
                "target_cpa": {"target_cpa_micros": "500000"},
            }
        },
        {"target_cpa": {"target_cpa_micros": 500000}},
    )
    assert not _matches_expected(
        {
            "ad_group_ad": {
                "status": "PAUSED",
                "ad": {"name": "Ad", "type": "RESPONSIVE_SEARCH_AD"},
            }
        },
        {
            "status": "PAUSED",
            "ad": {
                "name": "Ad",
                "final_urls": ["https://example.com"],
                "responsive_search_ad": {
                    "headlines": [{"text": "One"}],
                    "descriptions": [{"text": "Description"}],
                },
            },
        },
        operation_type="ad_group_ad_operation",
    )
    assert not _matches_expected(
        {"campaign": {"status": "PAUSED"}},
        {
            "resource_name": "customers/1234567890/campaigns/1",
            "status": "PAUSED",
            "final_url_suffix": "source=test",
        },
        action="update",
        update_mask=("status", "final_url_suffix"),
    )
    assert _matches_expected(
        {
            "campaign": {
                "geo_target_type_setting": {
                    "positive_geo_target_type": "PRESENCE",
                    "negative_geo_target_type": "PRESENCE",
                }
            }
        },
        {
            "resource_name": "customers/1234567890/campaigns/1",
            "geo_target_type_setting": {
                "positive_geo_target_type": "PRESENCE",
                "negative_geo_target_type": "PRESENCE",
            },
        },
        action="update",
        update_mask=(
            "geo_target_type_setting.positive_geo_target_type",
            "geo_target_type_setting.negative_geo_target_type",
        ),
    )
    assert not _matches_expected(
        {"campaign": {}},
        {"geo": {"child": "value"}},
        action="update",
        update_mask=("geo.missing.child",),
    )
    assert not _matches_expected(
        {"campaign": {}},
        {"geo": {"child": "value"}},
        action="update",
        update_mask=("geo.child", "geo.child.grandchild"),
    )


def test_partial_authoritative_create_targets_cannot_verify_whole_plan(tmp_path: Path) -> None:
    operator, _, _, journal, _ = _operator(tmp_path)

    class AssetRead(FakeReadAdapter):
        async def search(
            self, profile: Any, customer_id: str, query: str
        ) -> AdapterSearchResult:
            if "asset.resource_name" in query and "assets/1" in query:
                return AdapterSearchResult(
                    (
                        {
                            "asset": {
                                "resource_name": f"customers/{customer_id}/assets/1",
                                "name": "One",
                                "type": "TEXT",
                                "text_asset": {"text": "First"},
                            }
                        },
                    ),
                    1,
                    False,
                )
            return AdapterSearchResult((), 0, False)

    operator._read_adapter = AssetRead()
    plan = WritePlan(
        profile="operator",
        customer_id="1234567890",
        policy_id="safe",
        kind="asset_create",
        related_key="asset-batch",
        before={"objects": [{"index": 0, "items": []}]},
        after={"count": 2},
        items=(
            MutationItem(
                "asset_operation",
                "create",
                {
                    "resource_name": "customers/1234567890/assets/-1",
                    "name": "One",
                    "text_asset": {"text": "First"},
                },
                correlation_id="one",
            ),
            MutationItem(
                "asset_operation",
                "create",
                {
                    "resource_name": "customers/1234567890/assets/-2",
                    "name": "Two",
                    "text_asset": {"text": "Second"},
                },
                correlation_id="two",
            ),
        ),
        readback_queries=("SELECT asset.id FROM asset WHERE asset.name = 'batch' LIMIT 2",),
        monetary_delta={"spendMicros": "0"},
        risk={"spendAffecting": False},
    )
    validation = {"valid": True}
    journal.create_preview(
        operation_id="partial-create",
        receipt_hash_value="partial-hash",
        plan=plan,
        current_fingerprint=value_fingerprint(plan.before),
        plan_digest=value_fingerprint(plan.as_dict()),
        validation_digest=value_fingerprint(validation),
        policy_fingerprint=_policy().fingerprint,
        validation=validation,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    record = journal.begin_apply("partial-create", "partial-hash", NOW)
    response = ApplyResult(
        state="applied",
        items=(
            ApplyItemResult(
                correlation_id="one",
                status="applied",
                resource_name="customers/1234567890/assets/1",
            ),
        ),
    )
    readback = asyncio.run(
        operator._readback(_config().profile("operator"), record, response)
    )
    assert readback.state == "partial"
    assert len(readback.objects) == 2


def test_temporary_update_target_with_unchanged_state_is_not_verified(tmp_path: Path) -> None:
    operator, _, _, journal, _ = _operator(tmp_path)
    before = {"objects": [{"index": 0, "items": []}]}
    plan = WritePlan(
        profile="operator",
        customer_id="1234567890",
        policy_id="safe",
        kind="campaign_status",
        related_key="campaign:temporary",
        before=before,
        after={"status": "PAUSED"},
        items=(
            MutationItem(
                "campaign_operation",
                "update",
                {
                    "resource_name": "customers/1234567890/campaigns/-1",
                    "status": "PAUSED",
                },
                ("status",),
                "campaign",
            ),
        ),
        readback_queries=("SELECT asset.id FROM asset WHERE asset.id = -1 LIMIT 1",),
        monetary_delta={"spendMicros": "0"},
        risk={"spendAffecting": False},
    )
    validation = {"valid": True}
    journal.create_preview(
        operation_id="temporary-update",
        receipt_hash_value="temporary-hash",
        plan=plan,
        current_fingerprint=value_fingerprint(before),
        plan_digest=value_fingerprint(plan.as_dict()),
        validation_digest=value_fingerprint(validation),
        policy_fingerprint=_policy().fingerprint,
        validation=validation,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    record = journal.begin_apply("temporary-update", "temporary-hash", NOW)
    readback = asyncio.run(
        operator._readback(_config().profile("operator"), record, response=None)
    )
    assert readback.state == "matched_before"


def test_atomic_create_without_authoritative_name_stays_unverified(tmp_path: Path) -> None:
    operator, read, _, journal, _ = _operator(tmp_path)
    plan = WritePlan(
        profile="operator",
        customer_id="1234567890",
        policy_id="safe",
        kind="asset_create",
        related_key="asset-name:new asset",
        before={"objects": [{"index": 0, "items": []}]},
        after={"name": "New asset", "assetType": "TEXT"},
        items=(
            MutationItem(
                "asset_operation",
                "create",
                {"name": "New asset", "text_asset": {"text": "Headline"}},
                correlation_id="asset",
            ),
        ),
        readback_queries=(
            "SELECT asset.id, asset.name, asset.type FROM asset "
            "WHERE asset.name = 'New asset' LIMIT 2",
        ),
        monetary_delta={"spendMicros": "0"},
        risk={"spendAffecting": False},
    )
    journal.create_preview(
        operation_id="crashed-create",
        receipt_hash_value="receipt-hash",
        plan=plan,
        current_fingerprint=value_fingerprint(plan.before),
        plan_digest=value_fingerprint(plan.as_dict()),
        validation_digest=value_fingerprint({"valid": True}),
        policy_fingerprint=_policy().fingerprint,
        validation={"valid": True},
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    journal.begin_apply("crashed-create", "receipt-hash", NOW)
    read.asset_created = True
    verified = asyncio.run(operator.verify("crashed-create"))
    assert verified["operation"]["state"] == "committed_unverified"


def test_operator_windows_state_guard_is_separate_from_pure_logic_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(journal_module.os, "name", "nt")
    with pytest.raises(SecurityError, match="Windows ACL"):
        OperationJournal(tmp_path / "state")


@pytest.mark.skipif(os.name == "nt", reason="POSIX write-state contract")
def test_lazy_state_dependencies_and_missing_customer_context(tmp_path: Path) -> None:
    config = _config()
    read = FakeReadAdapter()
    operator = OperatorService(
        config,
        GoogleAdsService(config, read),
        read,
        FakeWriteAdapter(read),
        allow_writes=True,
        state_dir=tmp_path / "state",
    )
    assert operator._journal() is operator._journal()
    assert operator._signer() is operator._signer()

    class MissingContext:
        async def customer_context(self, profile: Any, customer_id: str) -> dict[str, Any]:
            return {"currencyCode": None}

    missing = OperatorService(
        config,
        cast(Any, MissingContext()),
        read,
        FakeWriteAdapter(read),
        allow_writes=True,
        policy_loader=lambda: PolicyFile(Path("/synthetic"), (_policy(),)),
        journal=operator._journal(),
        signer=operator._signer(),
    )
    with pytest.raises(BlockedError, match="currency"):
        asyncio.run(
            missing._policy(
                profile=config.profile("operator"),
                customer_id="1234567890",
                policy_id="safe",
                kind="campaign_status",
            )
        )


def test_operator_policy_math_and_state_resolution_defensive_branches() -> None:
    policy = _policy(allow_enable=True)
    budget_resource = "customers/1234567890/campaignBudgets/1"
    valid_aggregate = {
        "objects": [
            {
                "items": [
                    {
                        "campaign": {
                            "id": "1",
                            "status": "PAUSED",
                            "campaign_budget": budget_resource,
                        },
                        "campaign_budget": {
                            "resource_name": budget_resource,
                            "amount_micros": "100",
                        },
                    }
                ]
            }
        ]
    }
    assert OperatorService._aggregate_budget(
        valid_aggregate, object_index=0, customer_id="1234567890"
    ) == 100
    for before, object_index in (
        ({}, 0),
        ({"objects": []}, 0),
        ({"objects": [{}]}, 0),
        ({"objects": [{"items": ["malformed"]}]}, 0),
    ):
        with pytest.raises(BlockedError, match=r"incomplete|malformed"):
            OperatorService._aggregate_budget(
                before,
                object_index=object_index,
                customer_id="1234567890",
            )
    def aggregate_row(
        *,
        identifier: str = "1",
        status: str = "PAUSED",
        campaign_budget: str = budget_resource,
        resource_name: str = budget_resource,
        amount: object = "100",
    ) -> dict[str, Any]:
        return {
            "campaign": {
                "id": identifier,
                "status": status,
                "campaign_budget": campaign_budget,
            },
            "campaign_budget": {
                "resource_name": resource_name,
                "amount_micros": amount,
            },
        }

    malformed_rows = [
        [aggregate_row(amount="invalid")],
        [aggregate_row(amount=None)],
        [aggregate_row(amount="-1")],
        [aggregate_row(status="REMOVED")],
        [aggregate_row(resource_name="customers/1234567890/campaignBudgets/2")],
        [aggregate_row(), aggregate_row()],
        [{"campaign": {"id": "1"}}],
    ]
    for rows in malformed_rows:
        with pytest.raises(BlockedError, match="malformed"):
            OperatorService._aggregate_budget(
                {"objects": [{"items": rows}]},
                object_index=0,
                customer_id="1234567890",
            )
    aggregate_query = aggregate_budget_query()
    base = WritePlan(
        profile="operator",
        customer_id="1234567890",
        policy_id="safe",
        kind="campaign_status",
        related_key="campaign:1",
        before={"objects": [{"items": []}]},
        after={"status": "PAUSED"},
        items=(),
        readback_queries=(aggregate_query,),
        monetary_delta={"spendMicros": None},
        risk={"spendAffecting": False},
    )
    assert OperatorService._enforce_policy(policy, base).after == {"status": "PAUSED"}
    with pytest.raises(BlockedError, match="evidence is missing"):
        OperatorService._enforce_policy(
            policy,
            replace(base, readback_queries=()),
        )
    with pytest.raises(BlockedError, match="could not be bounded"):
        OperatorService._enforce_policy(
            policy,
            replace(base, risk={"spendAffecting": True}),
        )
    bounded = replace(
        base,
        after={
            "campaignId": "1",
            "status": "ENABLED",
            "currentCampaignDailyBudgetMicros": "100",
        },
        risk={"spendAffecting": True},
    )
    assert OperatorService._enforce_policy(policy, bounded).after[
        "aggregateDailyBudgetMicros"
    ] == "0"
    with pytest.raises(BlockedError, match="enable policy allowlist"):
        OperatorService._enforce_policy(
            replace(policy, campaign_ids=frozenset()),
            bounded,
        )

    matched_before = ReadbackResult(state="matched_before")
    matched_after = ReadbackResult(state="matched_after")
    partial = ReadbackResult(state="partial")
    assert OperatorService._resolve_state(
        ApplyResult(state="partial"), None, matched_after
    ) == "partial"
    assert OperatorService._resolve_state(None, None, partial) == "partial"
    assert OperatorService._resolve_state(
        None, AdapterError("failed", code="deterministic"), matched_before
    ) == "failed"
    assert OperatorService._resolve_state(
        ApplyResult(state="failed"), None, matched_before
    ) == "failed"
    nested = {"root": [{"child": {"value": 1}}, "ignored"]}
    assert _walk(nested) == [nested, {"child": {"value": 1}}, {"value": 1}]


def test_readback_without_direct_targets_is_inconclusive_after_state_drift(
    tmp_path: Path,
) -> None:
    operator, _, _, journal, _ = _operator(tmp_path)
    plan = WritePlan(
        profile="operator",
        customer_id="1234567890",
        policy_id="safe",
        kind="campaign_status",
        related_key="campaign:none",
        before={"objects": []},
        after={"status": "PAUSED"},
        items=(),
        readback_queries=("SELECT campaign.id FROM campaign LIMIT 1",),
        monetary_delta={"spendMicros": "0"},
        risk={"spendAffecting": False},
    )
    validation = {"valid": True}
    journal.create_preview(
        operation_id="no-targets",
        receipt_hash_value="no-targets-hash",
        plan=plan,
        current_fingerprint=value_fingerprint(plan.before),
        plan_digest=value_fingerprint(plan.as_dict()),
        validation_digest=value_fingerprint(validation),
        policy_fingerprint=_policy().fingerprint,
        validation=validation,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    record = journal.begin_apply("no-targets", "no-targets-hash", NOW)

    readback = asyncio.run(
        operator._readback(_config().profile("operator"), record, response=None)
    )

    assert readback.state == "inconclusive"


def test_preview_validation_failures_apply_runtime_and_request_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operator, _, write, _, _ = _operator(tmp_path / "validation")

    async def invalid_validation(profile: Any, plan: WritePlan) -> ValidationResult:
        return ValidationResult(valid=False)

    monkeypatch.setattr(write, "validate", invalid_validation)
    with pytest.raises(BlockedError, match="validate_only"):
        _preview(operator)

    async def unavailable_validation(profile: Any, plan: WritePlan) -> ValidationResult:
        raise AdapterError("unavailable", code="unavailable")

    monkeypatch.setattr(write, "validate", unavailable_validation)
    with pytest.raises(AdapterError, match="unavailable"):
        _preview(operator)

    enabled, _, enabled_write, _, _ = _operator(tmp_path / "apply")
    preview = _preview(enabled)
    enabled._allow_writes = False
    with pytest.raises(BlockedError, match="operations_apply"):
        asyncio.run(enabled.apply(preview["operation"]["receipt"]))
    enabled._allow_writes = True
    enabled_write.error = AdapterError(
        "rejected",
        code="deterministic",
        request_id="request-1",
    )
    failed = asyncio.run(enabled.apply(preview["operation"]["receipt"]))
    assert failed["operation"]["state"] == "failed"
    assert failed["operation"]["result"]["requestId"] == "request-1"


def test_receipt_scope_helpers_listing_and_result_rehydration(tmp_path: Path) -> None:
    operator, _, _, journal, _ = _operator(tmp_path)
    preview = _preview(operator)
    stored = journal.inspect(preview["operation"]["operationId"])
    payload = operator._signer().verify(preview["operation"]["receipt"])
    with pytest.raises(BlockedError, match="scope"):
        operator._verify_receipt_scope(replace(payload, plan_digest="0" * 64), stored)

    empty, _, _, empty_journal, _ = _operator(tmp_path / "empty")
    assert asyncio.run(empty.list("operator"))["items"] == []
    assert asyncio.run(empty.list())["items"] == []
    no_profiles = OperatorService(
        AccountsConfig(Path("/synthetic/empty.json"), ()),
        empty._read_service,
        empty._read_adapter,
        empty._write_adapter,
        allow_writes=True,
        journal=empty_journal,
        signer=ReceiptSigner(b"k" * 32),
    )
    with pytest.raises(ValidationError, match="At least one"):
        asyncio.run(no_profiles.list())

    assert _apply_result_from_record(stored) is None
    hydrated = _apply_result_from_record(
        replace(
            stored,
            result={
                "state": "applied",
                "requestId": "request",
                "items": [
                    "ignored",
                    {"correlationId": "one", "status": "applied"},
                    {
                        "correlationId": "two",
                        "status": "failed",
                        "resourceName": "customers/1234567890/campaigns/2",
                        "errorCode": "error",
                    },
                ],
            },
        )
    )
    assert hydrated is not None
    assert hydrated.request_id == "request"
    assert hydrated.items[0].resource_name is None
    assert hydrated.items[1].error_code == "error"


def test_verify_preserves_partial_readback_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operator, _, write, _, _ = _operator(tmp_path)
    preview = _preview(operator)
    write.error = AdapterError("ambiguous", code="ambiguous_write")
    asyncio.run(operator.apply(preview["operation"]["receipt"]))

    async def partial_readback(
        profile: Any, record: Any, response: ApplyResult | None
    ) -> ReadbackResult:
        return ReadbackResult(state="partial", objects=({"matched": True},))

    monkeypatch.setattr(operator, "_readback", partial_readback)
    result = asyncio.run(operator.verify(preview["operation"]["operationId"]))
    assert result["operation"]["state"] == "partial"


def test_direct_query_customer_id_and_exact_comparator_branches() -> None:
    assert _customer_id("123-456-7890") == "1234567890"
    with pytest.raises(ValidationError, match="10 digits"):
        _customer_id("not-an-id")
    assert _direct_query("unknown", "resource") is None
    assert _direct_query("campaign_operation", None) is None
    escaped = _direct_query("campaign_operation", "customers/1/campaigns/a'b\\c")
    assert escaped is not None
    assert "a\\'b\\\\c" in escaped

    assert not _matches_expected({}, {}, operation_type="unknown")
    assert not _matches_expected({"campaign": []}, {"name": "Campaign"})
    assert not _matches_expected(
        {"campaign": {"status": "PAUSED"}},
        {"status": "PAUSED"},
        action="update",
        update_mask=("status", "name"),
    )
    assert not _matches_expected(
        {"campaign": {"network_settings": "invalid"}},
        {"network_settings": {"target_google_search": True}},
    )
    assert not _matches_expected(
        {"campaign": {"network_settings": {}}},
        {"network_settings": {"target_google_search": True}},
    )
    assert not _matches_expected(
        {"campaign": {"network_settings": {"target_google_search": True}}},
        {"network_settings": {"target_google_search": False}},
    )
    assert not _matches_expected(
        {"campaign": {"bidding_strategy_type": "MANUAL_CPC", "target_cpa": {}}},
        {"target_cpa": {"target_cpa_micros": 1}},
    )
    assert _matches_expected(
        {"campaign": {"bidding_strategy_type": "MAXIMIZE_CONVERSIONS"}},
        {"maximize_conversions": {}},
    )
    assert not _matches_expected(
        {"campaign": {"bidding_strategy_type": "MANUAL_CPC"}},
        {"maximize_conversions": {}},
    )
    assert not _matches_expected(
        {"asset_group": {"final_urls": "https://example.com"}},
        {"final_urls": ["https://example.com"]},
        operation_type="asset_group_operation",
    )
    assert not _matches_expected(
        {"asset_group": {"final_urls": []}},
        {"final_urls": ["https://example.com"]},
        operation_type="asset_group_operation",
    )
    assert not _matches_expected(
        {"asset_group": {"final_urls": ["https://other.example"]}},
        {"final_urls": ["https://example.com"]},
        operation_type="asset_group_operation",
    )
    assert _matches_expected(
        {"ad_group": {"type": "SEARCH_STANDARD"}},
        {"type_": "SEARCH_STANDARD"},
        operation_type="ad_group_operation",
    )
    assert _matches_expected(
        {"ad_group": {"type": None, "type_": "SEARCH_STANDARD"}},
        {"type_": "SEARCH_STANDARD"},
        operation_type="ad_group_operation",
    )
    implicit_budget = {
        "campaign_budget": {
            "name": "Campaign",
            "amount_micros": "1000000",
            "delivery_method": "STANDARD",
            "explicitly_shared": False,
        }
    }
    expected_implicit_budget = {
        "name": "Campaign budget",
        "amount_micros": 1_000_000,
        "delivery_method": "STANDARD",
        "explicitly_shared": False,
    }
    assert _matches_expected(
        implicit_budget,
        expected_implicit_budget,
        operation_type="campaign_budget_operation",
    )
    assert not _matches_expected(
        implicit_budget,
        {**expected_implicit_budget, "amount_micros": 2_000_000},
        operation_type="campaign_budget_operation",
    )
    assert not _matches_expected(
        {"campaign": {}},
        {"resource_name": "customers/1234567890/campaigns/-1"},
    )
    assert not _matches_expected(
        {
            "campaign": {
                "campaign_budget": "customers/1234567890/campaignBudgets/10",
                "name": "Campaign",
            }
        },
        {
            "campaign_budget": "customers/1234567890/campaignBudgets/-1",
            "name": "Campaign",
        },
    )
    assert _matches_expected(
        {
            "campaign": {
                "campaign_budget": "customers/1234567890/campaignBudgets/10",
                "name": "Campaign",
            }
        },
        {
            "campaign_budget": "customers/1234567890/campaignBudgets/-1",
            "name": "Campaign",
        },
        temporary_names={
            "customers/1234567890/campaignBudgets/-1": (
                "customers/1234567890/campaignBudgets/10"
            )
        },
    )
