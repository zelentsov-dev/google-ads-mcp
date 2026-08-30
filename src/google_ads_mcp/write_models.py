from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, cast

WriteKind = Literal[
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
]

OperationState = Literal[
    "previewed",
    "applying",
    "applied",
    "failed",
    "partial",
    "committed_unverified",
    "expired",
]


@dataclass(frozen=True, slots=True)
class MutationItem:
    operation_type: str
    action: Literal["create", "update"]
    resource: dict[str, Any]
    update_mask: tuple[str, ...] = ()
    correlation_id: str = "item-1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "operationType": self.operation_type,
            "action": self.action,
            "resource": self.resource,
            "updateMask": list(self.update_mask),
            "correlationId": self.correlation_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MutationItem:
        return cls(
            operation_type=str(value["operationType"]),
            action=cast(Literal["create", "update"], str(value["action"])),
            resource=dict(value["resource"]),
            update_mask=tuple(str(item) for item in value.get("updateMask", ())),
            correlation_id=str(value.get("correlationId", "item-1")),
        )


@dataclass(frozen=True, slots=True)
class WritePlan:
    profile: str
    customer_id: str
    policy_id: str
    kind: WriteKind
    related_key: str
    before: dict[str, Any]
    after: dict[str, Any]
    items: tuple[MutationItem, ...]
    readback_queries: tuple[str, ...]
    monetary_delta: dict[str, str | None]
    risk: dict[str, Any]
    partial_failure: bool = False
    independent_items: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "customerId": self.customer_id,
            "policyId": self.policy_id,
            "kind": self.kind,
            "relatedKey": self.related_key,
            "before": self.before,
            "after": self.after,
            "items": [item.as_dict() for item in self.items],
            "readbackQueries": list(self.readback_queries),
            "monetaryDelta": self.monetary_delta,
            "risk": self.risk,
            "partialFailure": self.partial_failure,
            "independentItems": self.independent_items,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> WritePlan:
        return cls(
            profile=str(value["profile"]),
            customer_id=str(value["customerId"]),
            policy_id=str(value["policyId"]),
            kind=cast(WriteKind, str(value["kind"])),
            related_key=str(value["relatedKey"]),
            before=dict(value["before"]),
            after=dict(value["after"]),
            items=tuple(MutationItem.from_dict(dict(item)) for item in value["items"]),
            readback_queries=tuple(str(item) for item in value["readbackQueries"]),
            monetary_delta={
                str(key): str(item) if item is not None else None
                for key, item in value["monetaryDelta"].items()
            },
            risk=dict(value["risk"]),
            partial_failure=bool(value.get("partialFailure", False)),
            independent_items=bool(value.get("independentItems", False)),
        )


@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    request_id: str | None = None
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"valid": self.valid, "warnings": list(self.warnings)}
        if self.request_id is not None:
            result["requestId"] = self.request_id
        return result


@dataclass(frozen=True, slots=True)
class ApplyItemResult:
    correlation_id: str
    status: Literal["applied", "failed", "unknown"]
    resource_name: str | None = None
    error_code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "correlationId": self.correlation_id,
            "status": self.status,
        }
        if self.resource_name is not None:
            result["resourceName"] = self.resource_name
        if self.error_code is not None:
            result["errorCode"] = self.error_code
        return result


@dataclass(frozen=True, slots=True)
class ApplyResult:
    state: Literal["applied", "failed", "partial", "committed_unverified"]
    items: tuple[ApplyItemResult, ...] = ()
    request_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "state": self.state,
            "items": [item.as_dict() for item in self.items],
        }
        if self.request_id is not None:
            result["requestId"] = self.request_id
        return result


@dataclass(frozen=True, slots=True)
class ReadbackResult:
    state: Literal["matched_before", "matched_after", "partial", "inconclusive"]
    current: dict[str, Any] = field(default_factory=dict)
    objects: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "current": self.current,
            "objects": list(self.objects),
        }
