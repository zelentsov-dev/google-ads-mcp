from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from google_ads_mcp.constants import MAX_REPORT_DAYS
from google_ads_mcp.dates import validate_date_range
from google_ads_mcp.errors import ValidationError

_FIELD = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+", re.IGNORECASE)
_SELECT_FIELD = re.compile(r"[a-z][a-z0-9_.]*", re.IGNORECASE)
_RESOURCE = re.compile(r"[a-z][a-z0-9_]*", re.IGNORECASE)
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_MUTATION_KEYWORDS = frozenset({"ALTER", "CREATE", "DELETE", "DROP", "INSERT", "MUTATE", "UPDATE"})
_CLAUSE_BOUNDARIES = frozenset({"LIMIT", "ORDER", "PARAMETERS"})

_BOUNDED_DURING_RANGES = frozenset(
    {
        "TODAY",
        "YESTERDAY",
        "LAST_7_DAYS",
        "LAST_BUSINESS_WEEK",
        "THIS_MONTH",
        "LAST_MONTH",
        "LAST_14_DAYS",
        "LAST_30_DAYS",
        "THIS_WEEK_SUN_TODAY",
        "THIS_WEEK_MON_TODAY",
        "LAST_WEEK_SUN_SAT",
        "LAST_WEEK_MON_SUN",
    }
)

DENIED_RESOURCES = frozenset(
    {
        "account_budget",
        "billing_setup",
        "customer_client_link",
        "customer_manager_link",
        "customer_user_access",
        "customer_user_access_invitation",
        "offline_user_data_job",
        "payments_account",
        "user_list",
    }
)
DENIED_FIELD_PARTS = frozenset(
    {
        "client_secret",
        "credential",
        "developer_token",
        "email",
        "phone",
        "private_key",
        "refresh_token",
        "user_identifier",
    }
)


@dataclass(frozen=True, slots=True)
class ValidatedGaql:
    query: str
    resource: str
    fields: tuple[str, ...]
    limit: int
    has_metrics: bool
    date_from: str | None
    date_to: str | None
    predefined_date_range: str | None


@dataclass(frozen=True, slots=True)
class _Token:
    value: str
    kind: str
    start: int
    end: int

    @property
    def upper(self) -> str:
        return self.value.upper()


def _tokenize(query: str) -> tuple[_Token, ...]:
    tokens: list[_Token] = []
    index = 0
    while index < len(query):
        character = query[index]
        if character.isspace():
            index += 1
            continue
        if query.startswith("--", index) or query.startswith("/*", index):
            raise ValidationError("GAQL comments are not allowed")
        if query.startswith("*/", index) or character == "#":
            raise ValidationError("GAQL comments are not allowed")
        if character == ";":
            raise ValidationError("GAQL must contain exactly one statement without a semicolon")
        if character in {"'", '"'}:
            quote = character
            start = index
            index += 1
            escaped = False
            while index < len(query):
                current = query[index]
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == quote:
                    index += 1
                    tokens.append(_Token(query[start:index], "string", start, index))
                    break
                index += 1
            else:
                raise ValidationError("GAQL contains an unterminated string literal")
            continue
        if character.isalpha() or character == "_":
            start = index
            index += 1
            while index < len(query) and (
                query[index].isalnum() or query[index] in {"_", "."}
            ):
                index += 1
            tokens.append(_Token(query[start:index], "word", start, index))
            continue
        if character.isdigit():
            start = index
            index += 1
            while index < len(query) and query[index].isdigit():
                index += 1
            tokens.append(_Token(query[start:index], "number", start, index))
            continue
        if character in {"<", ">", "!", "="}:
            start = index
            index += 1
            if index < len(query) and query[index] == "=":
                index += 1
            tokens.append(_Token(query[start:index], "operator", start, index))
            continue
        tokens.append(_Token(character, "symbol", index, index + 1))
        index += 1
    return tuple(tokens)


def _selected_fields(query: str, tokens: tuple[_Token, ...], from_index: int) -> tuple[str, ...]:
    if from_index <= 1:
        raise ValidationError("GAQL must contain SELECT fields followed by FROM")
    selected = query[tokens[0].end : tokens[from_index].start]
    fields = tuple(field.strip() for field in selected.split(","))
    if not fields or any(not field or not _SELECT_FIELD.fullmatch(field) for field in fields):
        raise ValidationError("GAQL SELECT list must contain only field names")
    return fields


def _literal_date(token: _Token) -> date:
    if token.kind != "string":
        raise ValidationError("segments.date comparisons require quoted ISO dates")
    literal = token.value[1:-1]
    if not _ISO_DATE.fullmatch(literal):
        raise ValidationError("segments.date comparisons require quoted ISO dates")
    try:
        return date.fromisoformat(literal)
    except ValueError as exc:
        raise ValidationError("segments.date contains an invalid ISO date") from exc


