"""Tests for the low-severity fixes: export sanitization and pattern matching."""

from datetime import date

import pandas as pd

from conftest import post_entry
from models.account import Account
from services.pattern_learning import PatternLearner
from utils.export import sanitize_cell, sanitize_df


# ---- CSV/Excel formula-injection defense ----

def test_sanitize_cell_prefixes_formula_triggers():
    assert sanitize_cell("=cmd|'/c calc'!A1") == "'=cmd|'/c calc'!A1"
    for bad in ("=SUM(A1)", "+1", "-1", "@X", "\tx", "\rx"):
        assert sanitize_cell(bad).startswith("'")


def test_sanitize_cell_passes_safe_values():
    assert sanitize_cell("ACME STORE") == "ACME STORE"
    assert sanitize_cell("1000") == "1000"
    assert sanitize_cell(123.45) == 123.45
    assert sanitize_cell(None) is None


def test_sanitize_df_only_touches_text_columns():
    df = pd.DataFrame({"desc": ["=danger", "safe"], "amount": [-5.0, 10.0]})
    out = sanitize_df(df)
    assert list(out["desc"]) == ["'=danger", "safe"]
    assert list(out["amount"]) == [-5.0, 10.0]   # numbers untouched


# ---- pattern matcher: no over-match on short patterns, no ZeroDivision ----

def test_find_match_ignores_empty_pattern_no_crash(client_id, accounts):
    """A stored empty/whitespace pattern must not raise ZeroDivision in the
    word-overlap check; it is skipped."""
    from database.connection import get_cursor
    with get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO categorization_rules (client_id, pattern, default_account_id, confidence, times_used) "
            "VALUES (?, '   ', ?, 1.0, 1)",
            (client_id, accounts["expense"]),
        )
    # Should simply find no match rather than crash.
    assert PatternLearner.find_match(client_id, "SOME DESCRIPTION") is None


def test_find_match_does_not_overmatch_short_pattern(client_id, accounts):
    """A 1-3 char pattern must not substring-match an unrelated description."""
    from database.connection import get_cursor
    with get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO categorization_rules (client_id, pattern, default_account_id, confidence, times_used) "
            "VALUES (?, 'ab', ?, 1.0, 1)",
            (client_id, accounts["expense"]),
        )
    # 'ab' appears inside 'grab' but is too short to be a meaningful match.
    assert PatternLearner.find_match(client_id, "GRAB TAXI") is None


def test_find_match_still_matches_real_pattern(client_id, accounts):
    """A normal-length learned pattern still matches (regression guard)."""
    PatternLearner.learn_pattern(client_id, "STARBUCKS STORE 123", accounts["expense"])
    match = PatternLearner.find_match(client_id, "STARBUCKS STORE 456")
    assert match is not None
    assert match["account_id"] == accounts["expense"]
