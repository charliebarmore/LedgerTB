"""Parse a chart-of-accounts CSV for bulk import.

Accepts flexible headers (QuickBooks/Excel exports vary) and normalizes account
types to the app's five canonical types. Pure/testable -- the page handles the
file upload and the actual inserts.
"""

import csv
import io

from constants import AccountType

# Recognized spellings for each column we care about.
_HEADER_ALIASES = {
    "number": {"account number", "account #", "acct #", "acct", "number",
               "account_number", "acct_number", "account no", "acct no", "no",
               "#", "code", "account code"},
    "name": {"name", "account name", "account_name", "account", "title"},
    "type": {"type", "account type", "account_type", "category", "class"},
    "subtype": {"subtype", "sub-type", "sub type", "sub_type", "detail type",
                "detail_type", "detail"},
    "description": {"description", "desc", "memo", "notes", "note"},
}

# Common type spellings -> canonical account type.
# Canonical names, plus the type vocabulary QuickBooks exports actually use.
# QB names carry accounting meaning beyond the five canonical types, so they
# imply a subtype when the file doesn't provide one — "Bank" without
# subtype "Cash" would import fine and then silently miss every feature that
# keys on cash accounts (receipts & disbursements, the close package).
_TYPE_ALIASES = {
    "asset": (AccountType.ASSET, None), "assets": (AccountType.ASSET, None),
    "liability": (AccountType.LIABILITY, None),
    "liabilities": (AccountType.LIABILITY, None),
    "equity": (AccountType.EQUITY, None), "equities": (AccountType.EQUITY, None),
    "revenue": (AccountType.REVENUE, None),
    "revenues": (AccountType.REVENUE, None),
    "income": (AccountType.REVENUE, None),
    "expense": (AccountType.EXPENSE, None),
    "expenses": (AccountType.EXPENSE, None),
    # QuickBooks account types
    "bank": (AccountType.ASSET, "Cash"),
    "accounts receivable": (AccountType.ASSET, "Receivable"),
    "accounts receivable (a/r)": (AccountType.ASSET, "Receivable"),
    "a/r": (AccountType.ASSET, "Receivable"),
    "other current asset": (AccountType.ASSET, "Other Current Asset"),
    "other current assets": (AccountType.ASSET, "Other Current Asset"),
    "fixed asset": (AccountType.ASSET, "Fixed Asset"),
    "fixed assets": (AccountType.ASSET, "Fixed Asset"),
    "other asset": (AccountType.ASSET, "Other Asset"),
    "other assets": (AccountType.ASSET, "Other Asset"),
    "accounts payable": (AccountType.LIABILITY, "Payable"),
    "accounts payable (a/p)": (AccountType.LIABILITY, "Payable"),
    "a/p": (AccountType.LIABILITY, "Payable"),
    "credit card": (AccountType.LIABILITY, "Credit Card"),
    "other current liability": (AccountType.LIABILITY, "Other Current Liability"),
    "other current liabilities": (AccountType.LIABILITY, "Other Current Liability"),
    "long term liability": (AccountType.LIABILITY, "Long Term Liability"),
    "long-term liability": (AccountType.LIABILITY, "Long Term Liability"),
    "long term liabilities": (AccountType.LIABILITY, "Long Term Liability"),
    "other income": (AccountType.REVENUE, "Other Income"),
    "cost of goods sold": (AccountType.EXPENSE, "Cost of Goods Sold"),
    "cogs": (AccountType.EXPENSE, "Cost of Goods Sold"),
    "other expense": (AccountType.EXPENSE, "Other Expense"),
    "other expenses": (AccountType.EXPENSE, "Other Expense"),
}


def _norm(s):
    return (s or "").strip().lower()


def _map_headers(fieldnames):
    """Return {canonical_field: actual_header} for the columns we recognize."""
    mapping = {}
    for actual in fieldnames or []:
        key = _norm(actual)
        for canon, aliases in _HEADER_ALIASES.items():
            if key in aliases and canon not in mapping:
                mapping[canon] = actual
    return mapping


def normalize_type(raw):
    """Map a raw type string to ``(AccountType, implied_subtype)``.

    Returns ``None`` for an unknown type. The implied subtype (e.g. "Cash"
    for a QuickBooks "Bank" account) is used only when the file has no
    explicit subtype column value for the row.
    """
    return _TYPE_ALIASES.get(_norm(raw))


def parse_coa_csv(content: str):
    """Parse a chart-of-accounts CSV.

    Returns ``(accounts, errors)``. Each account is a dict with keys
    ``number, name, type, subtype, description``. ``errors`` is a list of
    human-readable strings (missing columns, bad/duplicate rows).
    """
    errors = []
    accounts = []

    reader = csv.DictReader(io.StringIO(content))
    headers = _map_headers(reader.fieldnames)

    missing = [f for f in ("number", "name", "type") if f not in headers]
    if missing:
        found = ", ".join(reader.fieldnames or []) or "(none)"
        errors.append(
            f"Missing required column(s): {', '.join(missing)}. Found: {found}. "
            f"Need at least Account Number, Name, and Type."
        )
        return accounts, errors

    seen = set()
    for i, row in enumerate(reader, start=2):  # row 1 is the header
        number = (row.get(headers["number"]) or "").strip()
        name = (row.get(headers["name"]) or "").strip()
        raw_type = (row.get(headers["type"]) or "").strip()

        if not (number or name or raw_type):
            continue  # blank line
        if not number:
            errors.append(f"Row {i}: missing account number.")
            continue
        if not name:
            errors.append(f"Row {i}: missing name (#{number}).")
            continue
        mapped = normalize_type(raw_type)
        if mapped is None:
            errors.append(
                f"Row {i}: unknown type '{raw_type}' (#{number} {name}) — "
                f"this row will NOT be imported. Use one of: "
                f"{', '.join(AccountType.ALL)}, or a QuickBooks type name."
            )
            continue
        acct_type, implied_subtype = mapped
        if number in seen:
            errors.append(f"Row {i}: duplicate account number #{number} in the file.")
            continue
        seen.add(number)

        accounts.append({
            "number": number,
            "name": name,
            "type": acct_type,
            "subtype": (row.get(headers.get("subtype", "")) or "").strip()
            or implied_subtype,
            "description": (row.get(headers.get("description", "")) or "").strip() or None,
        })

    return accounts, errors
