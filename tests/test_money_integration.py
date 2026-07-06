"""End-to-end exactness tests for integer-cents money storage (M2)."""

from datetime import date

from conftest import post_entry
from database.connection import get_cursor
from models.journal_entry import JournalEntry, JournalEntryLine
from models.reports import ReportGenerator


def test_fractional_dollar_line_roundtrips_exactly(client_id, accounts):
    """A line of 33.33 must persist and read back as exactly 33.33 (not 33.32
    or 33.330000001)."""
    entry = post_entry(client_id, date(2025, 1, 1),
                       [(accounts["cash"], 33.33, 0), (accounts["revenue"], 0, 33.33)])
    reloaded = JournalEntry.get_by_id(entry.id)
    debits = {ln.debit for ln in reloaded.lines if ln.debit}
    assert debits == {33.33}


def test_money_stored_as_integer_cents(client_id, accounts):
    post_entry(client_id, date(2025, 1, 1),
               [(accounts["cash"], 33.33, 0), (accounts["revenue"], 0, 33.33)])
    with get_cursor() as cur:
        vals = [r[0] for r in cur.execute(
            "SELECT debit FROM journal_entry_lines WHERE debit > 0").fetchall()]
    assert vals == [3333]           # integer cents in storage, exactly
    assert all(isinstance(v, int) for v in vals)


def test_fractional_split_balances_and_ties_out(client_id, accounts):
    """33.33 + 33.33 + 33.34 = 100.00: the three-way split balances exactly and
    the trial balance ties to the penny (the classic float-drift scenario)."""
    entry = JournalEntry(
        client_id=client_id, entry_date=date(2025, 3, 1), description="split",
        lines=[
            JournalEntryLine(account_id=accounts["expense"], debit=33.33, credit=0),
            JournalEntryLine(account_id=accounts["expense"], debit=33.33, credit=0),
            JournalEntryLine(account_id=accounts["expense"], debit=33.34, credit=0),
            JournalEntryLine(account_id=accounts["cash"], debit=0, credit=100.00),
        ],
    )
    assert entry.is_balanced()
    entry.save()  # would raise if not balanced

    tb = ReportGenerator.trial_balance(client_id, date(2025, 12, 31))
    assert sum(r.debit for r in tb) == sum(r.credit for r in tb) == 100.00


def test_entry_off_by_one_cent_does_not_balance(client_id, accounts):
    """No floating-point tolerance: a $0.01 imbalance is rejected exactly."""
    entry = JournalEntry(
        client_id=client_id, entry_date=date(2025, 3, 1), description="off by a cent",
        lines=[
            JournalEntryLine(account_id=accounts["expense"], debit=100.00, credit=0),
            JournalEntryLine(account_id=accounts["cash"], debit=0, credit=99.99),
        ],
    )
    assert not entry.is_balanced()
