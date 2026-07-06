"""Lock the domain constant values (M11). These strings are persisted in the DB
and referenced in schema CHECK constraints, so they must not drift."""

from constants import AccountType, EntryType, TxnStatus


def test_account_types_and_debit_normal_rule():
    assert AccountType.ALL == ["Asset", "Liability", "Equity", "Revenue", "Expense"]
    assert AccountType.is_debit_normal("Asset")
    assert AccountType.is_debit_normal("Expense")
    for credit_normal in ("Liability", "Equity", "Revenue"):
        assert not AccountType.is_debit_normal(credit_normal)


def test_entry_types():
    assert EntryType.ALL == ["Regular", "Adjusting", "Closing", "Beginning Balance"]


def test_txn_statuses():
    assert (TxnStatus.PENDING, TxnStatus.CATEGORIZED, TxnStatus.POSTED) == \
        ("Pending", "Categorized", "Posted")
