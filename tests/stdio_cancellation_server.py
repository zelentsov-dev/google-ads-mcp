from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from google_ads_mcp.adapter import AdapterSearchResult
from google_ads_mcp.config import parse_config
from google_ads_mcp.server import create_mcp


class SlowAdapter:
    async def search(self, profile: Any, customer_id: str, query: str) -> AdapterSearchResult:
        return AdapterSearchResult((), 0, False)

    async def accessible_customers(self, profile: Any) -> tuple[str, ...]:
        marker = Path(os.environ["GOOGLE_ADS_MCP_CANCELLATION_MARKER"])
        marker.with_suffix(".started").write_text("started")
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            marker.with_suffix(".cancelled").write_text("cancelled")
            raise
        return ()

    async def field_metadata(
        self, profile: Any, field_names: list[str]
    ) -> tuple[dict[str, Any], ...]:
        return ()

    async def validate_query(self, profile: Any, customer_id: str, query: str) -> None:
        return None


config = parse_config(
    {
        "profiles": [
            {
                "name": "test-read-only",
                "auth": {
                    "type": "adc",
                    "developerTokenEnv": "ABSENT_TEST_TOKEN",
                },
                "allowWrites": False,
            }
        ]
    },
    path=Path("/synthetic/accounts.json"),
)
create_mcp(config=config, adapter=SlowAdapter()).run(transport="stdio")
