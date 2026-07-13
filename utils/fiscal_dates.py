"""Fiscal-year date helpers shared by dashboards, filters, and reports."""

import calendar
from datetime import date


def fiscal_year_bounds(as_of_date: date, fiscal_year_end_month: int) -> tuple[date, date]:
    """Return the fiscal year containing ``as_of_date`` (inclusive bounds)."""
    if not 1 <= int(fiscal_year_end_month) <= 12:
        raise ValueError("Fiscal year-end month must be between 1 and 12.")
    end_month = int(fiscal_year_end_month)
    end_day = calendar.monthrange(as_of_date.year, end_month)[1]
    candidate_end = date(as_of_date.year, end_month, end_day)
    fiscal_end = candidate_end if as_of_date <= candidate_end else date(
        as_of_date.year + 1,
        end_month,
        calendar.monthrange(as_of_date.year + 1, end_month)[1],
    )
    start_month = end_month % 12 + 1
    start_year = fiscal_end.year if start_month == 1 else fiscal_end.year - 1
    return date(start_year, start_month, 1), fiscal_end


def previous_fiscal_year_bounds(
    as_of_date: date, fiscal_year_end_month: int
) -> tuple[date, date]:
    current_start, _ = fiscal_year_bounds(as_of_date, fiscal_year_end_month)
    previous_end = date.fromordinal(current_start.toordinal() - 1)
    return fiscal_year_bounds(previous_end, fiscal_year_end_month)


def require_valid_range(start_date: date, end_date: date, label: str = "Date") -> None:
    """Reject inverted inclusive date ranges at the domain boundary."""
    if start_date and end_date and start_date > end_date:
        raise ValueError(f"{label} start date cannot be after the end date.")
