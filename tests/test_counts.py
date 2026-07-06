"""Tests for the COUNT(*) helpers used on the home page (M8)."""

from datetime import date

from conftest import post_entry
from models.account import Account
from models.journal_entry import JournalEntry


def test_journal_entry_count(client_id, accounts):
    assert JournalEntry.count(client_id) == 0
    post_entry(client_id, date(2025, 1, 1), [(accounts["cash"], 100, 0), (accounts["revenue"], 0, 100)])
    post_entry(client_id, date(2025, 1, 2), [(accounts["cash"], 50, 0), (accounts["revenue"], 0, 50)])
    assert JournalEntry.count(client_id) == 2


def test_journal_entry_count_not_capped(client_id, accounts):
    """The old home-page count used get_all(limit=1000) and silently undercounted.
    COUNT(*) must report the true total regardless of any list limit."""
    for i in range(5):
        post_entry(client_id, date(2025, 1, 1), [(accounts["cash"], 1, 0), (accounts["revenue"], 0, 1)])
    # get_all with a small limit undercounts; count() does not.
    assert len(JournalEntry.get_all(client_id, limit=3)) == 3
    assert JournalEntry.count(client_id) == 5


def test_account_count(client_id, accounts):
    # The `accounts` fixture creates 5 accounts.
    assert Account.count(client_id) == 5

    inactive = Account(client_id=client_id, account_number="9999", name="Old", type="Expense", is_active=False)
    inactive.save()
    assert Account.count(client_id) == 5              # active-only by default
    assert Account.count(client_id, active_only=False) == 6
