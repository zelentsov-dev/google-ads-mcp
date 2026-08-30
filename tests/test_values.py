from __future__ import annotations

from datetime import date
from enum import Enum

import pytest
from google.ads.googleads.v25.services.types.google_ads_service import GoogleAdsRow

from google_ads_mcp.dates import parse_iso_date, validate_date_range
from google_ads_mcp.errors import ValidationError
from google_ads_mcp.normalization import enum_value, money, normalize_row


class Example(Enum):
    KNOWN = 1


def test_money_preserves_micros_as_string_and_null() -> None:
    assert money(123, "USD") == {"micros": "123", "currencyCode": "USD"}
    assert money("-42", "EUR") == {"micros": "-42", "currencyCode": "EUR"}
    assert money(None, "USD") is None
    with pytest.raises(TypeError):
        money(True, "USD")
    with pytest.raises(TypeError):
        money("1.5", "USD")


def test_enum_value_preserves_unknown() -> None:
    assert enum_value(Example.KNOWN, {"KNOWN"}) == {"value": "KNOWN", "recognized": True}
    assert enum_value("FUTURE_VALUE", {"KNOWN"}) == {
        "value": "FUTURE_VALUE",
        "recognized": False,
    }


def test_normalize_mapping_preserves_null_and_string_ids() -> None:
    assert normalize_row(
        {"campaign": {"id": 123, "budget_micros": 456, "clicks": None}, "count": 2}
    ) == {
        "campaign": {"id": "123", "budget_micros": "456", "clicks": None},
        "count": 2,
    }
    with pytest.raises(TypeError):
        normalize_row([1, 2])


def test_unknown_protobuf_enum_is_preserved() -> None:
    row = GoogleAdsRow()
    row.campaign.id = 123
    row.campaign.advertising_channel_type = 999
    normalized = normalize_row(row)
    assert normalized["campaign"]["advertising_channel_type"] == {
        "value": "999",
        "recognized": False,
    }


def test_selected_null_protobuf_metrics_remain_explicit_null() -> None:
    normalized = normalize_row(
        GoogleAdsRow(),
        requested_fields=("campaign.id", "metrics.clicks", "metrics.cost_micros"),
    )
    assert normalized == {
        "campaign": {"id": None},
        "metrics": {"clicks": None, "cost_micros": None},
    }


def test_selected_false_enhanced_cpc_remains_authoritative_false() -> None:
    row = GoogleAdsRow()
    row.campaign.manual_cpc.enhanced_cpc_enabled = False

    normalized = normalize_row(
        row,
        requested_fields=("campaign.manual_cpc.enhanced_cpc_enabled",),
    )

    assert normalized == {"campaign": {"manual_cpc": {"enhanced_cpc_enabled": False}}}


def test_iso_dates_and_ranges() -> None:
    assert parse_iso_date("2026-08-27", field="date") == date(2026, 8, 27)
    assert validate_date_range("2024-01-01", "2024-12-31", max_days=366)
    with pytest.raises(ValidationError):
        parse_iso_date("2026-8-27", field="date")
    with pytest.raises(ValidationError):
        validate_date_range("2026-02-02", "2026-02-01", max_days=366)
    with pytest.raises(ValidationError):
        validate_date_range("2024-01-01", "2025-01-01", max_days=366)
