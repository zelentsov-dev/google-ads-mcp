from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from google_ads_mcp.adapter import AdapterSearchResult
from google_ads_mcp.config import AccountsConfig, parse_config


@pytest.fixture
def accounts_config() -> AccountsConfig:
    return parse_config(
        {
            "profiles": [
                {
                    "name": "test-read-only",
                    "loginCustomerId": "1111111111",
                    "defaultCustomerId": "2222222222",
                    "auth": {
                        "type": "adc",
                        "developerTokenEnv": "TEST_GOOGLE_ADS_TOKEN",
                    },
                    "allowWrites": False,
                }
            ]
        },
        path=Path("/synthetic/accounts.json"),
    )


class FakeAdapter:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.result = AdapterSearchResult((), 0, False)
        self.test_account = False

    async def search(
        self, profile: Any, customer_id: str, query: str
    ) -> AdapterSearchResult:
        self.queries.append(query)
        if "customer.currency_code" in query:
            return AdapterSearchResult(
                (
                    {
                        "customer": {
                            "id": customer_id,
                            "currency_code": "USD",
                            "time_zone": "Etc/UTC",
                            "test_account": self.test_account,
                            "status": "ENABLED",
                        }
                    },
                ),
                1,
                False,
            )
        return self.result

    async def accessible_customers(self, profile: Any) -> tuple[str, ...]:
        return ("customers/2222222222", "customers/3333333333")

    async def field_metadata(
        self, profile: Any, field_names: list[str]
    ) -> tuple[dict[str, Any], ...]:
        return tuple({"name": name, "selectable": True} for name in field_names)

    async def validate_query(self, profile: Any, customer_id: str, query: str) -> None:
        self.queries.append(query)


@pytest.fixture
def fake_adapter() -> FakeAdapter:
    return FakeAdapter()
