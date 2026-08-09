import json
from pathlib import Path
import shutil

import pytest

from models.client import Client
from services.backups import (
    BackupBookMismatch,
    active_book_id,
    backup_health,
    create_backup,
    legacy_backup_count,
    list_backups,
    prune_backups,
    restore_backup,
)


def test_backup_is_verified_and_restore_replaces_live_database(db, tmp_path):
    Client(name="Before Backup").save(seed_accounts=False)
    backup_dir = tmp_path / "backups"
    record = create_backup(backup_dir)

    assert record.database_path.exists()
    assert record.manifest_path.exists()
    assert backup_health(backup_dir)["healthy"]

    Client(name="After Backup").save(seed_accounts=False)
    assert {c.name for c in Client.get_all()} == {"Before Backup", "After Backup"}

    safety_copy = restore_backup(record.database_path, backup_dir)
    assert safety_copy.exists()
    assert {c.name for c in Client.get_all()} == {"Before Backup"}


def test_tampered_backup_is_rejected(db, tmp_path):
    Client(name="Original").save(seed_accounts=False)
    record = create_backup(tmp_path / "backups")
    with record.database_path.open("ab") as handle:
        handle.write(b"tampered")

    with pytest.raises(ValueError, match="checksum"):
        restore_backup(record.database_path, tmp_path / "backups")
    health = backup_health(tmp_path / "backups")
    assert health["healthy"] is False
    assert "invalid" in health["reason"]


def test_retention_removes_old_unselected_backups(db, tmp_path):
    backup_dir = tmp_path / "backups"
    for i in range(3):
        Client(name=f"Client {i}").save(seed_accounts=False)
        create_backup(backup_dir, apply_retention=False)

    prune_backups(backup_dir, daily=1, weekly=0, monthly=0)
    assert len(list_backups(backup_dir)) == 1


def test_manifest_contains_release_and_integrity_metadata(db, tmp_path):
    record = create_backup(tmp_path / "backups")
    payload = json.loads(record.manifest_path.read_text())
    assert payload["integrity_ok"] is True
    assert payload["sha256"] == record.sha256
    assert payload["schema_versions"]
    assert payload["book_id"] == active_book_id() == record.book_id
    assert record.database_path.parent.name == record.book_id


def test_backups_and_restore_are_scoped_to_the_active_book(db, tmp_path):
    from database import init_database
    from database import connection as dbconn

    backup_dir = tmp_path / "backups"
    original_path = dbconn.DATABASE_PATH
    Client(name="Book A Client").save(seed_accounts=False)
    book_a_id = active_book_id()
    book_a_backup = create_backup(backup_dir)

    try:
        dbconn.DATABASE_PATH = tmp_path / "book-b.db"
        init_database()
        Client(name="Book B Client").save(seed_accounts=False)
        book_b_id = active_book_id()
        book_b_backup = create_backup(backup_dir)

        assert book_b_id != book_a_id
        assert [r.database_path for r in list_backups(backup_dir)] == [
            book_b_backup.database_path
        ]
        with pytest.raises(BackupBookMismatch, match="different book"):
            restore_backup(book_a_backup.database_path, backup_dir)
        assert [c.name for c in Client.get_all()] == ["Book B Client"]
        # A rejected cross-book restore must not create a pre-restore snapshot.
        assert [r.database_path for r in list_backups(backup_dir)] == [
            book_b_backup.database_path
        ]
    finally:
        dbconn.DATABASE_PATH = original_path

    assert active_book_id() == book_a_id
    assert [r.database_path for r in list_backups(backup_dir)] == [
        book_a_backup.database_path
    ]


def test_manifest_cannot_lie_about_the_backup_book(db, tmp_path):
    backup_dir = tmp_path / "backups"
    record = create_backup(backup_dir)
    payload = json.loads(record.manifest_path.read_text())
    payload["book_id"] = "0" * 32
    record.manifest_path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="identity does not match"):
        restore_backup(record.database_path, backup_dir)


def test_legacy_unscoped_backups_are_reported_but_not_offered(db, tmp_path):
    backup_dir = tmp_path / "backups"
    record = create_backup(backup_dir)
    legacy_database = backup_dir / record.database_path.name
    legacy_manifest = legacy_database.with_suffix(".json")
    shutil.move(record.database_path, legacy_database)
    shutil.move(record.manifest_path, legacy_manifest)
    payload = json.loads(legacy_manifest.read_text())
    payload.pop("book_id")
    legacy_manifest.write_text(json.dumps(payload))

    assert legacy_backup_count(backup_dir) == 1
    assert list_backups(backup_dir) == []


