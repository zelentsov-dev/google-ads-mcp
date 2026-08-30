from __future__ import annotations

import json
import os
import stat
import tempfile
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from google_ads_mcp.config import normalize_customer_id
from google_ads_mcp.constants import DEFAULT_POLICY_ENV, DEFAULT_POLICY_PATH
from google_ads_mcp.errors import ConfigError, SecurityError, ValidationError
from google_ads_mcp.receipts import value_fingerprint
from google_ads_mcp.security import read_owner_only_text
from google_ads_mcp.write_models import WriteKind

_WRITE_KINDS = frozenset(
    {
        "search_campaign_create",
        "performance_max_campaign_create",
        "app_campaign_create",
        "campaign_update",
        "campaign_targeting",
        "campaign_status",
        "campaign_budget",
        "campaign_bidding",
        "ad_group_create",
        "ad_group_update",
        "keyword_create",
        "negative_keyword_create",
        "criterion_update",
        "asset_create",
        "asset_link",
        "asset_group_create",
        "ad_create",
        "recommendation_apply",
        "recommendation_dismiss",
    }
)


def resolve_policy_path(cli_path: str | None = None) -> Path:
    raw = cli_path or os.environ.get(DEFAULT_POLICY_ENV) or DEFAULT_POLICY_PATH
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (Path.cwd() / path).absolute()


def _micros(value: object, *, field: str) -> int:
    rendered = str(value)
    if not rendered.isascii() or not rendered.isdigit():
        raise ConfigError(f"{field} must be a non-negative integer string")
    return int(rendered)


