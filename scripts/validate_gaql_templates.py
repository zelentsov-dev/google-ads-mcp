from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from google_ads_mcp.adapter import GoogleAdsReadAdapter
from google_ads_mcp.config import load_config, normalize_customer_id
from google_ads_mcp.plans import state_queries
from google_ads_mcp.reports import REPORTS, build_report_query, segment_subsets
from google_ads_mcp.write_models import WriteKind

ROOT = Path(__file__).resolve().parents[1]
FIELD = re.compile(r"^[a-z][a-z0-9_.]+$")
SELECT_FIELDS = re.compile(r"^SELECT\s+(.+?)\s+FROM\s+", re.IGNORECASE | re.DOTALL)


def write_query_shapes(customer_id: str) -> set[str]:
    asset = f"customers/{customer_id}/assets/1"
    recommendation = f"customers/{customer_id}/recommendations/1"
    payloads: dict[str, dict[str, Any]] = {
        "search_campaign_create": {"name": "Static Search"},
        "performance_max_campaign_create": {"name": "Static PMax"},
        "app_campaign_create": {"name": "Static App"},
        "campaign_update": {"campaignId": "1"},
        "campaign_targeting": {
            "campaignId": "1",
            "geoTargetIds": ["2840"],
            "languageIds": ["1000"],
        },
        "campaign_status": {"campaignId": "1", "status": "ENABLED"},
        "campaign_budget": {"budgetId": "1"},
        "campaign_bidding": {"campaignId": "1"},
        "ad_group_create": {"campaignId": "1", "name": "Static Group"},
        "ad_group_update": {"adGroupId": "1"},
        "keyword_create": {
            "level": "AD_GROUP",
            "adGroupId": "1",
            "text": "static keyword",
        },
        "negative_keyword_create": {
            "level": "CAMPAIGN",
            "campaignId": "1",
            "text": "static negative",
        },
        "criterion_update": {
            "level": "AD_GROUP",
            "adGroupId": "1",
            "criterionId": "2",
        },
        "asset_create": {"name": "Static Asset"},
        "asset_link": {
            "ownerType": "CAMPAIGN",
            "ownerId": "1",
            "assetResourceName": asset,
        },
        "asset_group_create": {"campaignId": "1", "name": "Static Asset Group"},
        "ad_create": {"adGroupId": "1", "name": "Static Ad"},
        "recommendation_apply": {"recommendationResourceName": recommendation},
        "recommendation_dismiss": {"recommendationResourceName": recommendation},
    }
    return {
        query
        for kind, payload in payloads.items()
        for _, query in state_queries(cast(WriteKind, kind), customer_id, payload)
    }


def _query_fields(queries: set[str]) -> set[str]:
    fields: set[str] = set()
    for query in queries:
        match = SELECT_FIELDS.search(query)
        if match is None:
            raise SystemExit("Write state query does not contain a SELECT field list")
        fields.update(field.strip() for field in match.group(1).split(","))
    return fields


def template_fields() -> list[str]:
    return sorted(
        {
            field
            for report in REPORTS.values()
            for field in (*report.dimensions, *report.metrics, *report.allowed_segments)
        }
        | _query_fields(write_query_shapes("1234567890"))
    )


def validate_static() -> list[str]:
    fields = template_fields()
    invalid = [field for field in fields if not FIELD.fullmatch(field)]
    if invalid:
        raise SystemExit(f"Invalid field syntax: {invalid[0]}")
    contract = json.loads((ROOT / "api-contract" / "upstream-baseline.json").read_text())
    if contract.get("googleAdsApi") != "v25":
        raise SystemExit("Upstream field baseline is not v25")
    return fields


def validate_field_metadata(fields: list[str], metadata: list[dict[str, object]]) -> None:
    by_name = {item["name"]: item for item in metadata if isinstance(item.get("name"), str)}
    missing = sorted(set(fields) - set(by_name))
    if missing:
        raise SystemExit(f"GoogleAdsFieldService v25 did not return field: {missing[0]}")
    nonselectable = sorted(
        field for field in fields if by_name[field].get("selectable") is not True
    )
    if nonselectable:
        raise SystemExit(f"GoogleAdsFieldService v25 field is not selectable: {nonselectable[0]}")


async def validate_live(profile_name: str, config_path: str | None, customer_id: str | None) -> int:
    fields = validate_static()
    config = load_config(config_path)
    profile = config.profile(profile_name)
    adapter = GoogleAdsReadAdapter()
    metadata_items: list[dict[str, object]] = []
    for offset in range(0, len(fields), 200):
        metadata = await adapter.field_metadata(profile, fields[offset : offset + 200])
        metadata_items.extend(metadata)
    validate_field_metadata(fields, metadata_items)
    selected_customer = normalize_customer_id(
        customer_id or profile.default_customer_id,
        field="customerId",
        required=True,
    )
    assert selected_customer is not None
    today = datetime.now(UTC).date().isoformat()
    queries: set[str] = set()
    for report in REPORTS.values():
        for segments in segment_subsets(report.allowed_segments):
            _, query = build_report_query(
                report.name,
                date_from=today,
                date_to=today,
                segments=segments,
            )
            queries.add(query)
    queries.update(write_query_shapes(selected_customer))
    for query in sorted(queries):
        await adapter.validate_query(profile, selected_customer, query)
    return len(queries)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-profile", default=os.environ.get("GOOGLE_ADS_MCP_METADATA_PROFILE"))
    parser.add_argument("--customer-id")
    parser.add_argument("--config")
    args = parser.parse_args()
    fields = validate_static()
    if args.live_profile:
        query_count = asyncio.run(validate_live(args.live_profile, args.config, args.customer_id))
        print(
            f"live GoogleAdsFieldService metadata valid: {len(fields)} fields; "
            f"Google Ads validate-only queries valid: {query_count}"
        )
    else:
        print(
            f"static v25 template contract valid: {len(fields)} fields; "
            "live metadata remains an acceptance gate"
        )


if __name__ == "__main__":
    main()
