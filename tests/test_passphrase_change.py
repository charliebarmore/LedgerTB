"""Changing a book's passphrase.

The risk here is not a wrong number on a page, it is an unreadable book, so
most of these tests are about what survives a failure.
"""

import sqlite3
from pathlib import Path

import pytest

from database import connection as dbconn
from database.crypto import (
    change_passphrase,
    database_state,
    derive_key,
    verify_passphrase,
)

OLD = "old-passphrase-here"
NEW = "new-passphrase-here"


@pytest.fixture
def book(tmp_path, monkeypatch):
    """An encrypted book with something in it, opened under OLD."""
    path = tmp_path / "rotate.ledgertb"
    monkeypatch.setattr(dbconn, "DATABASE_PATH", path)
    dbconn.set_active_key(derive_key(OLD))

    from database import init_database
    init_database()

    from models.client import Client
    Client(name="Kettle Ridge Cabinetry", entity_type="S-Corp",
           fiscal_year_end_month=12).save(seed_accounts=True)

    yield path
    dbconn.clear_active_key()


# --- the happy path ---------------------------------------------------------

def test_the_new_passphrase_opens_the_book(book):
    change_passphrase(book, derive_key(OLD), NEW)

    assert verify_passphrase(book, NEW) is True


def test_the_old_passphrase_stops_working(book):
    change_passphrase(book, derive_key(OLD), NEW)

    assert verify_passphrase(book, OLD) is False


def test_the_data_survives(book):
    from models.client import Client
    from models.account import Account

    before = [c.name for c in Client.get_all()]
    accounts_before = Account.count(Client.get_all()[0].id)

    new_key = change_passphrase(book, derive_key(OLD), NEW)
    dbconn.set_active_key(new_key)

    assert [c.name for c in Client.get_all()] == before
    assert Account.count(Client.get_all()[0].id) == accounts_before
    assert accounts_before > 0


def test_the_book_is_still_encrypted_afterwards(book):
    change_passphrase(book, derive_key(OLD), NEW)

    assert database_state(book) == "encrypted"
    # Belt and braces: a plain sqlite3 driver must not be able to read it.
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(str(book)).execute("SELECT count(*) FROM sqlite_master")


def test_no_copy_under_the_old_passphrase_is_left_behind(book):
    """Rotating after a passphrase leaks is pointless if the old file stays."""
    change_passphrase(book, derive_key(OLD), NEW)

    leftovers = [p.name for p in Path(book).parent.iterdir()
                 if p.name != Path(book).name and "rekey" in p.name]
    assert leftovers == []


def test_it_returns_the_new_key(book):
    assert change_passphrase(book, derive_key(OLD), NEW) == derive_key(NEW)


# --- refusals ---------------------------------------------------------------

def test_a_wrong_current_key_changes_nothing(book):
    with pytest.raises(Exception):
        change_passphrase(book, derive_key("not-the-passphrase"), NEW)

    assert verify_passphrase(book, OLD) is True
    assert verify_passphrase(book, NEW) is False


def test_reusing_the_same_passphrase_is_refused(book):
    with pytest.raises(ValueError, match="same as the current one"):
        change_passphrase(book, derive_key(OLD), OLD)

    assert verify_passphrase(book, OLD) is True


def test_an_empty_new_passphrase_is_refused(book):
    with pytest.raises(ValueError, match="cannot be empty"):
        change_passphrase(book, derive_key(OLD), "")


def test_a_book_that_is_not_encrypted_has_nothing_to_rotate(tmp_path):
    plain = tmp_path / "plain.db"
    sqlite3.connect(str(plain)).execute("CREATE TABLE t (id INTEGER)")

    with pytest.raises(RuntimeError, match="Only an encrypted book"):
        change_passphrase(plain, derive_key(OLD), NEW)


# --- surviving a failure ----------------------------------------------------

