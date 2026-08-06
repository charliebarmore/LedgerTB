import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Before ANY app module loads: config reads the Anthropic key at import time,
# falling back to the macOS credential vault. From a pytest process that read
# can raise a Keychain authorization dialog no headless run can answer — the
# suite hangs forever. A dummy env key short-circuits the vault entirely.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-never-used")

import pytest

from database import connection as db_connection
from database.connection import init_database
from models.client import Client
from models.account import Account
from models.journal_entry import JournalEntry, JournalEntryLine


@pytest.fixture(autouse=True)
def _human_actor_by_default(monkeypatch):
    """mcp_server's vault unlock marks the whole process as assistant
    (utils.actor._ASSISTANT); without this reset, one vault-unlock test taints
    every later test's writes with the "(AI)" stamp."""
    from utils import actor

    monkeypatch.setattr(actor, "_ASSISTANT", False)


@pytest.fixture(autouse=True)
def fake_credential_vault(request, monkeypatch):
    """No test may touch the real credential vault (see the env note above)."""
    if request.node.get_closest_marker("real_vault"):
        # secure_store's own tests stub the keyring library directly.
        return None

    import utils.secure_store as secure_store

    secrets = {}

    def _set(name, value):
        if not value:
            raise ValueError("Secret value cannot be empty.")
        secrets[name] = value

    monkeypatch.setattr(secure_store, "get_secret", lambda name: secrets.get(name))
    monkeypatch.setattr(secure_store, "set_secret", _set)
    monkeypatch.setattr(secure_store, "delete_secret", lambda name: secrets.pop(name, None))
    return secrets


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Point the app at a throwaway encrypted SQLite file for this test only."""
    monkeypatch.setattr(db_connection, "DATABASE_PATH", tmp_path / "test.db")
    # The database is SQLCipher-encrypted; set the derived key (the app's unlock
    # gate does this from the passphrase in production) so connections can open it.
    from database.crypto import derive_key
    db_connection.set_active_key(derive_key("test-passphrase"))
    init_database()
    yield
    db_connection.clear_active_key()


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

    def make_cash(account_number, name):
        # subtype "Cash" matches the real chart-of-accounts convention and is
        # what the close package's receipts & disbursements sheet keys on.
        account = Account(client_id=client_id, account_number=account_number,
                          name=name, type="Asset", subtype="Cash")
        account.save()
        return account.id

    return {
        "cash": make_cash("1000", "Cash"),
        "credit_card": make("2000", "Credit Card Payable", "Liability"),
        "equity": make("3000", "Owner's Equity", "Equity"),
        "revenue": make("4000", "Service Revenue", "Revenue"),
        "expense": make("6000", "Office Expense", "Expense"),
    }


REPO_ROOT = Path(__file__).resolve().parent.parent


def page_path(name: str) -> str:
    """Absolute path to an app page for AppTest.from_file.

    Streamlit changed relative-path resolution (now against the CALLING test
    file, not the cwd), so "pages/X.py" silently became tests/pages/X.py on
    newer versions — 34 failures on CI while older local installs passed.
    Absolute paths behave identically on every version and platform.
    """
    return str(REPO_ROOT / name)


def post_entry(client_id, entry_date, lines, entry_type="Regular", source_reference=None):
    """lines: list of (account_id, debit, credit) tuples."""
    entry = JournalEntry(
        client_id=client_id,
        entry_date=entry_date,
        description="test entry",
        entry_type=entry_type,
        source_reference=source_reference,
        lines=[JournalEntryLine(account_id=a, debit=d, credit=c) for a, d, c in lines],
    )
    entry.save()
    return entry
