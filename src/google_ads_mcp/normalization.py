from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from enum import Enum
from typing import Any

from google.protobuf.json_format import MessageToDict

_STRING_SUFFIXES = ("_id", "_micros")


def money(micros: str | int | None, currency_code: str) -> dict[str, str] | None:
    if micros is None:
        return None
    if isinstance(micros, bool):
        raise TypeError("Money micros must be an integer-like value")
    try:
        integer = int(micros)
    except (TypeError, ValueError) as exc:
        raise TypeError("Money micros must be an integer-like value") from exc
    if Decimal(str(micros)) != Decimal(integer):
        raise TypeError("Money micros must not contain a fractional micro")
    return {"micros": str(integer), "currencyCode": currency_code}


def enum_value(value: object, recognized_values: set[str]) -> dict[str, object]:
    rendered = value.name if isinstance(value, Enum) else str(value)
    return {"value": rendered, "recognized": rendered in recognized_values}


def _snake_key(key: str) -> str:
    return key


def _annotate_unknown_enums(message: Any, data: dict[str, Any]) -> None:
    for descriptor in message.DESCRIPTOR.fields:
        key = descriptor.name
        if key not in data:
            continue
        raw = getattr(message, key)
        if descriptor.enum_type is not None:
            known_numbers = descriptor.enum_type.values_by_number
            if descriptor.is_repeated:
                data[key] = [
                    value
                    if int(number) in known_numbers
                    else {"value": str(number), "recognized": False}
                    for number, value in zip(raw, data[key], strict=True)
                ]
            elif int(raw) not in known_numbers:
                data[key] = {"value": str(raw), "recognized": False}
        elif descriptor.message_type is not None:
            if descriptor.is_repeated:
                for child, child_data in zip(raw, data[key], strict=True):
                    if isinstance(child_data, dict):
                        _annotate_unknown_enums(child, child_data)
            elif isinstance(data[key], dict):
                _annotate_unknown_enums(raw, data[key])


def _protobuf_dict(message: Any) -> dict[str, Any]:
    converted = MessageToDict(
        message,
        preserving_proto_field_name=True,
        use_integers_for_enums=False,
    )
    _annotate_unknown_enums(message, converted)
    return converted


def _normalize(value: Any, key: str = "") -> Any:
    if value is None or isinstance(value, (bool, float, str)):
        return value
    if isinstance(value, int):
        if key.endswith(_STRING_SUFFIXES) or key in {"id", "resource_name"}:
            return str(value)
        return value
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, Mapping):
        return {_snake_key(str(k)): _normalize(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if hasattr(value, "_pb"):
        converted = _protobuf_dict(value._pb)
        return _normalize(converted)
    if hasattr(value, "DESCRIPTOR"):
        converted = _protobuf_dict(value)
        return _normalize(converted)
    return str(value)


def _materialize_requested_fields(data: dict[str, Any], requested_fields: tuple[str, ...]) -> None:
    for field in requested_fields:
        parts = field.split(".")
        current = data
        for part in parts[:-1]:
            nested = current.get(part)
            if nested is None:
                nested = {}
                current[part] = nested
            if not isinstance(nested, dict):
                break
            current = nested
        else:
            current.setdefault(parts[-1], None)


def normalize_row(value: Any, *, requested_fields: tuple[str, ...] = ()) -> dict[str, Any]:
    normalized = _normalize(value)
    if not isinstance(normalized, dict):
        raise TypeError("Google Ads row did not normalize to an object")
    _materialize_requested_fields(normalized, requested_fields)
    return normalized
