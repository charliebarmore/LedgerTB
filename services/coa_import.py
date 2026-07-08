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
_TYPE_ALIASES = {
    "asset": AccountType.ASSET, "assets": AccountType.ASSET,
    "liability": AccountType.LIABILITY, "liabilities": AccountType.LIABILITY,
    "equity": AccountType.EQUITY, "equities": AccountType.EQUITY,
    "revenue": AccountType.REVENUE, "revenues": AccountType.REVENUE,
    "income": AccountType.REVENUE,
    "expense": AccountType.EXPENSE, "expenses": AccountType.EXPENSE,
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
    """Map a raw type string to a canonical AccountType, or None if unknown."""
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
        acct_type = normalize_type(raw_type)
        if acct_type is None:
            errors.append(
                f"Row {i}: unknown type '{raw_type}' (#{number}). "
                f"Use one of: {', '.join(AccountType.ALL)}."
            )
            continue
        if number in seen:
            errors.append(f"Row {i}: duplicate account number #{number} in the file.")
            continue
        seen.add(number)

        accounts.append({
            "number": number,
            "name": name,
            "type": acct_type,
            "subtype": (row.get(headers.get("subtype", "")) or "").strip() or None,
            "description": (row.get(headers.get("description", "")) or "").strip() or None,
        })

    return accounts, errors
