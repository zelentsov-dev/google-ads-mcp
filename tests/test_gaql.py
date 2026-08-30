from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from google_ads_mcp.errors import ValidationError
from google_ads_mcp.gaql import DENIED_RESOURCES, validate_gaql


def test_valid_inventory_query() -> None:
    result = validate_gaql("SELECT campaign.id, campaign.name FROM campaign LIMIT 100")
    assert result.resource == "campaign"
    assert result.limit == 100
    assert not result.has_metrics


def test_valid_metric_query_with_dates() -> None:
    result = validate_gaql(
        "SELECT campaign.id, metrics.clicks FROM campaign "
        "WHERE segments.date BETWEEN '2026-01-01' AND '2026-01-31' LIMIT 1000"
    )
    assert result.has_metrics
    assert result.date_from == "2026-01-01"
    assert result.date_to == "2026-01-31"


def test_valid_metric_query_with_bounds_or_during() -> None:
    bounded = validate_gaql(
        "SELECT metrics.clicks FROM campaign WHERE segments.date >= '2026-01-01' "
        "AND segments.date <= '2026-01-02' LIMIT 1"
    )
    assert bounded.date_from == "2026-01-01"
    during = validate_gaql(
        "SELECT metrics.clicks FROM campaign WHERE segments.date DURING LAST_30_DAYS LIMIT 1"
    )
    assert during.date_from is None
    assert during.predefined_date_range == "LAST_30_DAYS"


def test_metrics_in_where_or_order_by_require_real_date_predicate() -> None:
    filtered = validate_gaql(
        "SELECT campaign.id FROM campaign WHERE metrics.clicks > 0 "
        "AND segments.date = '2026-01-01' LIMIT 10"
    )
    assert filtered.has_metrics is True
    assert filtered.date_from == "2026-01-01"
    ordered = validate_gaql(
        "SELECT campaign.id FROM campaign WHERE segments.date DURING LAST_7_DAYS "
        "ORDER BY metrics.clicks DESC LIMIT 10"
    )
    assert ordered.has_metrics is True
    assert ordered.predefined_date_range == "LAST_7_DAYS"


def test_literals_do_not_act_as_gaql_syntax() -> None:
    result = validate_gaql(
        "SELECT campaign.id FROM campaign "
        "WHERE campaign.name = 'DELETE; -- # /* segments.date DURING LAST_30_DAYS' LIMIT 1"
    )
    assert result.query.endswith("LIMIT 1")
    with pytest.raises(ValidationError, match="bounded"):
        validate_gaql(
            "SELECT metrics.clicks FROM campaign "
            "WHERE campaign.name = 'segments.date DURING LAST_30_DAYS' LIMIT 1"
        )


def test_escaped_literal_and_exclusive_date_bounds() -> None:
    literal = validate_gaql(
        "SELECT campaign.id FROM campaign WHERE campaign.name = 'name\\'s' LIMIT 1"
    )
    assert literal.limit == 1
    bounded = validate_gaql(
        "SELECT metrics.clicks FROM campaign WHERE segments.date > '2026-01-01' "
        "AND segments.date < '2026-01-04' LIMIT 1"
    )
    assert bounded.date_from == "2026-01-02"
    assert bounded.date_to == "2026-01-03"


def test_unterminated_literal_is_rejected_deterministically() -> None:
    with pytest.raises(ValidationError, match="unterminated string literal"):
        validate_gaql(
            "SELECT campaign.id FROM campaign WHERE campaign.name = 'unterminated LIMIT 1"
        )


