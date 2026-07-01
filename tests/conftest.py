import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from database import connection as db_connection
from database.connection import init_database
from models.client import Client
from models.account import Account
from models.journal_entry import JournalEntry, JournalEntryLine


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Point the app at a throwaway SQLite file for this test only."""
    monkeypatch.setattr(db_connection, "DATABASE_PATH", tmp_path / "test.db")
    init_database()


@pytest.fixture
def client_id(db):
    client = Client(name="Test Co", entity_type="S-Corp", fiscal_year_end_month=12)
    return client.save(seed_accounts=False)


@pytest.fixture
def accounts(client_id):
    """A minimal chart of accounts covering every account type."""

    def make(account_number, name, account_type):
        account = Account(client_id=client_id, account_number=account_number, name=name, type=account_type)
        account.save()
        return account.id

    return {
        "cash": make("1000", "Cash", "Asset"),
        "credit_card": make("2000", "Credit Card Payable", "Liability"),
        "equity": make("3000", "Owner's Equity", "Equity"),
        "revenue": make("4000", "Service Revenue", "Revenue"),
        "expense": make("6000", "Office Expense", "Expense"),
    }


def post_entry(client_id, entry_date, lines, entry_type="Regular"):
    """lines: list of (account_id, debit, credit) tuples."""
    entry = JournalEntry(
        client_id=client_id,
        entry_date=entry_date,
        description="test entry",
        entry_type=entry_type,
        lines=[JournalEntryLine(account_id=a, debit=d, credit=c) for a, d, c in lines],
    )
    entry.save()
    return entry