def _bounded_date_filter(
    where_tokens: tuple[_Token, ...],
) -> tuple[str | None, str | None, str | None]:
    lower_bounds: list[date] = []
    upper_bounds: list[date] = []
    predefined_ranges: list[str] = []
    index = 0
    while index < len(where_tokens):
        token = where_tokens[index]
        if token.kind != "word" or token.value.lower() != "segments.date":
            index += 1
            continue
        if index + 1 >= len(where_tokens):
            index += 1
            continue
        operation = where_tokens[index + 1]
        if operation.upper == "BETWEEN" and index + 4 < len(where_tokens):
            start = _literal_date(where_tokens[index + 2])
            if where_tokens[index + 3].upper != "AND":
                raise ValidationError("segments.date BETWEEN requires two ISO dates")
            end = _literal_date(where_tokens[index + 4])
            lower_bounds.append(start)
            upper_bounds.append(end)
            index += 5
            continue
        if operation.upper == "DURING" and index + 2 < len(where_tokens):
            value = where_tokens[index + 2]
            if value.kind != "word" or value.upper not in _BOUNDED_DURING_RANGES:
                raise ValidationError("Serving-metric GAQL DURING range is not bounded")
            predefined_ranges.append(value.upper)
            index += 3
            continue
        if operation.kind == "operator" and index + 2 < len(where_tokens):
            value = _literal_date(where_tokens[index + 2])
            if operation.value == "=":
                lower_bounds.append(value)
                upper_bounds.append(value)
            elif operation.value == ">=":
                lower_bounds.append(value)
            elif operation.value == ">":
                lower_bounds.append(value + timedelta(days=1))
            elif operation.value == "<=":
                upper_bounds.append(value)
            elif operation.value == "<":
                upper_bounds.append(value - timedelta(days=1))
            index += 3
            continue
        index += 1
    if predefined_ranges:
        if lower_bounds or upper_bounds or len(set(predefined_ranges)) != 1:
            raise ValidationError("Serving-metric GAQL must use one unambiguous date range")
        return None, None, predefined_ranges[0]
    if not lower_bounds and not upper_bounds:
        raise ValidationError("Serving-metric GAQL requires a bounded segments.date filter")
    if not lower_bounds or not upper_bounds:
        raise ValidationError("Serving-metric GAQL requires both segments.date bounds")
    start = max(lower_bounds)
    end = min(upper_bounds)
    start_value = start.isoformat()
    end_value = end.isoformat()
    validate_date_range(start_value, end_value, max_days=MAX_REPORT_DAYS)
    return start_value, end_value, None


def validate_gaql(query: str) -> ValidatedGaql:
    stripped = query.strip()
    if not stripped:
        raise ValidationError("GAQL query is required")
    if len(query) > 20_000:
        raise ValidationError("GAQL query is too long")
    tokens = _tokenize(stripped)
    if not tokens or tokens[0].kind != "word" or tokens[0].upper != "SELECT":
        raise ValidationError("Only SELECT GAQL queries are allowed")
    if any(token.kind == "word" and token.upper in _MUTATION_KEYWORDS for token in tokens):
        raise ValidationError("Mutation keywords are not allowed")

    from_indexes = [
        index
        for index, token in enumerate(tokens)
        if token.kind == "word" and token.upper == "FROM"
    ]
    if len(from_indexes) != 1:
        raise ValidationError("GAQL must contain exactly one FROM resource")
    from_index = from_indexes[0]
    if from_index + 1 >= len(tokens):
        raise ValidationError("GAQL FROM resource is required")
    resource_token = tokens[from_index + 1]
    if resource_token.kind != "word" or not _RESOURCE.fullmatch(resource_token.value):
        raise ValidationError("GAQL FROM resource is invalid")
    resource = resource_token.value.lower()
    if resource in DENIED_RESOURCES:
        raise ValidationError(f"GAQL resource is not allowed: {resource}")

    fields = _selected_fields(stripped, tokens, from_index)
    referenced_fields = tuple(
        token.value for token in tokens if token.kind == "word" and _FIELD.fullmatch(token.value)
    )
    for field in referenced_fields:
        if any(part in field.lower() for part in DENIED_FIELD_PARTS):
            raise ValidationError("Sensitive GAQL field is not allowed")

    limit_indexes = [
        index
        for index, token in enumerate(tokens)
        if token.kind == "word" and token.upper == "LIMIT"
    ]
    if len(limit_indexes) != 1:
        raise ValidationError("Safe GAQL requires exactly one explicit LIMIT")
    limit_index = limit_indexes[0]
    if limit_index + 2 != len(tokens) or tokens[limit_index + 1].kind != "number":
        raise ValidationError("GAQL LIMIT must be the final clause and contain an integer")
    limit = int(tokens[limit_index + 1].value)
    if not 1 <= limit <= 1_000:
        raise ValidationError("GAQL LIMIT must be between 1 and 1000")

    has_metrics = any(field.lower().startswith("metrics.") for field in referenced_fields)
    date_from: str | None = None
    date_to: str | None = None
    predefined_date_range: str | None = None
    if has_metrics:
        where_indexes = [
            index
            for index, token in enumerate(tokens)
            if token.kind == "word" and token.upper == "WHERE"
        ]
        if len(where_indexes) != 1:
            raise ValidationError("Serving-metric GAQL requires one bounded WHERE clause")
        where_index = where_indexes[0]
        boundary = next(
            (
                index
                for index in range(where_index + 1, len(tokens))
                if tokens[index].kind == "word" and tokens[index].upper in _CLAUSE_BOUNDARIES
            ),
            len(tokens),
        )
        where_tokens = tokens[where_index + 1 : boundary]
        if any(
            token.kind == "word" and token.upper in {"NOT", "OR"}
            for token in where_tokens
        ):
            raise ValidationError(
                "Serving-metric GAQL requires an AND-only positive date predicate"
            )
        date_from, date_to, predefined_date_range = _bounded_date_filter(
            where_tokens
        )
    return ValidatedGaql(
        query=stripped,
        resource=resource,
        fields=fields,
        limit=limit,
        has_metrics=has_metrics,
        date_from=date_from,
        date_to=date_to,
        predefined_date_range=predefined_date_range,
    )
