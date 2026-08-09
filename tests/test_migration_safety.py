"""Guarded removal of unencrypted copies left by legacy-book migration."""

import sqlite3

import pytest

from database import connection as dbconn
from database.crypto import database_state, plaintext_backup_path
from services import migration_safety


def _make_plaintext_sqlite(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE private_data (value TEXT)")
    conn.execute("INSERT INTO private_data VALUES ('client secret')")
    conn.commit()
    conn.close()


def test_removal_verifies_live_encrypted_book_and_deletes_exact_copy(db):
    book = dbconn.DATABASE_PATH
    backup = plaintext_backup_path(book)
    _make_plaintext_sqlite(backup)
    size = backup.stat().st_size

    result = migration_safety.remove_active_plaintext_backup()

    assert result.path == backup
    assert result.size_bytes == size
    assert not backup.exists()
    assert database_state(book) == "encrypted"
    with dbconn.get_cursor() as cursor:
        assert cursor.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_removal_refuses_an_unexpected_file_at_the_reserved_path(db):
    backup = plaintext_backup_path(dbconn.DATABASE_PATH)
    backup.write_text("not a SQLite migration copy", encoding="utf-8")

    with pytest.raises(
        migration_safety.PlaintextBackupRemovalError,
        match="manual inspection",
    ):
        migration_safety.remove_active_plaintext_backup()

    assert backup.read_text(encoding="utf-8") == "not a SQLite migration copy"


def test_removal_refuses_when_live_book_is_not_encrypted(
    db, tmp_path, monkeypatch
):
    live = tmp_path / "legacy.db"
    _make_plaintext_sqlite(live)
    backup = plaintext_backup_path(live)
    _make_plaintext_sqlite(backup)
    monkeypatch.setattr(dbconn, "DATABASE_PATH", live)

    with pytest.raises(
        migration_safety.PlaintextBackupRemovalError,
        match="live book is not encrypted",
    ):
        migration_safety.remove_active_plaintext_backup()

    assert live.exists() and backup.exists()


def test_removal_preserves_copy_when_integrity_check_fails(db, monkeypatch):
    backup = plaintext_backup_path(dbconn.DATABASE_PATH)
    _make_plaintext_sqlite(backup)

    class BadConnection:
        def execute(self, sql):
            return self

        def fetchone(self):
            return ("corrupt",)

        def close(self):
            pass

    monkeypatch.setattr(dbconn, "open_keyed", lambda path: BadConnection())
    with pytest.raises(
        migration_safety.PlaintextBackupRemovalError,
        match="failed its integrity check",
    ):
        migration_safety.remove_active_plaintext_backup()

    assert backup.exists()
