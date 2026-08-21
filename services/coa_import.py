"""Parse a chart-of-accounts CSV for bulk import.

Accepts flexible headers (QuickBooks/Excel exports vary) and normalizes account
types to the app's five canonical types. Pure/testable -- the page handles the
file upload and the actual inserts.
"""

import csv
import io

from constants import AccountSubtype, AccountType

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
    "bank": (AccountType.ASSET, AccountSubtype.CASH),
    "accounts receivable": (AccountType.ASSET, AccountSubtype.ACCOUNTS_RECEIVABLE),
    "accounts receivable (a/r)": (AccountType.ASSET, AccountSubtype.ACCOUNTS_RECEIVABLE),
    "a/r": (AccountType.ASSET, AccountSubtype.ACCOUNTS_RECEIVABLE),
    "other current asset": (AccountType.ASSET, AccountSubtype.OTHER_CURRENT_ASSET),
    "other current assets": (AccountType.ASSET, AccountSubtype.OTHER_CURRENT_ASSET),
    "fixed asset": (AccountType.ASSET, AccountSubtype.FIXED_ASSET),
    "fixed assets": (AccountType.ASSET, AccountSubtype.FIXED_ASSET),
    "other asset": (AccountType.ASSET, AccountSubtype.OTHER_ASSET),
    "other assets": (AccountType.ASSET, AccountSubtype.OTHER_ASSET),
    "accounts payable": (AccountType.LIABILITY, AccountSubtype.ACCOUNTS_PAYABLE),
    "accounts payable (a/p)": (AccountType.LIABILITY, AccountSubtype.ACCOUNTS_PAYABLE),
    "a/p": (AccountType.LIABILITY, AccountSubtype.ACCOUNTS_PAYABLE),
    "credit card": (AccountType.LIABILITY, AccountSubtype.CREDIT_CARD),
    "other current liability": (AccountType.LIABILITY, AccountSubtype.OTHER_CURRENT_LIABILITY),
    "other current liabilities": (AccountType.LIABILITY, AccountSubtype.OTHER_CURRENT_LIABILITY),
    "short term liability": (AccountType.LIABILITY, AccountSubtype.SHORT_TERM_DEBT),
    "short-term liability": (AccountType.LIABILITY, AccountSubtype.SHORT_TERM_DEBT),
    "long term liability": (AccountType.LIABILITY, AccountSubtype.LONG_TERM_LIABILITY),
    "long-term liability": (AccountType.LIABILITY, AccountSubtype.LONG_TERM_LIABILITY),
    "long term liabilities": (AccountType.LIABILITY, AccountSubtype.LONG_TERM_LIABILITY),
    "other income": (AccountType.REVENUE, AccountSubtype.OTHER_INCOME),
    "cost of goods sold": (AccountType.EXPENSE, AccountSubtype.COST_OF_GOODS_SOLD),
    "cogs": (AccountType.EXPENSE, AccountSubtype.COST_OF_GOODS_SOLD),
    "other expense": (AccountType.EXPENSE, AccountSubtype.OTHER_EXPENSE),
    "other expenses": (AccountType.EXPENSE, AccountSubtype.OTHER_EXPENSE),
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

    # Number is optional (QuickBooks Online exports often have no number
    # column at all) — the importer assigns numbers by type range instead.
    missing = [f for f in ("name", "type") if f not in headers]
    if missing:
        found = ", ".join(reader.fieldnames or []) or "(none)"
        errors.append(
            f"Missing required column(s): {', '.join(missing)}. Found: {found}. "
            f"Need at least Name and Type (Account Number is optional)."
        )
        return accounts, errors

    seen = set()
    for i, row in enumerate(reader, start=2):  # row 1 is the header
        number = (row.get(headers.get("number", "")) or "").strip()
        name = (row.get(headers["name"]) or "").strip()
        raw_type = (row.get(headers["type"]) or "").strip()

        if not (number or name or raw_type):
            continue  # blank line
        if not raw_type:
            errors.append(f"Row {i}: missing type ({name or '#' + number}).")
            continue
        # A missing number is not an error — QuickBooks Online exports
        # commonly have none. The importer assigns one by type range and
        # shows the user what it chose (assign_missing_numbers).
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
        if number and number in seen:
            errors.append(f"Row {i}: duplicate account number #{number} in the file.")
            continue
        if number:
            seen.add(number)

        raw_subtype = (
            (row.get(headers.get("subtype", "")) or "").strip()
            or implied_subtype
        )
        accounts.append({
            "number": number,
            "name": name,
            "type": acct_type,
            "subtype": AccountSubtype.normalize_for_storage(
                acct_type, raw_subtype, account_name=name
            ),
            "description": (row.get(headers.get("description", "")) or "").strip() or None,
        })

    return accounts, errors


# Number ranges by type, matching the default seed chart's conventions.
_NUMBER_BASES = {
    AccountType.ASSET: 1000, AccountType.LIABILITY: 2000,
    AccountType.EQUITY: 3000, AccountType.REVENUE: 4000,
    AccountType.EXPENSE: 6000,
}


def assign_missing_numbers(accounts, taken=frozenset()):
    """Give numberless accounts numbers by type range, never colliding.

    QuickBooks Online turns account numbers off by default, so names-only
    charts are common. Numbers are how LedgerTB (and an assistant) reference
    accounts unambiguously, so instead of demanding them we assign them —
    type base upward in steps of 10, skipping anything in ``taken`` or in the
    file itself. Mutates the dicts, sets ``number_assigned: True`` on each
    one changed, and returns the list of (number, name) assignments so every
    caller can SHOW the user what was chosen.
    """
    used = {str(t) for t in taken}
    used.update(a["number"] for a in accounts if a.get("number"))
    assigned = []
    for account in accounts:
        if account.get("number"):
            continue
        base = _NUMBER_BASES.get(account["type"], 9000)
        candidate = base
        while str(candidate) in used:
            candidate += 10
        account["number"] = str(candidate)
        account["number_assigned"] = True
        used.add(str(candidate))
        assigned.append((str(candidate), account["name"]))
    return assigned
