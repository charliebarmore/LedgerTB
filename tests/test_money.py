"""Tests for the dollars<->cents helpers (M2)."""

from money import to_cents, to_dollars


def test_to_cents_rounds_exactly():
    assert to_cents(33.33) == 3333          # not 3332 (binary-float trap)
    assert to_cents(66.67) == 6667
    assert to_cents(0.1) == 10
    assert to_cents(1000) == 100000
    assert to_cents("12.34") == 1234


def test_to_cents_half_up():
    assert to_cents(0.005) == 1             # rounds half up
    assert to_cents(2.675) == 268           # classic float-rounding case


def test_to_cents_negative_and_none():
    assert to_cents(-42.50) == -4250
    assert to_cents(None) == 0


def test_to_dollars_roundtrip():
    for d in [0.0, 33.33, 1000.0, -42.5, 0.1, 0.2]:
        assert to_dollars(to_cents(d)) == d
    assert to_dollars(None) == 0.0


def test_fractional_sum_is_exact_in_cents():
    # 33.33 + 33.33 + 33.34 = 100.00 exactly once in cents.
    cents = to_cents(33.33) + to_cents(33.33) + to_cents(33.34)
    assert cents == 10000
    assert to_dollars(cents) == 100.0
