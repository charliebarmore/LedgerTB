from datetime import date

import pytest

from conftest import post_entry
from models.journal_entry import JournalEntry
from models.fiscal_period import FiscalPeriod
from models.transaction import ImportedTransaction


def test_delete_entry_linked_to_imported_transaction_does_not_crash(client_id, accounts):
    """Regression for C2: deleting a journal entry that an imported_transactions
    row points at previously raised sqlite3.IntegrityError (the FK has no
    ON DELETE clause and PRAGMA foreign_keys is ON) and leaked the connection.
    The delete must succeed and the import record must survive with its link nulled."""
    entry = post_entry(client_id, date(2025, 5, 1), [
        (accounts["cash"], 100, 0),
        (accounts["revenue"], 0, 100),
    ])

    # An import-posted transaction referencing that entry (mirrors the posting flow).
    txn = ImportedTransaction(
        client_id=client_id,
        transaction_date=date(2025, 5, 1),
        description="ACME DEPOSIT",
        amount=100.0,
        bank_account_id=accounts["cash"],
        suggested_account_id=accounts["revenue"],
        status="Posted",
        journal_entry_id=entry.id,
    )
    txn.save()

    # Previously raised IntegrityError; must now succeed.
    JournalEntry.delete(entry.id)

    # The entry is gone...
    assert JournalEntry.get_by_id(entry.id) is None

    # ...but the import record survives with its journal_entry_id unlinked.
    posted = ImportedTransaction.get_by_status(client_id, "Posted")
    assert len(posted) == 1
    assert posted[0].journal_entry_id is None


def test_delete_plain_entry_still_works(client_id, accounts):
    """A manually-created entry with no import link deletes cleanly (unchanged behavior)."""
    entry = post_entry(client_id, date(2025, 5, 2), [
        (accounts["cash"], 50, 0),
        (accounts["revenue"], 0, 50),
    ])
    JournalEntry.delete(entry.id)
    assert JournalEntry.get_by_id(entry.id) is None


def test_closed_year_entry_cannot_be_moved_to_open_year(client_id, accounts):
    entry = post_entry(client_id, date(2025, 12, 31), [
        (accounts["cash"], 100, 0),
        (accounts["revenue"], 0, 100),
    ])
    FiscalPeriod(
        client_id=client_id,
        period_name="FY 2025",
        period_type="Year",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        is_closed=True,
    ).save()

    entry.entry_date = date(2026, 1, 1)
    with pytest.raises(ValueError, match="FY 2025 is closed"):
        entry.save()

    assert JournalEntry.get_by_id(entry.id).entry_date == date(2025, 12, 31)
