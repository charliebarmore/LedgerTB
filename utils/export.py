"""Spreadsheet-export hardening (CSV/Excel formula injection).

A cell whose text starts with =, +, -, @ (or a tab/CR) is interpreted as a
formula by Excel/Sheets, so an attacker-controlled value like ``=cmd|'/c calc'``
in a bank description would execute when the CPA opens the exported file. Prefix
such cells with a single quote so they are treated as literal text.
"""

_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_cell(value):
    """Prefix a leading formula trigger with a single quote; pass other values
    (numbers, safe strings, None) through unchanged."""
    if isinstance(value, str) and value and value[0] in _DANGEROUS_PREFIXES:
        return "'" + value
    return value


def sanitize_df(df):
    """Return a copy of ``df`` with text (object) columns sanitized for export.
    Numeric columns are left untouched (they can't be formulas)."""
    df = df.copy()
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].map(sanitize_cell)
    return df
