"""Database-at-rest encryption: key handling, state detection, migration, gating.

Covers the SQLCipher layer added in database/crypto.py and database/connection.py:
the DB is unreadable without the passphrase, a wrong passphrase is rejected, a
legacy plaintext database migrates in place, and connections refuse to open
while locked.
"""

import sqlite3

import pytest

from database import connection as dbconn
from database.crypto import (
    database_state,
    derive_key,
    encrypt_plaintext_db,
    plaintext_backup_path,
    verify_passphrase,
)
from database.schema import create_tables


PASSPHRASE = "correct horse battery staple"


def _use(tmp_path, monkeypatch, name="enc.db"):
    path = tmp_path / name
    monkeypatch.setattr(dbconn, "DATABASE_PATH", path)
    return path


def test_get_connection_refuses_while_locked(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch)
    dbconn.clear_active_key()
    with pytest.raises(dbconn.DatabaseLocked):
        dbconn.get_connection()


def test_derive_key_is_deterministic_and_passphrase_specific():
    assert derive_key(PASSPHRASE) == derive_key(PASSPHRASE)
    assert derive_key(PASSPHRASE) != derive_key(PASSPHRASE + "!")
    assert len(derive_key(PASSPHRASE)) == 64  # 32 bytes hex


def test_new_database_is_encrypted_and_reopens(tmp_path, monkeypatch):
    path = _use(tmp_path, monkeypatch)
    dbconn.set_active_key(derive_key(PASSPHRASE))
    try:
        dbconn.init_database()
        with dbconn.get_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO clients(name, entity_type, fiscal_year_end_month, is_active)"
                " VALUES ('Secret Co', 'S-Corp', 12, 1)"
            )

        assert database_state(path) == "encrypted"
        raw = path.read_bytes()
        assert raw[:16] != b"SQLite format 3\x00"     # no plaintext SQLite header
        assert b"Secret Co" not in raw                 # payload not in cleartext

        # A plain (unkeyed) sqlite3 open cannot read the encrypted file.
        with pytest.raises(sqlite3.DatabaseError):
            sqlite3.connect(path).execute("SELECT * FROM clients").fetchall()
    finally:
        dbconn.clear_active_key()


def test_wrong_passphrase_is_rejected(tmp_path, monkeypatch):
    path = _use(tmp_path, monkeypatch)
    dbconn.set_active_key(derive_key(PASSPHRASE))
    try:
        dbconn.init_database()
    finally:
        dbconn.clear_active_key()
    assert verify_passphrase(path, PASSPHRASE) is True
    assert verify_passphrase(path, "not the passphrase") is False


def test_plaintext_database_migrates_in_place(tmp_path, monkeypatch):
    path = _use(tmp_path, monkeypatch, name="legacy.db")

    # Build a legacy plaintext database with the real schema + a row.
    conn = sqlite3.connect(path)
    create_tables(conn)
    conn.execute(
        "INSERT INTO clients(name, entity_type, fiscal_year_end_month, is_active)"
        " VALUES ('Legacy LLC', 'S-Corp', 12, 1)"
    )
    conn.commit()
    conn.close()
    assert database_state(path) == "plaintext"

    backup = encrypt_plaintext_db(path, PASSPHRASE)
    assert backup == plaintext_backup_path(path)
    assert database_state(path) == "encrypted"
    assert backup.exists()
    assert backup.read_bytes()[:16] == b"SQLite format 3\x00"  # backup stays plaintext
    assert b"Legacy LLC" not in path.read_bytes()             # migrated file encrypted

    dbconn.set_active_key(derive_key(PASSPHRASE))
    try:
        with dbconn.get_cursor() as cur:
            names = [r[0] for r in cur.execute("SELECT name FROM clients").fetchall()]
        assert names == ["Legacy LLC"]
    finally:
        dbconn.clear_active_key()


def test_migration_refuses_to_overwrite_an_existing_plaintext_copy(
    tmp_path, monkeypatch
):
    path = _use(tmp_path, monkeypatch, name="legacy.db")
    conn = sqlite3.connect(path)
    create_tables(conn)
    conn.close()
    original = path.read_bytes()

    backup = plaintext_backup_path(path)
    backup.write_bytes(b"existing recovery copy")
    with pytest.raises(RuntimeError, match="already exists"):
        encrypt_plaintext_db(path, PASSPHRASE)

    assert path.read_bytes() == original
    assert backup.read_bytes() == b"existing recovery copy"


def test_migration_refuses_a_symlinked_plaintext_source(tmp_path, monkeypatch):
    actual = tmp_path / "actual.db"
    conn = sqlite3.connect(actual)
    create_tables(conn)
    conn.close()
    linked = _use(tmp_path, monkeypatch, name="linked.db")
    try:
        linked.symlink_to(actual)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")

    with pytest.raises(RuntimeError, match="symlinks"):
        encrypt_plaintext_db(linked, PASSPHRASE)

    assert linked.is_symlink()
    assert database_state(actual) == "plaintext"
