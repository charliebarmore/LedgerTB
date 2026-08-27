"""Pure date-preset calculations shared by interactive reports."""

from datetime import date, timedelta

from utils.fiscal_dates import fiscal_year_bounds, previous_fiscal_year_bounds


PERIOD_PRESETS = (
    "This Fiscal Year",
    "Last Fiscal Year",
    "This Calendar Year",
    "Last Calendar Year",
    "Last 30 Days",
    "Last 90 Days",
    "Custom",
)

AS_OF_PRESETS = (
    "Today",
    "End of Last Month",
    "End of Last Fiscal Year",
    "End of Last Calendar Year",
    "Custom",
)


def period_for_preset(
    preset: str, today: date, fiscal_year_end_month: int
) -> tuple[date, date] | None:
    if preset == "This Fiscal Year":
        start, end = fiscal_year_bounds(today, fiscal_year_end_month)
        return start, min(today, end)
    if preset == "Last Fiscal Year":
        return previous_fiscal_year_bounds(today, fiscal_year_end_month)
    if preset == "This Calendar Year":
        return date(today.year, 1, 1), today
    if preset == "Last Calendar Year":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    if preset == "Last 30 Days":
        return today - timedelta(days=30), today
    if preset == "Last 90 Days":
        return today - timedelta(days=90), today
    return None

def as_of_for_preset(
    preset: str, today: date, fiscal_year_end_month: int
) -> date | None:
    if preset == "Today":
        return today
    if preset == "End of Last Month":
        return today.replace(day=1) - timedelta(days=1)
    if preset == "End of Last Fiscal Year":
        return previous_fiscal_year_bounds(today, fiscal_year_end_month)[1]
    if preset == "End of Last Calendar Year":
        return date(today.year - 1, 12, 31)
    return None