def _timestamp(value: object, *, field: str, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{field} must be an RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConfigError(f"{field} must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise ConfigError(f"{field} must include a time zone")
    return parsed.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class PolicyLimits:
    max_aggregate_daily_budget_micros: int
    max_campaign_daily_budget_micros: int
    max_spend_change_micros_per_operation: int
    max_bid_micros: int

    def as_dict(self) -> dict[str, str]:
        return {
            "maxAggregateDailyBudgetMicros": str(self.max_aggregate_daily_budget_micros),
            "maxCampaignDailyBudgetMicros": str(self.max_campaign_daily_budget_micros),
            "maxSpendChangeMicrosPerOperation": str(self.max_spend_change_micros_per_operation),
            "maxBidMicros": str(self.max_bid_micros),
        }


@dataclass(frozen=True, slots=True)
class PolicyApproval:
    status: Literal["pending", "approved", "revoked"]
    approved_at: datetime | None
    expires_at: datetime | None
    policy_fingerprint: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "approvedAt": _format_time(self.approved_at),
            "expiresAt": _format_time(self.expires_at),
            "policyFingerprint": self.policy_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class WritePolicy:
    policy_id: str
    profile: str
    customer_id: str
    currency_code: str
    permissions: frozenset[WriteKind]
    allow_campaign_enable: bool
    campaign_ids: frozenset[str]
    limits: PolicyLimits
    approval: PolicyApproval

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "policyId": self.policy_id,
            "profile": self.profile,
            "customerId": self.customer_id,
            "currencyCode": self.currency_code,
            "permissions": sorted(self.permissions),
            "allowCampaignEnable": self.allow_campaign_enable,
            "campaignIds": sorted(self.campaign_ids),
            "limits": self.limits.as_dict(),
        }

    def as_dict(self) -> dict[str, Any]:
        result = self.unsigned_dict()
        result["approval"] = self.approval.as_dict()
        return result

    @property
    def fingerprint(self) -> str:
        return value_fingerprint(self.unsigned_dict())

    def require_active(
        self,
        *,
        profile: str,
        customer_id: str,
        currency_code: str,
        kind: WriteKind,
        now: datetime,
    ) -> None:
        if self.profile != profile or self.customer_id != customer_id:
            raise ValidationError(
                "The write policy does not match the selected profile and customer",
                code="policy_scope_mismatch",
            )
        if self.currency_code != currency_code:
            raise ValidationError(
                "The write policy currency does not match the customer currency",
                code="policy_currency_mismatch",
            )
        if kind not in self.permissions:
            raise ValidationError(
                "The write policy does not permit this operation",
                code="policy_permission_denied",
            )
        approval = self.approval
        if (
            approval.status != "approved"
            or approval.expires_at is None
            or approval.policy_fingerprint != self.fingerprint
            or now.astimezone(UTC) >= approval.expires_at
        ):
            raise ValidationError(
                "The write policy is not currently approved",
                code="policy_not_approved",
            )

    def enforce_money(
        self, monetary_delta: dict[str, str | None], after: dict[str, Any]
    ) -> None:
        raw_delta = monetary_delta.get("spendMicros")
        if raw_delta is not None:
            absolute = abs(int(raw_delta))
            if absolute > self.limits.max_spend_change_micros_per_operation:
                raise ValidationError(
                    "The monetary change exceeds the policy operation limit",
                    code="policy_limit_exceeded",
                )
        budget = after.get("dailyBudgetMicros") or after.get(
            "currentCampaignDailyBudgetMicros"
        )
        if budget is not None and int(str(budget)) > self.limits.max_campaign_daily_budget_micros:
            raise ValidationError(
                "The requested campaign budget exceeds the policy limit",
                code="policy_limit_exceeded",
            )
        bid = after.get("bidMicros") or after.get("targetCpaMicros")
        if bid is not None and int(str(bid)) > self.limits.max_bid_micros:
            raise ValidationError(
                "The requested bid exceeds the policy limit",
                code="policy_limit_exceeded",
            )


@dataclass(frozen=True, slots=True)
class PolicyFile:
    path: Path
    policies: tuple[WritePolicy, ...]

    def policy(self, policy_id: str) -> WritePolicy:
        for policy in self.policies:
            if policy.policy_id == policy_id:
                return policy
        raise ValidationError("The requested write policy does not exist", code="policy_not_found")

    def as_document(self) -> dict[str, Any]:
        return {"schemaVersion": 1, "policies": [policy.as_dict() for policy in self.policies]}


def _format_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _strict_keys(value: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ConfigError(f"Unknown policy key at {field}: {unknown[0]}")


def _parse_policy(value: object, *, index: int) -> WritePolicy:
    if not isinstance(value, dict):
        raise ConfigError(f"policies[{index}] must be an object")
    item = cast(dict[str, Any], value)
    _strict_keys(
        item,
        {
            "policyId",
            "profile",
            "customerId",
            "currencyCode",
            "permissions",
            "allowCampaignEnable",
            "campaignIds",
            "limits",
            "approval",
        },
        f"policies[{index}]",
    )
    policy_id = item.get("policyId")
    profile = item.get("profile")
    currency = item.get("currencyCode")
    if not isinstance(policy_id, str) or not policy_id or len(policy_id) > 64:
        raise ConfigError(f"policies[{index}].policyId is invalid")
    if not isinstance(profile, str) or not profile or len(profile) > 64:
        raise ConfigError(f"policies[{index}].profile is invalid")
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha():
        raise ConfigError(f"policies[{index}].currencyCode must be a three-letter code")
    raw_permissions = item.get("permissions")
    if not isinstance(raw_permissions, list) or not raw_permissions:
        raise ConfigError(f"policies[{index}].permissions must be a non-empty array")
    permissions = frozenset(str(permission) for permission in raw_permissions)
    unknown_permissions = sorted(permissions - _WRITE_KINDS)
    if unknown_permissions:
        raise ConfigError(f"Unsupported policy permission: {unknown_permissions[0]}")
    raw_campaign_ids = item.get("campaignIds", [])
    if not isinstance(raw_campaign_ids, list) or len(raw_campaign_ids) > 100:
        raise ConfigError(f"policies[{index}].campaignIds must be an array of at most 100 IDs")
    campaign_ids: list[str] = []
    for campaign_index, raw_campaign_id in enumerate(raw_campaign_ids):
        if (
            not isinstance(raw_campaign_id, str)
            or not raw_campaign_id.isascii()
            or not raw_campaign_id.isdigit()
            or len(raw_campaign_id) > 20
        ):
            raise ConfigError(
                f"policies[{index}].campaignIds[{campaign_index}] must be a numeric string"
            )
        campaign_ids.append(raw_campaign_id)
    if len(set(campaign_ids)) != len(campaign_ids):
        raise ConfigError(f"policies[{index}].campaignIds must not contain duplicates")
    allow_campaign_enable = item.get("allowCampaignEnable") is True
    if allow_campaign_enable and not campaign_ids:
        raise ConfigError(
            f"policies[{index}].campaignIds must contain at least one ID when enabling is allowed"
        )
    limits_raw = item.get("limits")
    if not isinstance(limits_raw, dict):
        raise ConfigError(f"policies[{index}].limits must be an object")
    limits_map = cast(dict[str, Any], limits_raw)
    _strict_keys(
        limits_map,
        {
            "maxAggregateDailyBudgetMicros",
            "maxCampaignDailyBudgetMicros",
            "maxSpendChangeMicrosPerOperation",
            "maxBidMicros",
        },
        f"policies[{index}].limits",
    )
    limits = PolicyLimits(
        max_aggregate_daily_budget_micros=_micros(
            limits_map.get("maxAggregateDailyBudgetMicros"),
            field="maxAggregateDailyBudgetMicros",
        ),
        max_campaign_daily_budget_micros=_micros(
            limits_map.get("maxCampaignDailyBudgetMicros"),
            field="maxCampaignDailyBudgetMicros",
        ),
        max_spend_change_micros_per_operation=_micros(
            limits_map.get("maxSpendChangeMicrosPerOperation"),
            field="maxSpendChangeMicrosPerOperation",
        ),
        max_bid_micros=_micros(limits_map.get("maxBidMicros"), field="maxBidMicros"),
    )
    if (
        min(
            limits.max_aggregate_daily_budget_micros,
            limits.max_campaign_daily_budget_micros,
            limits.max_spend_change_micros_per_operation,
            limits.max_bid_micros,
        )
        <= 0
    ):
        raise ConfigError("All monetary policy limits must be greater than zero")
    if limits.max_campaign_daily_budget_micros > limits.max_aggregate_daily_budget_micros:
        raise ConfigError("Campaign budget limit must not exceed the aggregate budget limit")
    approval_raw = item.get("approval")
    if not isinstance(approval_raw, dict):
        raise ConfigError(f"policies[{index}].approval must be an object")
    approval_map = cast(dict[str, Any], approval_raw)
    _strict_keys(
        approval_map,
        {"status", "approvedAt", "expiresAt", "policyFingerprint"},
        f"policies[{index}].approval",
    )
    status = approval_map.get("status")
    if status not in {"pending", "approved", "revoked"}:
        raise ConfigError(f"policies[{index}].approval.status is invalid")
    approval = PolicyApproval(
        status=status,
        approved_at=_timestamp(
            approval_map.get("approvedAt"), field="approval.approvedAt", optional=True
        ),
        expires_at=_timestamp(
            approval_map.get("expiresAt"), field="approval.expiresAt", optional=True
        ),
        policy_fingerprint=(
            str(approval_map["policyFingerprint"])
            if approval_map.get("policyFingerprint") is not None
            else None
        ),
    )
    if approval.status == "approved" and (
        approval.approved_at is None
        or approval.expires_at is None
        or approval.policy_fingerprint is None
        or approval.expires_at <= approval.approved_at
        or approval.expires_at - approval.approved_at > timedelta(days=30)
    ):
        raise ConfigError("Approved policy requires a valid approval of at most 30 days")
    customer_id = normalize_customer_id(
        item.get("customerId"), field=f"policies[{index}].customerId", required=True
    )
    assert customer_id is not None
    return WritePolicy(
        policy_id=policy_id,
        profile=profile,
        customer_id=customer_id,
        currency_code=currency.upper(),
        permissions=cast(frozenset[WriteKind], permissions),
        allow_campaign_enable=allow_campaign_enable,
        campaign_ids=frozenset(campaign_ids),
        limits=limits,
        approval=approval,
    )


def parse_policies(value: object, *, path: Path) -> PolicyFile:
    if not isinstance(value, dict):
        raise ConfigError("Policy document root must be an object")
    root = cast(dict[str, Any], value)
    _strict_keys(root, {"schemaVersion", "policies"}, "$")
    if root.get("schemaVersion") != 1:
        raise ConfigError("Policy schemaVersion must be 1")
    raw_policies = root.get("policies")
    if not isinstance(raw_policies, list):
        raise ConfigError("policies must be an array")
    policies = tuple(_parse_policy(item, index=index) for index, item in enumerate(raw_policies))
    identifiers = [policy.policy_id for policy in policies]
    if len(set(identifiers)) != len(identifiers):
        raise ConfigError("Policy IDs must be unique")
    for policy in policies:
        if (
            policy.approval.status == "approved"
            and policy.approval.policy_fingerprint != policy.fingerprint
        ):
            raise ConfigError(f"Approved policy {policy.policy_id} fingerprint does not match")
    return PolicyFile(path=path, policies=policies)


def load_policies(cli_path: str | None = None) -> PolicyFile:
    path = resolve_policy_path(cli_path)
    text, _ = read_owner_only_text(path, label="Google Ads MCP write policies")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Policy document is not valid JSON at line {exc.lineno}") from exc
    return parse_policies(value, path=path)


def _write_owner_only(path: Path, value: dict[str, Any], *, replace_existing: bool) -> None:
    if os.name == "nt":
        raise SecurityError(
            "Windows ACL verification is unavailable; policy writes are disabled",
            code="writes_not_secure",
        )
    if path.parent.is_symlink():
        raise SecurityError("The policy directory must not be a symbolic link")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_stat = path.parent.stat()
    if parent_stat.st_uid != os.getuid() or stat.S_IMODE(parent_stat.st_mode) & 0o077:
        raise SecurityError("The policy directory must be owner-only")
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    descriptor, name = tempfile.mkstemp(prefix=".policies.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists() and not replace_existing:
            raise ConfigError("Policy file already exists", code="policy_exists")
        os.replace(temporary, path)
        path.chmod(0o600)
    except BaseException:
        with suppress(OSError):
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def initialize_policies(path: str | None = None) -> Path:
    resolved = resolve_policy_path(path)
    _write_owner_only(resolved, {"schemaVersion": 1, "policies": []}, replace_existing=False)
    return resolved


def save_policies(policies: PolicyFile) -> None:
    parse_policies(policies.as_document(), path=policies.path)
    _write_owner_only(policies.path, policies.as_document(), replace_existing=True)


def approve_policy(
    policy_file: PolicyFile,
    policy_id: str,
    *,
    now: datetime | None = None,
) -> WritePolicy:
    selected = policy_file.policy(policy_id)
    approved_at = (now or datetime.now(UTC)).astimezone(UTC)
    approved = replace(
        selected,
        approval=PolicyApproval(
            status="approved",
            approved_at=approved_at,
            expires_at=approved_at + timedelta(days=30),
            policy_fingerprint=selected.fingerprint,
        ),
    )
    save_policies(
        replace(
            policy_file,
            policies=tuple(
                approved if item.policy_id == policy_id else item for item in policy_file.policies
            ),
        )
    )
    return approved


def revoke_policy(policy_file: PolicyFile, policy_id: str) -> WritePolicy:
    selected = policy_file.policy(policy_id)
    revoked = replace(
        selected,
        approval=PolicyApproval(
            status="revoked",
            approved_at=selected.approval.approved_at,
            expires_at=selected.approval.expires_at,
            policy_fingerprint=selected.approval.policy_fingerprint,
        ),
    )
    save_policies(
        replace(
            policy_file,
            policies=tuple(
                revoked if item.policy_id == policy_id else item for item in policy_file.policies
            ),
        )
    )
    return revoked


def policy_document_fingerprint(policy_file: PolicyFile) -> str:
    return value_fingerprint(policy_file.as_document())