@pytest.mark.parametrize(
    "query",
    [
        "",
        "DELETE FROM campaign LIMIT 1",
        "SELECT campaign.id FROM campaign; SELECT campaign.id FROM campaign LIMIT 1",
        "SELECT campaign.id FROM campaign -- comment LIMIT 1",
        "SELECT campaign.id FROM campaign # comment LIMIT 1",
        "SELECT campaign.id FROM campaign /* comment */ LIMIT 1",
        "SELECT campaign.id, FROM campaign LIMIT 1",
        "SELECT * FROM campaign LIMIT 1",
        "SELECT campaign.id FROM campaign",
        "SELECT campaign.id FROM campaign LIMIT 0",
        "SELECT campaign.id FROM campaign LIMIT 1001",
        "SELECT metrics.clicks FROM campaign LIMIT 1",
        "SELECT metrics.clicks FROM campaign WHERE segments.date BETWEEN "
        "'2024-01-01' AND '2026-01-01' LIMIT 1",
        "SELECT metrics.clicks FROM campaign WHERE segments.date DURING ALL_TIME LIMIT 1",
        "SELECT campaign.id FROM campaign FROM ad_group LIMIT 1",
        "SELECT customer.email FROM customer LIMIT 1",
        "SELECT change_event.change_date_time FROM change_event "
        "WHERE change_event.user_email = 'person@example.test' LIMIT 1",
        "SELECT FROM campaign LIMIT 1",
        "SELECT campaign.id FROM campaign LIMIT 1" + (" " * 20_001),
        "SELECT campaign.id FROM campaign WHERE metrics.clicks > 0 LIMIT 1000",
        "SELECT metrics.clicks FROM campaign "
        "WHERE campaign.name = 'segments.date DURING LAST_30_DAYS' LIMIT 1000",
        "SELECT metrics.clicks FROM campaign WHERE segments.date >= '2026-01-01' LIMIT 1",
        "SELECT metrics.clicks FROM campaign WHERE segments.date <= '2026-01-01' LIMIT 1",
        "SELECT campaign.id FROM campaign LIMIT 1 LIMIT 1",
        "SELECT campaign.id FROM campaign LIMIT 1 trailing",
        "SELECT campaign.id FROM campaign WHERE campaign.name = DELETE LIMIT 1",
        "SELECT campaign.id FROM",
        "SELECT campaign.id FROM 123 LIMIT 1",
        "SELECT metrics.clicks FROM campaign WHERE segments.date >= 20260101 "
        "AND segments.date <= '2026-01-02' LIMIT 1",
        "SELECT metrics.clicks FROM campaign WHERE segments.date >= '2026/01/01' "
        "AND segments.date <= '2026-01-02' LIMIT 1",
        "SELECT metrics.clicks FROM campaign WHERE segments.date >= '2026-02-30' "
        "AND segments.date <= '2026-03-01' LIMIT 1",
        "SELECT metrics.clicks FROM campaign WHERE segments.date LIMIT 1",
        "SELECT metrics.clicks FROM campaign WHERE segments.date "
        "BETWEEN '2026-01-01' OR '2026-01-02' LIMIT 1",
        "SELECT metrics.clicks FROM campaign WHERE segments.date "
        "BETWEEN '2026-01-01' XOR '2026-01-02' LIMIT 1",
        "SELECT metrics.clicks FROM campaign WHERE segments.date != '2026-01-01' LIMIT 1",
        "SELECT metrics.clicks FROM campaign WHERE segments.date IS NULL LIMIT 1",
        "SELECT metrics.clicks FROM campaign WHERE segments.date DURING LAST_7_DAYS "
        "AND segments.date DURING LAST_30_DAYS LIMIT 1",
        "SELECT metrics.clicks FROM campaign WHERE metrics.clicks > 0 "
        "OR segments.date = '2026-01-01' LIMIT 1",
        "SELECT metrics.clicks FROM campaign WHERE NOT segments.date = '2026-01-01' LIMIT 1",
    ],
)
def test_rejects_unsafe_gaql(query: str) -> None:
    with pytest.raises(ValidationError):
        validate_gaql(query)


@pytest.mark.parametrize("resource", sorted(DENIED_RESOURCES))
def test_denied_resources(resource: str) -> None:
    with pytest.raises(ValidationError, match="not allowed"):
        validate_gaql(f"SELECT {resource}.resource_name FROM {resource} LIMIT 1")


def test_account_budget_proposal_billing_fields_are_explicitly_denied() -> None:
    with pytest.raises(ValidationError, match="not allowed"):
        validate_gaql(
            "SELECT account_budget_proposal.purchase_order_number, "
            "account_budget_proposal.approved_spending_limit_micros "
            "FROM account_budget_proposal LIMIT 1"
        )


@given(
    st.tuples(st.text(max_size=49), st.sampled_from([";", "#"]), st.text(max_size=49)).map("".join)
)
def test_malicious_arbitrary_text_never_executes(value: str) -> None:
    with pytest.raises(ValidationError):
        validate_gaql(value)
