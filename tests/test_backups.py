import json
from pathlib import Path

import pytest

from models.client import Client
from services.backups import (
    backup_health,
    create_backup,
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
