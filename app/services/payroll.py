"""Studio payroll rules: 8h day, 26-day month, leave in hours."""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from app.models import LeaveRequest

WORK_HOURS_PER_DAY = 8
WORK_DAYS_PER_MONTH = 26
HOURS_PER_MONTH = WORK_HOURS_PER_DAY * WORK_DAYS_PER_MONTH  # 208


def hourly_from_monthly(monthly_gross_paise: Optional[int]) -> Optional[int]:
    if monthly_gross_paise is None:
        return None
    gross = max(0, int(monthly_gross_paise))
    if gross <= 0:
        return 0
    return round(gross / HOURS_PER_MONTH)


def leave_days(req: "LeaveRequest") -> float:
    if req.half_day:
        return 0.5
    return float((req.end_date - req.start_date).days + 1)


def leave_hours(req: "LeaveRequest") -> float:
    if req.half_day:
        return float(WORK_HOURS_PER_DAY / 2)  # 4
    return leave_days(req) * WORK_HOURS_PER_DAY


def leave_hours_in_month(req: "LeaveRequest", year: int, month: int) -> float:
    first = date(year, month, 1)
    last = date(year, month, monthrange(year, month)[1])
    if req.end_date < first or req.start_date > last:
        return 0.0
    if req.half_day:
        return float(WORK_HOURS_PER_DAY / 2) if first <= req.start_date <= last else 0.0
    start = max(req.start_date, first)
    end = min(req.end_date, last)
    days = (end - start).days + 1
    return float(max(0, days) * WORK_HOURS_PER_DAY)


def resolve_hourly_rate_paise(
    *,
    hourly_rate_paise: Optional[int],
    monthly_gross_paise: Optional[int],
) -> int:
    if hourly_rate_paise is not None and hourly_rate_paise > 0:
        return int(hourly_rate_paise)
    derived = hourly_from_monthly(monthly_gross_paise)
    return int(derived or 0)


def unpaid_deduction_paise(unpaid_hours: float, hourly_rate_paise: int) -> int:
    if unpaid_hours <= 0 or hourly_rate_paise <= 0:
        return 0
    return max(0, round(unpaid_hours * hourly_rate_paise))


def parse_month_key(month_key: str) -> Tuple[int, int]:
    year_s, month_s = month_key.split("-", 1)
    year, month = int(year_s), int(month_s)
    if month < 1 or month > 12:
        raise ValueError("Invalid month")
    return year, month
