from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Final

from google_ads_mcp.constants import MAX_INTERNAL_ROWS
from google_ads_mcp.dates import validate_date_range
from google_ads_mcp.errors import ValidationError

PINNED_CAMPAIGN_TYPES: Final = frozenset(
    {
        "DEMAND_GEN",
        "DISPLAY",
        "HOTEL",
        "LOCAL",
        "LOCAL_SERVICES",
        "MULTI_CHANNEL",
        "PERFORMANCE_MAX",
        "SEARCH",
        "SHOPPING",
        "SMART",
        "TRAVEL",
        "UNKNOWN",
        "UNSPECIFIED",
        "VIDEO",
    }
)


@dataclass(frozen=True, slots=True)
class ReportDefinition:
    name: str
    description: str
    resource: str
    dimensions: tuple[str, ...]
    metrics: tuple[str, ...]
    allowed_segments: tuple[str, ...] = ()
    campaign_types: tuple[str, ...] = ()
    modeled_conversions: bool = False

    @property
    def requires_dates(self) -> bool:
        return bool(self.metrics)


def segment_subsets(allowed_segments: tuple[str, ...]) -> list[list[str]]:
    return [
        list(subset)
        for subset_size in range(len(allowed_segments) + 1)
        for subset in combinations(allowed_segments, subset_size)
    ]


REPORTS: Final[dict[str, ReportDefinition]] = {
    report.name: report
    for report in (
        ReportDefinition(
            "account_performance",
            "Account serving and conversion performance.",
            "customer",
            ("customer.id", "customer.descriptive_name"),
            (
                "metrics.impressions",
                "metrics.clicks",
                "metrics.cost_micros",
                "metrics.conversions",
                "metrics.conversions_value",
            ),
            ("segments.date", "segments.device", "segments.ad_network_type"),
            modeled_conversions=True,
        ),
        ReportDefinition(
            "campaign_performance",
            "Generic performance for every campaign type.",
            "campaign",
            (
                "campaign.id",
                "campaign.name",
                "campaign.status",
                "campaign.advertising_channel_type",
            ),
            (
                "metrics.impressions",
                "metrics.clicks",
                "metrics.cost_micros",
                "metrics.conversions",
                "metrics.conversions_value",
            ),
            (
                "segments.date",
                "segments.device",
                "segments.ad_network_type",
                "segments.conversion_action",
            ),
            modeled_conversions=True,
        ),
        ReportDefinition(
            "ad_group_performance",
            "Ad group serving performance.",
            "ad_group",
            ("campaign.id", "ad_group.id", "ad_group.name", "ad_group.status"),
            ("metrics.impressions", "metrics.clicks", "metrics.cost_micros", "metrics.conversions"),
            ("segments.date", "segments.device"),
        ),
        ReportDefinition(
            "ad_performance",
            "Ad-level serving performance.",
            "ad_group_ad",
            (
                "campaign.id",
                "ad_group.id",
                "ad_group_ad.ad.id",
                "ad_group_ad.status",
                "ad_group_ad.ad.type",
            ),
            ("metrics.impressions", "metrics.clicks", "metrics.cost_micros", "metrics.conversions"),
            ("segments.date", "segments.device"),
        ),
        ReportDefinition(
            "asset_performance",
            "Asset-level performance where Google exposes it.",
            "campaign_asset",
            (
                "campaign.id",
                "campaign_asset.asset",
                "campaign_asset.field_type",
                "campaign_asset.status",
            ),
            ("metrics.impressions", "metrics.clicks", "metrics.cost_micros", "metrics.conversions"),
            ("segments.date",),
        ),
        ReportDefinition(
            "criteria_performance",
            "Campaign criterion serving performance.",
            "campaign_criterion",
            (
                "campaign.id",
                "campaign_criterion.criterion_id",
                "campaign_criterion.type",
                "campaign_criterion.negative",
            ),
            ("metrics.impressions", "metrics.clicks", "metrics.cost_micros", "metrics.conversions"),
            ("segments.date", "segments.device"),
        ),
        ReportDefinition(
            "conversion_performance",
            "Performance segmented by conversion action.",
            "campaign",
            ("campaign.id", "segments.conversion_action", "segments.conversion_action_name"),
            ("metrics.conversions", "metrics.all_conversions", "metrics.conversions_value"),
            ("segments.date",),
            modeled_conversions=True,
        ),
        ReportDefinition(
            "search_terms",
            "Search terms for Search and Shopping inventory.",
            "search_term_view",
            (
                "campaign.id",
                "ad_group.id",
                "search_term_view.search_term",
                "search_term_view.status",
            ),
            ("metrics.impressions", "metrics.clicks", "metrics.cost_micros", "metrics.conversions"),
            ("segments.date", "segments.device"),
            ("SEARCH", "SHOPPING"),
        ),
        ReportDefinition(
            "placements",
            "Automatic placement performance.",
            "group_placement_view",
            (
                "campaign.id",
                "group_placement_view.placement",
                "group_placement_view.placement_type",
            ),
            ("metrics.impressions", "metrics.clicks", "metrics.cost_micros", "metrics.conversions"),
            ("segments.date",),
            ("DISPLAY", "VIDEO", "DEMAND_GEN", "PERFORMANCE_MAX"),
        ),
        ReportDefinition(
            "app_campaign_performance",
            "App campaign performance.",
            "campaign",
            (
                "campaign.id",
                "campaign.app_campaign_setting.app_id",
                "campaign.app_campaign_setting.app_store",
            ),
            ("metrics.impressions", "metrics.clicks", "metrics.cost_micros", "metrics.conversions"),
            ("segments.date", "segments.device"),
            ("MULTI_CHANNEL",),
        ),
        ReportDefinition(
            "performance_max_asset_groups",
            "Performance Max asset group inventory and performance.",
            "asset_group",
            ("campaign.id", "asset_group.id", "asset_group.name", "asset_group.status"),
            ("metrics.impressions", "metrics.clicks", "metrics.cost_micros", "metrics.conversions"),
            ("segments.date",),
            ("PERFORMANCE_MAX",),
        ),
        ReportDefinition(
            "shopping_products",
            "Shopping product performance.",
            "shopping_performance_view",
            (
                "campaign.id",
                "segments.product_item_id",
                "segments.product_title",
                "segments.product_type_l1",
            ),
            ("metrics.impressions", "metrics.clicks", "metrics.cost_micros", "metrics.conversions"),
            ("segments.date", "segments.device"),
            ("SHOPPING", "PERFORMANCE_MAX"),
        ),
        ReportDefinition(
            "video_performance",
            "Video campaign performance.",
            "campaign",
            ("campaign.id", "campaign.name"),
            (
                "metrics.video_views",
                "metrics.video_view_rate",
                "metrics.cost_micros",
                "metrics.conversions",
            ),
            ("segments.date", "segments.device"),
            ("VIDEO",),
        ),
        ReportDefinition(
            "hotel_performance",
            "Hotel campaign performance.",
            "hotel_performance_view",
            ("campaign.id", "segments.hotel_id", "segments.hotel_country", "segments.hotel_city"),
            ("metrics.impressions", "metrics.clicks", "metrics.cost_micros", "metrics.conversions"),
            ("segments.date",),
            ("HOTEL", "TRAVEL"),
        ),
        ReportDefinition(
            "local_services_performance",
            "Local Services campaign performance where available.",
            "campaign",
            ("campaign.id", "campaign.name"),
            ("metrics.impressions", "metrics.clicks", "metrics.cost_micros", "metrics.conversions"),
            ("segments.date",),
            ("LOCAL_SERVICES", "LOCAL"),
        ),
        ReportDefinition(
            "demand_gen_performance",
            "Demand Gen campaign performance.",
            "campaign",
            ("campaign.id", "campaign.name"),
            ("metrics.impressions", "metrics.clicks", "metrics.cost_micros", "metrics.conversions"),
            ("segments.date", "segments.device", "segments.ad_network_type"),
            ("DEMAND_GEN",),
        ),
        ReportDefinition(
            "display_performance",
            "Display campaign performance.",
            "campaign",
            ("campaign.id", "campaign.name"),
            ("metrics.impressions", "metrics.clicks", "metrics.cost_micros", "metrics.conversions"),
            ("segments.date", "segments.device", "segments.ad_network_type"),
            ("DISPLAY",),
        ),
        ReportDefinition(
            "smart_campaign_performance",
            "Smart campaign performance where available.",
            "campaign",
            ("campaign.id", "campaign.name"),
            ("metrics.impressions", "metrics.clicks", "metrics.cost_micros", "metrics.conversions"),
            ("segments.date", "segments.device"),
            ("SMART",),
        ),
    )
}


