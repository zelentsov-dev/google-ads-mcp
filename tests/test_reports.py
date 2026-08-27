from __future__ import annotations

import json
from pathlib import Path

import pytest
from google.ads.googleads.v25.enums.types.advertising_channel_type import (
    AdvertisingChannelTypeEnum,
)

from google_ads_mcp.errors import ValidationError
from google_ads_mcp.reports import (
    PINNED_CAMPAIGN_TYPES,
    REPORTS,
    build_report_query,
    reports_catalog,
    segment_subsets,
)


def test_catalog_covers_generic_and_specialized_campaign_types() -> None:
    catalog = reports_catalog()
    assert len(catalog) == len(REPORTS)
    covered = {kind for item in catalog for kind in item["campaignTypes"]}
    assert PINNED_CAMPAIGN_TYPES - {"UNKNOWN", "UNSPECIFIED"} <= covered
    assert any(item["name"] == "campaign_performance" for item in catalog)
    golden = json.loads(Path("api-contract/reports-v0.1.json").read_text())["reports"]
    assert catalog == golden


def test_campaign_matrix_matches_pinned_v25_enum() -> None:
    assert (
        set(AdvertisingChannelTypeEnum.AdvertisingChannelType.__members__) == PINNED_CAMPAIGN_TYPES
    )
    baseline = json.loads(Path("api-contract/upstream-baseline.json").read_text())
    assert set(baseline["advertisingChannelTypes"]) == PINNED_CAMPAIGN_TYPES


def test_build_report_query_is_bounded_and_numeric() -> None:
    definition, query = build_report_query(
        "campaign_performance",
        date_from="2026-01-01",
        date_to="2026-01-31",
        campaign_ids=["123", "456"],
        segments=["segments.device"],
    )
    assert definition.name == "campaign_performance"
    assert "campaign.id IN (123, 456)" in query
    assert "LIMIT 1000" in query
    assert "segments.device" in query


def test_specialized_report_filters_campaign_type() -> None:
    _, query = build_report_query(
        "app_campaign_performance",
        date_from="2026-01-01",
        date_to="2026-01-02",
    )
    assert "campaign.advertising_channel_type IN ('MULTI_CHANNEL')" in query


def test_live_validation_covers_every_allowed_segment_subset() -> None:
    expected_query_count = sum(2 ** len(report.allowed_segments) for report in REPORTS.values())
    actual_query_count = sum(
        len(segment_subsets(report.allowed_segments)) for report in REPORTS.values()
    )
    queries = {
        build_report_query(
            report.name,
            date_from="2026-01-01",
            date_to="2026-01-01",
            segments=segments,
        )[1]
        for report in REPORTS.values()
        for segments in segment_subsets(report.allowed_segments)
    }
    assert expected_query_count == 84
    assert actual_query_count == expected_query_count
    assert len(queries) == expected_query_count
    for report in REPORTS.values():
        actual = {frozenset(subset) for subset in segment_subsets(report.allowed_segments)}
        assert len(actual) == 2 ** len(report.allowed_segments)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"report_name": "missing"}, "Unknown report"),
        ({"segments": ["segments.hour"]}, "Segment"),
        ({"campaign_ids": ["1 OR 1=1"]}, "digits"),
        ({"campaign_ids": [str(index) for index in range(101)]}, "100"),
    ],
)
def test_report_query_rejects_unbounded_inputs(kwargs: dict[str, object], message: str) -> None:
    arguments: dict[str, object] = {
        "report_name": "campaign_performance",
        "date_from": "2026-01-01",
        "date_to": "2026-01-02",
    }
    arguments.update(kwargs)
    with pytest.raises(ValidationError, match=message):
        build_report_query(**arguments)  # type: ignore[arg-type]