def test_a_failure_partway_leaves_the_book_openable(book, monkeypatch):
    """If the swap explodes, the old book must still be there and still open.

    An interrupted rotation that leaves nothing readable is the exact disaster
    this feature exists to prevent.
    """
    import database.crypto as crypto

    real_replace = Path.replace
    calls = {"n": 0}

    def exploding_replace(self, target):
        calls["n"] += 1
        if calls["n"] == 2:      # the temp file moving into place
            raise OSError("simulated failure mid-swap")
        return real_replace(self, target)

    # A scoped context, not monkeypatch.undo(): undo() unwinds every patch this
    # test's monkeypatch has made, including the book fixture's DATABASE_PATH,
    # which sends the assertions below at the real book instead of this one.
    with monkeypatch.context() as patched:
        patched.setattr(Path, "replace", exploding_replace)
        with pytest.raises(OSError):
            crypto.change_passphrase(book, derive_key(OLD), NEW)

    assert book.exists()
    assert verify_passphrase(book, OLD) is True

    from models.client import Client
    dbconn.set_active_key(derive_key(OLD))
    assert [c.name for c in Client.get_all()] == ["Kettle Ridge Cabinetry"]


def test_no_temp_file_is_left_behind_after_a_failure(book, monkeypatch):
    import database.crypto as crypto

    def refuse(self, target):
        raise OSError("simulated failure")

    with monkeypatch.context() as patched:
        patched.setattr(Path, "replace", refuse)
        with pytest.raises(OSError):
            crypto.change_passphrase(book, derive_key(OLD), NEW)

    assert not (book.parent / (book.name + ".rekey.tmp")).exists()
    assert verify_passphrase(book, OLD) is True


# --- the app-level wrapper --------------------------------------------------

def test_the_wrapper_swaps_the_active_key(book):
    from utils.unlock import change_book_passphrase
    from models.client import Client

    change_book_passphrase(NEW)

    assert dbconn.get_active_key() == derive_key(NEW)
    # The open session keeps working, without re-unlocking.
    assert [c.name for c in Client.get_all()] == ["Kettle Ridge Cabinetry"]


def test_the_wrapper_updates_a_remembered_key(book):
    """Otherwise the next launch holds a key that no longer opens the book."""
    from utils import secure_store
    from utils.unlock import change_book_passphrase, saved_key_name, try_saved_key

    secure_store.set_secret(saved_key_name(book), derive_key(OLD))
    change_book_passphrase(NEW)

    assert secure_store.get_secret(saved_key_name(book)) == derive_key(NEW)

    dbconn.clear_active_key()
    assert try_saved_key() is True


def test_the_wrapper_leaves_an_unremembered_book_unremembered(book):
    """Rotating must not start saving a key nobody asked it to save."""
    from utils import secure_store
    from utils.unlock import change_book_passphrase, saved_key_name

    change_book_passphrase(NEW)

    assert secure_store.get_secret(saved_key_name(book)) is None


def test_the_wrapper_enforces_the_minimum_length(book):
    from utils.unlock import change_book_passphrase

    with pytest.raises(ValueError, match="at least"):
        change_book_passphrase("short")

    assert verify_passphrase(book, OLD) is True


def test_the_wrapper_refuses_when_the_book_is_locked(book):
    from utils.unlock import change_book_passphrase

    dbconn.clear_active_key()
    with pytest.raises(RuntimeError, match="must be unlocked"):
        change_book_passphrase(NEW)


# --- the audit action -------------------------------------------------------

def test_the_rekey_action_can_actually_be_written(book):
    """AUDIT_ACTIONS and the audit_log CHECK have to move together; migration
    021 is what makes this write something other than an IntegrityError."""
    from models.audit_log import AuditLog
    from models.client import Client

    client = Client.get_all()[0]
    AuditLog.log_event(client.id, "REKEY", "book_passphrase_changed",
                       {"book": str(book)})

    events = AuditLog.get_history("book_passphrase_changed", 0)
    assert events and events[0].action == "REKEY"
