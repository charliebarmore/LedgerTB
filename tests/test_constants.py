"""Lock the domain constant values (M11). These strings are persisted in the DB
and referenced in schema CHECK constraints, so they must not drift."""

from constants import AccountSubtype, AccountType, EntryType, TxnStatus


def test_account_types_and_debit_normal_rule():
    assert AccountType.ALL == ["Asset", "Liability", "Equity", "Revenue", "Expense"]
    assert AccountType.is_debit_normal("Asset")
    assert AccountType.is_debit_normal("Expense")
    for credit_normal in ("Liability", "Equity", "Revenue"):
        assert not AccountType.is_debit_normal(credit_normal)
    assert [AccountType.plural_label(value) for value in AccountType.ALL] == [
        "Assets", "Liabilities", "Equities", "Revenues", "Expenses"
    ]


def test_entry_types():
    assert EntryType.ALL == ["Regular", "Adjusting", "Closing", "Beginning Balance"]


def test_account_subtypes_are_ordered_by_account_type():
    assert AccountSubtype.for_type(AccountType.ASSET) == [
        "Cash", "Accounts Receivable", "Inventory", "Other Current Asset",
        "Fixed Asset", "Accumulated Depreciation", "Other Asset",
    ]
    assert AccountSubtype.SHORT_TERM_DEBT in AccountSubtype.for_type(
        AccountType.LIABILITY
    )
    assert AccountSubtype.resolve(
        AccountType.LIABILITY, "Payable", "Visa Business Card"
    ) == AccountSubtype.CREDIT_CARD
    assert AccountSubtype.resolve(
        AccountType.EQUITY, "Capital", "Treasury Stock"
    ) == AccountSubtype.OTHER_EQUITY
    assert not AccountSubtype.is_canonical(AccountType.ASSET, "Receivable")


def test_txn_statuses():
    assert (TxnStatus.PENDING, TxnStatus.CATEGORIZED, TxnStatus.POSTED,
            TxnStatus.DISMISSED) == \
        ("Pending", "Categorized", "Posted", "Dismissed")
