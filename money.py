"""Money conversion helpers (M2).

Money is stored in the database as INTEGER cents so that storage, SQL SUM()s,
and balance checks are exact (binary floats cannot represent most cent values).
The application interfaces stay in dollars; conversion happens at the persistence
and report boundaries via these helpers.
"""

from decimal import Decimal, ROUND_HALF_UP


def to_cents(amount) -> int:
    """Convert a dollar amount to integer cents, rounded half-up.

    Accepts float/int/str/Decimal. Uses Decimal(str(amount)) so that a float
    like 33.33 becomes exactly 3333 rather than 3332 from binary rounding.
    """
    if amount is None:
        return 0
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def to_dollars(cents) -> float:
    """Convert integer cents back to a dollar float for display/interfaces."""
    if cents is None:
        return 0.0
    return int(cents) / 100.0
