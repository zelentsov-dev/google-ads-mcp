from __future__ import annotations

from datetime import date

from google_ads_mcp.errors import ValidationError


def parse_iso_date(value: str, *, field: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field} must be an ISO date (YYYY-MM-DD)") from exc
    if parsed.isoformat() != value:
        raise ValidationError(f"{field} must be an ISO date (YYYY-MM-DD)")
    return parsed


def validate_date_range(date_from: str, date_to: str, *, max_days: int) -> tuple[date, date]:
    start = parse_iso_date(date_from, field="dateFrom")
    end = parse_iso_date(date_to, field="dateTo")
    if end < start:
        raise ValidationError("dateTo must not be before dateFrom")
    if (end - start).days + 1 > max_days:
        raise ValidationError(f"Date range must not exceed {max_days} days")
    return start, end
