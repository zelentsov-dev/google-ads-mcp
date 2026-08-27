from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from google_ads_mcp.adapter import GoogleAdsReadAdapter
from google_ads_mcp.config import load_config, normalize_customer_id
from google_ads_mcp.reports import REPORTS, build_report_query, segment_subsets

ROOT = Path(__file__).resolve().parents[1]
FIELD = re.compile(r"^[a-z][a-z0-9_.]+$")


def template_fields() -> list[str]:
    return sorted(
        {
            field
            for report in REPORTS.values()
            for field in (*report.dimensions, *report.metrics, *report.allowed_segments)
        }
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


async def validate_live(
    profile_name: str, config_path: str | None, customer_id: str | None
) -> int:
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
        query_count = asyncio.run(
            validate_live(args.live_profile, args.config, args.customer_id)
        )
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
