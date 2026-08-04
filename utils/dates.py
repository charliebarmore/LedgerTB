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


def long_datetime(dt) -> str:
    """August 4, 2026 at 3:07 PM"""
    return f"{long_date(dt)} at {(dt.hour % 12) or 12}:{dt:%M} {dt:%p}"
