"""Verified SQLite backup, restore, health, and retention operations."""

import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from functools import lru_cache

from config import APP_VERSION, BACKUP_DIR
from database import connection as db_connection

DEFAULT_BACKUP_DIR = BACKUP_DIR


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _integrity(path: Path) -> bool:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


@dataclass
class BackupRecord:
    database_path: Path
    manifest_path: Path
    created_at: datetime
    sha256: str
    size_bytes: int
    integrity_ok: bool


def create_backup(
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    *,
    reason: str = "manual",
    apply_retention: bool = True,
) -> BackupRecord:
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(backup_dir, 0o700)
    stamp = _timestamp()
    final_db = backup_dir / f"probooks-{stamp}.db"
    temp_db = backup_dir / f".{final_db.name}.tmp"
    manifest = final_db.with_suffix(".json")
    temp_manifest = backup_dir / f".{manifest.name}.tmp"

    source = db_connection.get_connection()
    target = sqlite3.connect(temp_db)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    os.chmod(temp_db, 0o600)
    if not _integrity(temp_db):
        temp_db.unlink(missing_ok=True)
        raise RuntimeError("Backup failed SQLite integrity verification.")

    created_at = datetime.now(timezone.utc)
    digest = _sha256(temp_db)
    payload = {
        "app_version": APP_VERSION,
        "schema_versions": _schema_versions(temp_db),
        "created_at": created_at.isoformat(),
        "reason": reason,
        "database_file": final_db.name,
        "size_bytes": temp_db.stat().st_size,
        "sha256": digest,
        "integrity_ok": True,
    }
    temp_manifest.write_text(json.dumps(payload, indent=2) + "\n")
    os.chmod(temp_manifest, 0o600)
    os.replace(temp_db, final_db)
    os.replace(temp_manifest, manifest)

    if apply_retention:
        prune_backups(backup_dir)
    return BackupRecord(
        final_db, manifest, created_at, digest, final_db.stat().st_size, True
    )


def _schema_versions(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        found = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        if not found:
            return []
        return [r[0] for r in conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()]
    finally:
        conn.close()


def load_record(database_path: Path) -> BackupRecord:
    database_path = Path(database_path)
    manifest = database_path.with_suffix(".json")
    if not database_path.is_file() or not manifest.is_file():
        raise ValueError("Backup database or manifest is missing.")
    payload = json.loads(manifest.read_text())
    if payload.get("database_file") != database_path.name:
        raise ValueError("Backup manifest does not match the database file.")
    digest = _sha256(database_path)
    if digest != payload.get("sha256"):
        raise ValueError("Backup checksum verification failed.")
    if not _integrity(database_path):
        raise ValueError("Backup database failed SQLite integrity verification.")
    return BackupRecord(
        database_path=database_path,
        manifest_path=manifest,
        created_at=datetime.fromisoformat(payload["created_at"]),
        sha256=digest,
        size_bytes=database_path.stat().st_size,
        integrity_ok=True,
    )


def list_backups(backup_dir: Path = DEFAULT_BACKUP_DIR) -> list[BackupRecord]:
    if not backup_dir.exists():
        return []
    records = []
    for path in backup_dir.glob("probooks-*.db"):
        try:
            records.append(load_record(path))
        except Exception:
            continue
    return sorted(records, key=lambda r: r.created_at, reverse=True)


def restore_backup(database_path: Path, backup_dir: Path = DEFAULT_BACKUP_DIR) -> Path:
    record = load_record(Path(database_path))
    # A verified pre-restore snapshot gives recovery even if replacement is
    # interrupted after this point.
    pre_restore = create_backup(
        backup_dir, reason="pre_restore", apply_retention=False
    ).database_path
    live = Path(db_connection.DATABASE_PATH)
    live.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = live.with_name(f".{live.name}.restore.tmp")
    shutil.copy2(record.database_path, temp)
    os.chmod(temp, 0o600)
    if not _integrity(temp):
        temp.unlink(missing_ok=True)
        raise RuntimeError("Restored copy failed SQLite integrity verification.")
    os.replace(temp, live)
    return pre_restore


def prune_backups(
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    *,
    daily: int = 30,
    weekly: int = 12,
    monthly: int = 7,
) -> None:
    records = list_backups(backup_dir)
    keep: set[Path] = set()
    keep.update(r.database_path for r in records[:daily])

    weeks, months = set(), set()
    for record in records:
        iso = record.created_at.isocalendar()
        week = (iso.year, iso.week)
        month = (record.created_at.year, record.created_at.month)
        if len(weeks) < weekly and week not in weeks:
            weeks.add(week)
            keep.add(record.database_path)
        if len(months) < monthly and month not in months:
            months.add(month)
            keep.add(record.database_path)

    for record in records:
        if record.database_path not in keep:
            record.database_path.unlink(missing_ok=True)
            record.manifest_path.unlink(missing_ok=True)


def backup_health(
    backup_dir: Path = DEFAULT_BACKUP_DIR, *, max_age_hours: int = 24
) -> dict:
    backup_dir = Path(backup_dir)
    candidates = sorted(backup_dir.glob("probooks-*.db"), reverse=True) if backup_dir.exists() else []
    if not candidates:
        return {"healthy": False, "reason": "No verified backup exists.", "latest": None}
    path = candidates[0]
    try:
        latest = _cached_record(str(path), path.stat().st_mtime_ns)
    except Exception as exc:
        return {"healthy": False, "reason": f"Latest backup is invalid: {exc}", "latest": None}
    age = datetime.now(timezone.utc) - latest.created_at
    healthy = age.total_seconds() <= max_age_hours * 3600
    return {
        "healthy": healthy,
        "reason": "Backup is current." if healthy else "Latest backup is stale.",
        "latest": latest,
        "age_hours": age.total_seconds() / 3600,
    }


@lru_cache(maxsize=8)
def _cached_record(path: str, _mtime_ns: int) -> BackupRecord:
    """Verify an unchanged backup once rather than hashing it on every rerun."""
    return load_record(Path(path))
