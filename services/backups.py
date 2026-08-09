"""Verified SQLite backup, restore, health, and retention operations."""

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from functools import lru_cache

from config import APP_VERSION, BACKUP_DIR
from database import connection as db_connection

DEFAULT_BACKUP_DIR = BACKUP_DIR
_BOOK_ID = re.compile(r"^[0-9a-f]{32}$")


class BackupBookMismatch(ValueError):
    """A valid backup belongs to a different book than the one now open."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _integrity(path: Path) -> bool:
    # Backups are SQLCipher-encrypted, so they must be opened with the active
    # key -- a plaintext sqlite3 open would fail on the encrypted header.
    conn = db_connection.open_keyed(path)
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
    book_id: str
    book_name: str


def _read_book_id(conn) -> str:
    try:
        row = conn.execute(
            "SELECT book_id FROM book_identity WHERE id = 1"
        ).fetchone()
    except Exception as exc:
        raise ValueError(
            "This backup predates book-specific recovery protection and cannot "
            "be restored automatically."
        ) from exc
    book_id = str(row[0]) if row else ""
    if not _BOOK_ID.fullmatch(book_id):
        raise ValueError("The database has an invalid book identity.")
    return book_id


def active_book_id() -> str:
    """Stable identity stored inside the currently open encrypted book."""
    conn = db_connection.get_connection()
    try:
        return _read_book_id(conn)
    finally:
        conn.close()


def _database_book_id(path: Path) -> str:
    conn = db_connection.open_keyed(path)
    try:
        return _read_book_id(conn)
    finally:
        conn.close()


def _book_backup_dir(backup_dir: Path, book_id: str) -> Path:
    if not _BOOK_ID.fullmatch(book_id):
        raise ValueError("The database has an invalid book identity.")
    return Path(backup_dir) / book_id


def create_backup(
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    *,
    reason: str = "manual",
    apply_retention: bool = True,
) -> BackupRecord:
    backup_root = Path(backup_dir)
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(backup_root, 0o700)

    source = db_connection.get_connection()
    try:
        book_id = _read_book_id(source)
        book_name = Path(db_connection.DATABASE_PATH).stem
        book_dir = _book_backup_dir(backup_root, book_id)
        book_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(book_dir, 0o700)
        stamp = _timestamp()
        final_db = book_dir / f"probooks-{book_id[:12]}-{stamp}.db"
        temp_db = book_dir / f".{final_db.name}.tmp"
        manifest = final_db.with_suffix(".json")
        temp_manifest = book_dir / f".{manifest.name}.tmp"

        # Key the backup target with the same passphrase so the backup file is
        # itself encrypted (SQLCipher's online backup writes encrypted pages
        # when the target connection is keyed).
        target = db_connection.open_keyed(temp_db)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
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
        "book_id": book_id,
        "book_name": book_name,
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
        prune_backups(backup_root)
    return BackupRecord(
        final_db, manifest, created_at, digest, final_db.stat().st_size, True,
        book_id, book_name,
    )


def _schema_versions(path: Path) -> list[str]:
    conn = db_connection.open_keyed(path)
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


def load_record(
    database_path: Path, *, expected_book_id: Optional[str] = None
) -> BackupRecord:
    database_path = Path(database_path)
    manifest = database_path.with_suffix(".json")
    if not database_path.is_file() or not manifest.is_file():
        raise ValueError("Backup database or manifest is missing.")
    payload = json.loads(manifest.read_text())
    if payload.get("database_file") != database_path.name:
        raise ValueError("Backup manifest does not match the database file.")
    manifest_book_id = str(payload.get("book_id") or "")
    if not _BOOK_ID.fullmatch(manifest_book_id):
        raise ValueError(
            "This backup predates book-specific recovery protection and cannot "
            "be restored automatically."
        )
    digest = _sha256(database_path)
    if digest != payload.get("sha256"):
        raise ValueError("Backup checksum verification failed.")
    if not _integrity(database_path):
        raise ValueError("Backup database failed SQLite integrity verification.")
    database_book_id = _database_book_id(database_path)
    if database_book_id != manifest_book_id:
        raise ValueError("Backup manifest identity does not match its database.")
    if expected_book_id and database_book_id != expected_book_id:
        raise BackupBookMismatch(
            "That recovery point belongs to a different book and cannot replace "
            "the book currently open."
        )
    return BackupRecord(
        database_path=database_path,
        manifest_path=manifest,
        created_at=datetime.fromisoformat(payload["created_at"]),
        sha256=digest,
        size_bytes=database_path.stat().st_size,
        integrity_ok=True,
        book_id=database_book_id,
        book_name=str(payload.get("book_name") or "Book"),
    )


def list_backups(backup_dir: Path = DEFAULT_BACKUP_DIR) -> list[BackupRecord]:
    backup_root = Path(backup_dir)
    if not backup_root.exists():
        return []
    book_id = active_book_id()
    book_dir = _book_backup_dir(backup_root, book_id)
    records = []
    for path in book_dir.glob("probooks-*.db"):
        try:
            manifest = path.with_suffix(".json")
            records.append(_cached_record(
                str(path), path.stat().st_mtime_ns,
                manifest.stat().st_mtime_ns, book_id,
            ))
        except Exception:
            continue
    return sorted(records, key=lambda r: r.created_at, reverse=True)


def restore_backup(database_path: Path, backup_dir: Path = DEFAULT_BACKUP_DIR) -> Path:
    book_id = active_book_id()
    record = load_record(Path(database_path), expected_book_id=book_id)
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
    backup_root = Path(backup_dir)
    book_id = active_book_id()
    book_dir = _book_backup_dir(backup_root, book_id)
    candidates = sorted(book_dir.glob("probooks-*.db"), reverse=True)
    if not candidates:
        return {
            "healthy": False,
            "reason": "No verified backup exists for this book.",
            "latest": None,
        }
    path = candidates[0]
    try:
        manifest = path.with_suffix(".json")
        latest = _cached_record(
            str(path), path.stat().st_mtime_ns,
            manifest.stat().st_mtime_ns, book_id,
        )
    except Exception as exc:
        return {
            "healthy": False,
            "reason": f"Latest backup for this book is invalid: {exc}",
            "latest": None,
        }
    age = datetime.now(timezone.utc) - latest.created_at
    healthy = age.total_seconds() <= max_age_hours * 3600
    return {
        "healthy": healthy,
        "reason": "Backup is current." if healthy else "Latest backup is stale.",
        "latest": latest,
        "age_hours": age.total_seconds() / 3600,
    }


@lru_cache(maxsize=32)
def _cached_record(
    path: str, _mtime_ns: int, _manifest_mtime_ns: int, expected_book_id: str
) -> BackupRecord:
    """Verify an unchanged backup once rather than hashing it on every rerun."""
    return load_record(Path(path), expected_book_id=expected_book_id)


def legacy_backup_count(backup_dir: Path = DEFAULT_BACKUP_DIR) -> int:
    """Older root-level backups that cannot safely be assigned to a book."""
    backup_root = Path(backup_dir)
    if not backup_root.exists():
        return 0
    count = 0
    for path in backup_root.glob("probooks-*.db"):
        try:
            payload = json.loads(path.with_suffix(".json").read_text())
        except Exception:
            continue
        if not payload.get("book_id"):
            count += 1
    return count