def reports_catalog() -> list[dict[str, object]]:
    return [
        {
            "name": report.name,
            "description": report.description,
            "resource": report.resource,
            "requiresDateRange": report.requires_dates,
            "allowedSegments": list(report.allowed_segments),
            "campaignTypes": list(report.campaign_types),
            "modeledConversions": report.modeled_conversions,
        }
        for report in REPORTS.values()
    ]


def _numeric_ids(values: list[str] | None) -> list[str]:
    if not values:
        return []
    if len(values) > 100:
        raise ValidationError("At most 100 campaign IDs are allowed")
    normalized: list[str] = []
    for value in values:
        rendered = str(value)
        if not rendered.isascii() or not rendered.isdigit():
            raise ValidationError("Campaign IDs must contain digits only")
        normalized.append(rendered)
    return normalized


def build_report_query(
    report_name: str,
    *,
    date_from: str,
    date_to: str,
    campaign_ids: list[str] | None = None,
    segments: list[str] | None = None,
) -> tuple[ReportDefinition, str]:
    try:
        report = REPORTS[report_name]
    except KeyError as exc:
        raise ValidationError(f"Unknown report: {report_name}") from exc
    validate_date_range(date_from, date_to, max_days=366)
    requested_segments = tuple(segments or ())
    unsupported = sorted(set(requested_segments) - set(report.allowed_segments))
    if unsupported:
        raise ValidationError(f"Segment is not supported by {report_name}: {unsupported[0]}")
    fields = list(report.dimensions) + list(report.metrics)
    for segment in requested_segments:
        if segment not in fields:
            fields.append(segment)
    filters = [f"segments.date BETWEEN '{date_from}' AND '{date_to}'"]
    if report.campaign_types:
        campaign_types = ", ".join(f"'{value}'" for value in report.campaign_types)
        filters.append(f"campaign.advertising_channel_type IN ({campaign_types})")
    ids = _numeric_ids(campaign_ids)
    if ids:
        filters.append(f"campaign.id IN ({', '.join(ids)})")
    query = (
        f"SELECT {', '.join(fields)} FROM {report.resource} "
        f"WHERE {' AND '.join(filters)} LIMIT {MAX_INTERNAL_ROWS}"
    )
    return report, query
