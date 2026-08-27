from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from google_ads_mcp.cursors import CursorStore
from google_ads_mcp.dates import validate_date_range
from google_ads_mcp.errors import SecurityError
from google_ads_mcp.normalization import enum_value, money, normalize_row
from google_ads_mcp.reports import build_report_query
from google_ads_mcp.security import check_owner_only_file, redact_untrusted


@given(st.integers(min_value=-(2**63), max_value=2**63 - 1))
def test_money_and_micros_property(value: int) -> None:
    assert money(value, "USD") == {"micros": str(value), "currencyCode": "USD"}
    normalized = normalize_row({"metrics": {"cost_micros": value}})
    assert normalized["metrics"]["cost_micros"] == str(value)


@given(st.dates(min_value=date(2000, 1, 1), max_value=date(2090, 1, 1)), st.integers(0, 365))
def test_date_range_property(start: date, days: int) -> None:
    end = start + timedelta(days=days)
    assert validate_date_range(start.isoformat(), end.isoformat(), max_days=366) == (start, end)


@given(st.text(min_size=1, max_size=30).filter(lambda value: value != "KNOWN"))
def test_unknown_enum_property(value: str) -> None:
    assert enum_value(value, {"KNOWN"}) == {"value": value, "recognized": False}


@given(st.lists(st.integers(), min_size=2, max_size=50), st.integers(min_value=1, max_value=20))
def test_cursor_round_trip_property(values: list[int], page_size: int) -> None:
    items = [{"value": value} for value in values]
    first_size = min(page_size, len(items) - 1)
    store = CursorStore()
    token = store.create(profile="p", binding="b", items=items, offset=first_size)
    assert token is not None
    recovered = items[:first_size]
    while token is not None:
        page = store.page(token, profile="p", binding="b", page_size=min(page_size, 200))
        token = page.next_cursor
        recovered.extend(page.items)
    assert recovered == items


@given(
    st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")), min_size=1, max_size=20
    )
)
def test_redaction_property(local_part: str) -> None:
    email = f"{local_part}@example.com"
    rendered = redact_untrusted({"actor_email": email, "text": email})
    assert email not in str(rendered)


@given(st.lists(st.integers(min_value=0, max_value=2**63 - 1), min_size=1, max_size=20))
def test_report_id_filter_property(ids: list[int]) -> None:
    rendered = [str(value) for value in ids]
    _, query = build_report_query(
        "campaign_performance",
        date_from="2026-01-01",
        date_to="2026-01-02",
        campaign_ids=rendered,
    )
    assert f"campaign.id IN ({', '.join(rendered)})" in query


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission property")
@given(st.integers(min_value=1, max_value=0o77))
def test_unsafe_permission_property(extra_mode: int) -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.json"
        path.write_text("{}")
        path.chmod(0o600 | extra_mode)
        with pytest.raises(SecurityError):
            check_owner_only_file(path, label="property config")
