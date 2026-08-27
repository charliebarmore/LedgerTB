"""Human date formatting that behaves the same on every platform.

strftime's no-leading-zero codes are libc extensions that differ per OS
(%-d on Unix, %#d on Windows — the latter raises "Invalid format string"
on the former and vice versa), so day/month/hour numbers are interpolated
by hand instead.
"""


def long_date(d) -> str:
    """August 4, 2026"""
    return f"{d:%B} {d.day}, {d:%Y}"


def short_date(d) -> str:
    """Aug 4, 2026"""
    return f"{d:%b} {d.day}, {d:%Y}"


def slash_date(d) -> str:
    """8/4/2026"""
    return f"{d.month}/{d.day}/{d:%Y}"


def display_date(value, date_format: str) -> str:
    """Format a date using the saved display preference."""
    from datetime import date

    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError:
            return value
    if date_format == "DD/MM/YYYY":
        return f"{value.day:02d}/{value.month:02d}/{value.year:04d}"
    if date_format == "YYYY/MM/DD":
        return f"{value.year:04d}/{value.month:02d}/{value.day:02d}"
    return f"{value.month:02d}/{value.day:02d}/{value.year:04d}"


def long_datetime(dt) -> str:
    """August 4, 2026 at 3:07 PM"""
    return f"{long_date(dt)} at {(dt.hour % 12) or 12}:{dt:%M} {dt:%p}"
