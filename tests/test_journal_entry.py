from datetime import date

import pytest

from conftest import post_entry
from models.journal_entry import JournalEntry, JournalEntryLine
from models.fiscal_period import FiscalPeriod
from models.transaction import ImportedTransaction


def test_delete_entry_linked_to_imported_transaction_is_blocked(client_id, accounts):
    """Imported source history must never be left marked Posted without its entry."""
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

    with pytest.raises(ValueError, match="Reverse it instead"):
        JournalEntry.delete(entry.id)

    assert JournalEntry.get_by_id(entry.id) is not None

    posted = ImportedTransaction.get_by_status(client_id, "Posted")
    assert len(posted) == 1
    assert posted[0].journal_entry_id == entry.id


def test_delete_plain_entry_still_works(client_id, accounts):
    """A manually-created entry with no import link deletes cleanly (unchanged behavior)."""
    entry = post_entry(client_id, date(2025, 5, 2), [
        (accounts["cash"], 50, 0),
        (accounts["revenue"], 0, 50),
    ])
    JournalEntry.delete(entry.id)
    assert JournalEntry.get_by_id(entry.id) is None


def test_negative_journal_amounts_are_rejected(client_id, accounts):
    entry = JournalEntry(
        client_id=client_id,
        entry_date=date(2025, 5, 3),
        lines=[
            JournalEntryLine(account_id=accounts["cash"], debit=-100, credit=0),
            JournalEntryLine(account_id=accounts["revenue"], debit=0, credit=-100),
        ],
    )

    with pytest.raises(ValueError, match="cannot be negative"):
        entry.save()


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


def test_entry_list_filters_by_search_and_account(client_id, accounts):
    """Search matches description/reference/amount; account filter matches lines."""
    transfer = post_entry(
        client_id, date(2026, 3, 21),
        [(accounts["cash"], 1200, 0), (accounts["equity"], 0, 1200)],
    )
    transfer.description = "Transfer from Relay #7313"
    transfer.save()
    post_entry(
        client_id, date(2026, 2, 1),
        [(accounts["expense"], 15, 0), (accounts["credit_card"], 0, 15)],
    )

    by_text = JournalEntry.get_all(client_id, search_term="relay")
    assert [e.id for e in by_text] == [transfer.id]

    by_amount = JournalEntry.get_all(client_id, search_term="1,200.00")
    assert [e.id for e in by_amount] == [transfer.id]

    by_account = JournalEntry.get_all(client_id, account_id=accounts["credit_card"])
    assert len(by_account) == 1 and by_account[0].id != transfer.id

    # a zero search must not match the whole journal via empty line sides
    assert JournalEntry.get_all(client_id, search_term="0.00") == []

    summary = JournalEntry.get_filtered_summary(
        client_id, search_term="relay"
    )
    assert summary["total_count"] == 1
    assert summary["total_debits"] == 1200.0


def test_delete_reversal_entry_referenced_by_import_history_is_blocked(client_id, accounts):
    """A reversal the import history points to must refuse deletion with a
    plain-language message, not surface a raw foreign-key IntegrityError."""
    original = post_entry(client_id, date(2025, 5, 1), [
        (accounts["cash"], 100, 0),
        (accounts["revenue"], 0, 100),
    ])
    reversal = post_entry(client_id, date(2025, 6, 1), [
        (accounts["revenue"], 100, 0),
        (accounts["cash"], 0, 100),
    ])
    txn = ImportedTransaction(
        client_id=client_id,
        transaction_date=date(2025, 5, 1),
        description="ACME DEPOSIT",
        amount=100.0,
        bank_account_id=accounts["cash"],
        status="Posted",
        journal_entry_id=original.id,
    )
    txn.save()
    from database.connection import get_connection

    with get_connection() as conn:
        conn.execute(
            "UPDATE imported_transactions SET reversal_journal_entry_id = ? WHERE id = ?",
            (reversal.id, txn.id),
        )
        conn.commit()

    with pytest.raises(ValueError, match="must stay in the books"):
        JournalEntry.delete(reversal.id)

    assert JournalEntry.get_by_id(reversal.id) is not None
