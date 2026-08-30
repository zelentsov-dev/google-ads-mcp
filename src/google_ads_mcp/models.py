from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, NotRequired

from pydantic import Field
from typing_extensions import TypedDict

Status = Literal["ok", "partial", "not_supported", "unavailable", "blocked"]


class ForecastKeyword(TypedDict):
    text: str
    matchType: Literal["EXACT", "PHRASE", "BROAD"]


GeoTargetIds = Annotated[
    list[str],
    Field(min_length=1, max_length=20, json_schema_extra={"uniqueItems": True}),
]


class Money(TypedDict):
    micros: str
    currencyCode: str


class ResponseContext(TypedDict):
    profile: str | None
    loginCustomerId: str | None
    customerId: str | None
    currencyCode: str | None
    timeZone: str | None
    apiVersion: str
    generatedAt: str
    contentTrust: Literal["untrusted_data"]


class ResponseEvidence(TypedDict):
    source: str
    dateFrom: str | None
    dateTo: str | None
    dataThrough: str | None
    partial: bool
    limitations: list[str]


class ErrorDetail(TypedDict):
    code: str
    message: str
    requestId: NotRequired[str]


class ToolResponse(TypedDict):
    status: Status
    context: ResponseContext
    evidence: ResponseEvidence
    items: list[dict[str, Any]]
    truncated: bool
    nextCursor: NotRequired[str]
    error: NotRequired[ErrorDetail]
    operation: NotRequired[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class SearchResult:
    items: tuple[dict[str, Any], ...]
    total_results: int | None = None
    truncated: bool = False


@dataclass(slots=True)
class ResponseEnvelope:
    status: Status
    profile: str
    login_customer_id: str | None
    customer_id: str | None
    currency_code: str | None = None
    time_zone: str | None = None
    source: str = "google_ads_api"
    date_from: str | None = None
    date_to: str | None = None
    data_through: str | None = None
    partial: bool = False
    limitations: list[str] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    next_cursor: str | None = None
    truncated: bool = False

    def as_dict(self) -> ToolResponse:
        result: ToolResponse = {
            "status": self.status,
            "context": {
                "profile": self.profile,
                "loginCustomerId": self.login_customer_id,
                "customerId": self.customer_id,
                "currencyCode": self.currency_code,
                "timeZone": self.time_zone,
                "apiVersion": "v25",
                "generatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "contentTrust": "untrusted_data",
            },
            "evidence": {
                "source": self.source,
                "dateFrom": self.date_from,
                "dateTo": self.date_to,
                "dataThrough": self.data_through,
                "partial": self.partial,
                "limitations": self.limitations,
            },
            "items": self.items,
            "truncated": self.truncated,
        }
        if self.next_cursor is not None:
            result["nextCursor"] = self.next_cursor
        return result