def _fabricate_legacy_backup(record, backup_root):
    """Recreate what v1.0.0 wrote: a root-level backup with no book identity
    inside and no book_id in its manifest."""
    import hashlib

    from database import connection as dbconn

    legacy_db = backup_root / "probooks-20260701T000000000000Z.db"
    shutil.copy2(record.database_path, legacy_db)
    conn = dbconn.open_keyed(legacy_db)
    try:
        conn.execute("DROP TABLE book_identity")
        conn.execute("DELETE FROM schema_migrations WHERE version LIKE '016%'")
        conn.commit()
    finally:
        conn.close()
    payload = json.loads(record.manifest_path.read_text())
    del payload["book_id"]
    payload["database_file"] = legacy_db.name
    payload["sha256"] = hashlib.sha256(legacy_db.read_bytes()).hexdigest()
    payload["size_bytes"] = legacy_db.stat().st_size
    legacy_db.with_suffix(".json").write_text(json.dumps(payload))
    return legacy_db


def test_adopted_legacy_backup_becomes_restorable(db, tmp_path):
    """The v1.0.0 upgrade regression: pre-book-identity backups were invisible
    and unrestorable. Adoption must make one a first-class recovery point —
    listed, identity-stamped, and restorable without re-running migration 016."""
    from services.backups import adopt_legacy_backups

    Client(name="Legacy Era").save(seed_accounts=False)
    backup_root = tmp_path / "backups"
    record = create_backup(backup_root)
    legacy_db = _fabricate_legacy_backup(record, backup_root)

    assert legacy_backup_count(backup_root) == 1
    assert all(r.database_path.name != legacy_db.name
               for r in list_backups(backup_root))

    result = adopt_legacy_backups(backup_root)
    assert result["adopted"] == [legacy_db.name]
    assert result["skipped"] == []
    assert not legacy_db.exists()  # moved, not copied
    assert legacy_backup_count(backup_root) == 0

    adopted = next(r for r in list_backups(backup_root)
                   if r.database_path.name == legacy_db.name)
    assert adopted.book_id == active_book_id()

    # Restoring it must keep the stamped identity: migration 016 is recorded
    # as applied, so init neither collides nor assigns a fresh random id
    # (which would orphan every other recovery point).
    book_id = active_book_id()
    restore_backup(adopted.database_path, backup_root)
    from database import init_database
    init_database()
    assert active_book_id() == book_id
    assert any(c.name == "Legacy Era" for c in Client.get_all())


def test_adoption_refuses_backups_it_cannot_verify(db, tmp_path):
    """A legacy backup that does not open with this book's key is someone
    else's book — it must be skipped and left exactly where it was."""
    import hashlib
    import sqlite3

    from services.backups import adopt_legacy_backups

    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    foreign = backup_root / "probooks-20260601T000000000000Z.db"
    plain = sqlite3.connect(foreign)  # unreadable under our key, like a
    plain.execute("CREATE TABLE t (x)")  # backup keyed by another passphrase
    plain.commit()
    plain.close()
    foreign.with_suffix(".json").write_text(json.dumps({
        "database_file": foreign.name,
        "sha256": hashlib.sha256(foreign.read_bytes()).hexdigest(),
        "created_at": "2026-06-01T00:00:00+00:00",
    }))

    result = adopt_legacy_backups(backup_root)
    assert result["adopted"] == []
    assert result["skipped"] == [foreign.name]
    assert foreign.exists()
    assert legacy_backup_count(backup_root) == 1


def test_backup_health_degrades_instead_of_raising(db, tmp_path, monkeypatch):
    """backup_health runs in the sidebar on every page; a book whose identity
    can't be read must report unhealthy, not crash the app."""
    import services.backups as backups_mod

    def broken_identity():
        raise ValueError("This backup predates book-specific recovery protection")

    monkeypatch.setattr(backups_mod, "active_book_id", broken_identity)
    health = backup_health(tmp_path / "backups")
    assert health["healthy"] is False
    assert health["latest"] is None
    assert "unavailable" in health["reason"]
