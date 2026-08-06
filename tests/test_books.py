"""Firm mode: book-file registry, the in-use lock protocol, and book switching."""
import json
import os

import pytest

from database import connection as dbconn
from utils import book_lock, books


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(books, "SETTINGS_PATH", tmp_path / "books.json")
    monkeypatch.setattr(books, "DEFAULT_BOOK", tmp_path / "default.db")
    monkeypatch.delenv("PROBOOKS_DB_PATH", raising=False)
    return tmp_path


def test_registry_round_trip_and_recents(settings, tmp_path):
    assert books.active_book() == tmp_path / "default.db"

    books.set_active_book(tmp_path / "SmithCo.db")
    books.set_active_book(tmp_path / "JonesLLC.db")
    books.set_active_book(tmp_path / "SmithCo.db")  # reopen: moves to front

    assert books.active_book() == tmp_path / "SmithCo.db"
    recents = [p.name for p in books.recent_books()]
    assert recents[0] == "default.db"  # default is always offered
    assert recents.index("SmithCo.db") < recents.index("JonesLLC.db")
    assert recents.count("SmithCo.db") == 1  # deduped

    # The env override (dev/tests) beats the saved choice.
    os.environ["PROBOOKS_DB_PATH"] = str(tmp_path / "env.db")
    try:
        assert books.active_book() == tmp_path / "default.db"
    finally:
        del os.environ["PROBOOKS_DB_PATH"]


def test_local_book_detection_is_conservative(settings, tmp_path, monkeypatch):
    managed = tmp_path / "managed"
    monkeypatch.setattr(books, "USER_DATA_DIR", managed)
    monkeypatch.setattr(books, "DEFAULT_BOOK", managed / "probooks.db")

    assert books.is_local_book(managed / "probooks.db")
    assert books.is_local_book(managed / "client-books" / "Smith.db")
    assert not books.is_local_book(tmp_path / "shared" / "Smith.db")


def test_lock_acquire_conflict_takeover_release(settings, tmp_path):
    book = tmp_path / "shared.db"

    assert book_lock.acquire(book)["acquired"] is True
    holder = book_lock.read_lock(book)
    assert holder["pid"] == os.getpid()
    # Re-acquiring our own lock is fine (same process reopening).
    assert book_lock.acquire(book)["acquired"] is True

    # Someone on another machine holds it: refused, holder reported.
    other = {"user": "colleague", "host": "OFFICE-PC", "pid": 1234,
             "opened_at": "2026-08-04T15:00:00+00:00"}
    book_lock.lock_path(book).write_text(json.dumps(other))
    result = book_lock.acquire(book)
    assert result["acquired"] is False
    assert "colleague on OFFICE-PC" in book_lock.describe(result["holder"])

    # release() never removes someone else's lock…
    book_lock.release(book)
    assert book_lock.read_lock(book)["user"] == "colleague"
    # …but a deliberate takeover replaces it, and release then clears ours.
    assert book_lock.takeover(book)["acquired"] is True
    book_lock.release(book)
    assert book_lock.read_lock(book) is None


def test_stale_lock_from_this_machine_is_reclaimed(settings, tmp_path):
    import getpass
    import socket

    book = tmp_path / "crashed.db"
    stale = {"user": getpass.getuser(), "host": socket.gethostname(),
             "pid": 99999999, "opened_at": "2026-08-04T09:00:00+00:00"}
    book_lock.lock_path(book).write_text(json.dumps(stale))

    result = book_lock.acquire(book)
    assert result["acquired"] is True
    assert book_lock.read_lock(book)["pid"] == os.getpid()


def test_switching_books_isolates_data(client_id, accounts, tmp_path):
    from database import init_database
    from models.client import Client

    original = dbconn.DATABASE_PATH
    assert any(c.id == client_id for c in Client.get_all())

    try:
        dbconn.DATABASE_PATH = tmp_path / "book-b.db"
        init_database()
        assert Client.get_all() == []  # a fresh book knows nothing of book A
        Client(name="Book B Client").save(seed_accounts=False)
        assert len(Client.get_all()) == 1
    finally:
        dbconn.DATABASE_PATH = original

    names = [c.name for c in Client.get_all()]
    assert "Book B Client" not in names
    assert any(c.id == client_id for c in Client.get_all())


def test_remembered_key_round_trip_and_stale_cleanup(client_id, monkeypatch):
    """'Remember on this Mac': a saved key unlocks without a prompt; a key
    that no longer opens the book is dropped from the vault."""
    from utils import unlock
    from utils.secure_store import get_secret, set_secret

    name = unlock.saved_key_name(dbconn.DATABASE_PATH)
    real_key = dbconn.get_active_key()
    assert real_key

    set_secret(name, real_key)
    dbconn.clear_active_key()
    assert unlock.try_saved_key() is True
    assert dbconn.has_active_key()

    # Wrong key (passphrase changed): refused AND forgotten.
    set_secret(name, "00" * 32)
    dbconn.clear_active_key()
    assert unlock.try_saved_key() is False
    assert not dbconn.has_active_key()
    assert get_secret(name) is None

    # Nothing saved: quietly declines.
    assert unlock.try_saved_key() is False
    dbconn.set_active_key(real_key)  # restore for teardown
