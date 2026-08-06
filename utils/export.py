"""Spreadsheet-export hardening (CSV/Excel formula injection).

A cell whose text starts with =, +, -, @ (or a tab/CR) is interpreted as a
formula by Excel/Sheets, so an attacker-controlled value like ``=cmd|'/c calc'``
in a bank description would execute when the CPA opens the exported file. Prefix
such cells with a single quote so they are treated as literal text.
"""

from pandas.api import types as _ptypes

_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_cell(value):
    """Prefix a leading formula trigger with a single quote; pass other values
    (numbers, safe strings, None) through unchanged."""
    if isinstance(value, str) and value and value[0] in _DANGEROUS_PREFIXES:
        return "'" + value
    return value


def _holds_text(series):
    """True if a column could contain strings, across pandas 2 and 3.

    Do NOT narrow this back to ``dtype == object``. Pandas 3 gives text columns a
    dedicated string dtype, so that check is False for exactly the columns that
    need sanitizing — the guard silently stops running and formulas reach the
    exported workbook. Caught by a fresh-install test run on pandas 3.0.5.
    """
    return _ptypes.is_object_dtype(series) or _ptypes.is_string_dtype(series)


def sanitize_df(df):
    """Return a copy of ``df`` with text columns sanitized for export.
    Numeric columns are left untouched (they can't be formulas)."""
    df = df.copy()
    for col in df.columns:
        if _holds_text(df[col]):
            df[col] = df[col].map(sanitize_cell)
    return df


def set_excel_literal(cell, value):
    """Write an OpenPyXL cell while forcing string values to remain text.

    OpenPyXL otherwise classifies a leading ``=`` as a formula. Explicitly
    storing text preserves the exact visible value without adding an apostrophe.
    Intentional formulas should be assigned directly instead of using this helper.
    """
    cell.value = value
    if isinstance(value, str):
        cell.data_type = "s"
    return cell
