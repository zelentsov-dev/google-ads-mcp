from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from google_ads_mcp.reports import REPORTS

_SPEC = importlib.util.spec_from_file_location(
    "google_ads_mcp_validate_gaql_templates",
    Path(__file__).resolve().parents[1] / "scripts" / "validate_gaql_templates.py",
)
assert _SPEC is not None
assert _SPEC.loader is not None
validate_gaql_templates = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = validate_gaql_templates
_SPEC.loader.exec_module(validate_gaql_templates)


def _config(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "name": "acceptance",
                        "defaultCustomerId": "2222222222",
                        "auth": {
                            "type": "adc",
                            "developerTokenEnv": "ABSENT_TEST_TOKEN",
                        },
                        "allowWrites": False,
                    }
                ]
            }
        )
    )
    path.chmod(0o600)


def test_live_audit_validates_metadata_and_every_query_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "accounts.json"
    _config(config)

    class Adapter:
        def __init__(self) -> None:
            self.queries: list[str] = []

        async def field_metadata(
            self, profile: Any, field_names: list[str]
        ) -> tuple[dict[str, Any], ...]:
            return tuple({"name": name, "selectable": True} for name in field_names)

        async def validate_query(
            self, profile: Any, customer_id: str, query: str
        ) -> None:
            assert customer_id == "2222222222"
            self.queries.append(query)

    adapter = Adapter()
    monkeypatch.setattr(validate_gaql_templates, "GoogleAdsReadAdapter", lambda: adapter)
    count = asyncio.run(
        validate_gaql_templates.validate_live("acceptance", str(config), None)
    )
    assert count == len(adapter.queries)
    assert count > len(REPORTS)
    assert all("segments.date BETWEEN" in query for query in adapter.queries)


def test_metadata_audit_rejects_missing_or_nonselectable_fields() -> None:
    with pytest.raises(SystemExit, match="did not return"):
        validate_gaql_templates.validate_field_metadata(["campaign.id"], [])
    with pytest.raises(SystemExit, match="not selectable"):
        validate_gaql_templates.validate_field_metadata(
            ["campaign.id"], [{"name": "campaign.id", "selectable": False}]
        )
