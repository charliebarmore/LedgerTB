from datetime import date

import pytest

from database.connection import get_connection
from models.journal_entry import JournalEntry
from models.transaction import ImportedTransaction
from services.posting import build_journal_entry, post_transaction


def _txn(amount, description="ACME STORE #123", d=date(2025, 4, 10)):
    return {"amount": amount, "description": description, "date": d, "batch_id": "B1"}


def _rule_count(client_id):
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM categorization_rules WHERE client_id = ?", (client_id,)
        ).fetchone()[0]
    finally:
        conn.close()


# ---- build_journal_entry: the four sign/transfer branches ----

def test_build_expense_debits_target_credits_bank(accounts):
    entry = build_journal_entry(1, _txn(-100.0), accounts["expense"], accounts["cash"])
    assert entry.is_balanced()
    target, bank = entry.lines
    assert target.account_id == accounts["expense"] and target.debit == 100.0
    assert bank.account_id == accounts["cash"] and bank.credit == 100.0


def test_build_deposit_debits_bank_credits_target(accounts):
    entry = build_journal_entry(1, _txn(250.0), accounts["revenue"], accounts["cash"])
    assert entry.is_balanced()
    bank, target = entry.lines
    assert bank.account_id == accounts["cash"] and bank.debit == 250.0
    assert target.account_id == accounts["revenue"] and target.credit == 250.0


def test_build_transfer_out_debits_target_credits_bank_and_flags(accounts):
    entry = build_journal_entry(
        1, _txn(-500.0, "CC PAYMENT"), accounts["credit_card"], accounts["cash"],
        is_transfer=True,
    )
    assert entry.is_balanced()
    assert entry.description.startswith("[Transfer]")
    target, bank = entry.lines
    assert target.account_id == accounts["credit_card"] and target.debit == 500.0
    assert "Transfer:" in (target.memo or "")
    assert bank.account_id == accounts["cash"] and bank.credit == 500.0


def test_build_transfer_in_debits_bank_credits_target(accounts):
    entry = build_journal_entry(
        1, _txn(500.0, "XFER FROM SAVINGS"), accounts["equity"], accounts["cash"],
        is_transfer=True,
    )
    assert entry.is_balanced()
    bank, target = entry.lines
    assert bank.account_id == accounts["cash"] and bank.debit == 500.0
    assert target.account_id == accounts["equity"] and target.credit == 500.0


# ---- post_transaction: atomic persistence ----

def test_post_transaction_writes_entry_import_record_and_pattern(client_id, accounts):
    entry, imported = post_transaction(
        client_id=client_id,
        transaction=_txn(-75.0),
        target_account_id=accounts["expense"],
        bank_account_id=accounts["cash"],
        batch_id="B1",
    )

    # Journal entry persisted and balanced.
    saved = JournalEntry.get_by_id(entry.id)
    assert saved is not None
    assert saved.is_balanced()

    # Import record persisted, marked Posted, and linked to the entry.
    posted = ImportedTransaction.get_by_status(client_id, "Posted")
    assert len(posted) == 1
    assert posted[0].journal_entry_id == entry.id

    # A categorization pattern was learned.
    assert _rule_count(client_id) == 1


def test_post_transaction_transfer_does_not_learn_pattern(client_id, accounts):
    post_transaction(
        client_id=client_id,
        transaction=_txn(-500.0, "CC PAYMENT"),
        target_account_id=accounts["credit_card"],
        bank_account_id=accounts["cash"],
        is_transfer=True,
        batch_id="B1",
    )
    assert _rule_count(client_id) == 0  # transfers are not expense/revenue patterns


def test_post_transaction_rolls_back_completely_on_failure(client_id, accounts):
    """Atomicity (M1): if any write fails, nothing is committed -- no orphan
    journal entry, no import record, no pattern. Here a bad target account id
    trips the journal_entry_lines FK after the journal_entries row is inserted."""
    bogus_account_id = 999999

    with pytest.raises(Exception):
        post_transaction(
            client_id=client_id,
            transaction=_txn(-75.0),
            target_account_id=bogus_account_id,
            bank_account_id=accounts["cash"],
            batch_id="B1",
        )

    assert JournalEntry.get_all(client_id) == []
    assert ImportedTransaction.get_by_status(client_id, "Posted") == []
    assert _rule_count(client_id) == 0


def test_post_transaction_rolls_back_entry_when_import_record_fails(client_id, accounts, monkeypatch):
    """The core M1 guarantee: a failure at the import-record stage (AFTER the
    journal entry has been written to the shared transaction) must roll the
    journal entry back too -- no orphan entry. Before extraction the entry was
    committed on its own connection first, so this scenario left an orphan."""
    def boom(self, conn=None):
        raise RuntimeError("simulated import-record failure")

    monkeypatch.setattr(ImportedTransaction, "save", boom)

    with pytest.raises(RuntimeError):
        post_transaction(
            client_id=client_id,
            transaction=_txn(-75.0),
            target_account_id=accounts["expense"],
            bank_account_id=accounts["cash"],
            batch_id="B1",
        )

    # The journal entry that was inserted before the failure must be gone.
    assert JournalEntry.get_all(client_id) == []
    assert _rule_count(client_id) == 0
