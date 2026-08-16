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
_BACKUP_PATTERNS = ("ledgertb-*.db", "probooks-*.db")


class BackupBookMismatch(ValueError):
    """A valid backup belongs to a different book than the one now open."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _integrity(path: Path, key=None) -> bool:
    # Backups are SQLCipher-encrypted, so they must be opened with the active
    # key -- a plaintext sqlite3 open would fail on the encrypted header.
    # A wrong key raises rather than reporting corruption, and callers all want
    # the same answer either way: this file is not readable as it stands.
    try:
        conn = db_connection.open_keyed(path, key)
    except Exception:
        return False
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    except Exception:
        return False
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
    # Which key this backup was written with. Empty for backups predating the
    # marker, which are treated as openable until proven otherwise.
    key_fingerprint: str = ""


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


def _mtime_or_zero(path: Path) -> int:
    # The sort key stats each file, and backup_health runs on every sidebar
    # render — a backup pruned between glob and stat must sort, not crash.
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _backup_paths(directory: Path) -> list[Path]:
    """New LedgerTB backups plus ProBooks-era backups, without duplicates."""
    found = {path for pattern in _BACKUP_PATTERNS for path in directory.glob(pattern)}
    return sorted(found, key=_mtime_or_zero, reverse=True)


def create_backup(
    backup_dir: Optional[Path] = None,
    *,
    reason: str = "manual",
    apply_retention: bool = True,
    target_key: Optional[str] = None,
) -> BackupRecord:
    """Write a verified encrypted copy of the live book.

    ``target_key`` keys the copy with something other than the session's
    current key. Used once: the backup taken immediately before a passphrase
    change is written under the NEW passphrase, so the recovery point it leaves
    opens with the passphrase the user just chose and recorded, rather than one
    they may never have known.
    """
    backup_root = Path(backup_dir or DEFAULT_BACKUP_DIR)
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
        final_db = book_dir / f"ledgertb-{book_id[:12]}-{stamp}.db"
        temp_db = book_dir / f".{final_db.name}.tmp"
        manifest = final_db.with_suffix(".json")
        temp_manifest = book_dir / f".{manifest.name}.tmp"

        # Key the backup target with the same passphrase so the backup file is
        # itself encrypted (SQLCipher's online backup writes encrypted pages
        # when the target connection is keyed).
        target = db_connection.open_keyed(temp_db, target_key)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()

    os.chmod(temp_db, 0o600)
    if not _integrity(temp_db, target_key):
        temp_db.unlink(missing_ok=True)
        raise RuntimeError("Backup failed SQLite integrity verification.")

    created_at = datetime.now(timezone.utc)
    digest = _sha256(temp_db)
    payload = {
        "app_version": APP_VERSION,
        "schema_versions": _schema_versions(temp_db, target_key),
        "created_at": created_at.isoformat(),
        "reason": reason,
        "book_id": book_id,
        "book_name": book_name,
        "database_file": final_db.name,
        "size_bytes": temp_db.stat().st_size,
        "sha256": digest,
        "integrity_ok": True,
        "key_fingerprint": _fingerprint_for(target_key),
    }
    temp_manifest.write_text(json.dumps(payload, indent=2) + "\n")
    os.chmod(temp_manifest, 0o600)
    os.replace(temp_db, final_db)
    os.replace(temp_manifest, manifest)

    if apply_retention:
        prune_backups(backup_root)
    return BackupRecord(
        final_db, manifest, created_at, digest, final_db.stat().st_size, True,
        book_id, book_name, payload["key_fingerprint"],
    )


def _fingerprint_for(key=None) -> str:
    """Marker for the key a backup was written with, or "" if unencrypted."""
    from database.crypto import key_fingerprint

    chosen = key or db_connection.get_active_key()
    return key_fingerprint(chosen) if chosen else ""


def rekey_backups(current_key: str, new_key: str, days: Optional[int] = None,
                  backup_dir: Optional[Path] = None):
    """Re-encrypt this book's backups so they open with the new key.

    Every managed backup by default. A partial re-key was considered and
    rejected: it leaves passphrase tiers the recovery UI cannot open, and one
    slower rotation is easier to live with than an archive where which
    passphrase opens which recovery point depends on its age. ``days`` limits
    it for tests and for a caller that knowingly wants less.

    Nothing is ever deleted here. A backup that cannot be converted is left
    exactly as it was, still openable with the passphrase it already had, and
    reported.

    Returns (converted, [(name, reason), ...]). Each backup is converted on its
    own, so one failure does not stop the rest.

    Residual window, stated rather than hidden: the database file and its
    manifest are replaced one after the other, so a crash between them leaves a
    checksum mismatch. load_record reports that as an interrupted re-key rather
    than as corruption, and the backup can be deleted and remade.
    """
    from datetime import timedelta

    from database.crypto import key_fingerprint, rekey_file

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)) if days else None
    converted, failed = 0, []
    for record in list_backups(backup_dir):
        if cutoff and record.created_at < cutoff:
            continue
        manifest_path = record.database_path.with_suffix(".json")
        try:
            payload = json.loads(manifest_path.read_text())
            if payload.get("key_fingerprint") == key_fingerprint(new_key):
                continue                      # already converted, idempotent
            rekey_file(record.database_path, current_key, new_key)
            payload["sha256"] = _sha256(record.database_path)
            payload["size_bytes"] = record.database_path.stat().st_size
            payload["key_fingerprint"] = key_fingerprint(new_key)
            tmp = manifest_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2) + "\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, manifest_path)
            converted += 1
        except Exception as exc:
            failed.append((record.database_path.name, str(exc)))
    return converted, failed


def opens_with_active_key(record) -> bool:
    """Whether the session's current key is the one this backup was written
    with. Unknown (an older manifest with no fingerprint) counts as yes, since
    there is nothing to contradict it."""
    recorded = getattr(record, "key_fingerprint", "") or ""
    return not recorded or recorded == _fingerprint_for()


def _schema_versions(path: Path, key=None) -> list[str]:
    conn = db_connection.open_keyed(path, key)
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
        raise ValueError(
            "This recovery point does not match its own record of itself, so "
            "its checksum verification failed. It may have been altered, or a "
            "passphrase change may have been interrupted while converting it. "
            "Either way it cannot be restored; delete it and take a fresh backup."
        )
    # A backup written under a previous passphrase cannot be opened with the
    # session's current key, and that must not make it disappear. Listing it is
    # how someone finds out it needs the old passphrase; silently dropping it
    # tells them their recovery points are gone. The checksum above is the
    # anti-tamper control that still applies either way; the identity
    # cross-check below only runs when the file can actually be read.
    openable = _integrity(database_path)
    if openable:
        database_book_id = _database_book_id(database_path)
        if database_book_id != manifest_book_id:
            raise ValueError("Backup manifest identity does not match its database.")
    else:
        database_book_id = manifest_book_id
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
        integrity_ok=openable,
        key_fingerprint=payload.get("key_fingerprint", ""),
        book_id=database_book_id,
        book_name=str(payload.get("book_name") or "Book"),
    )


def list_backups(backup_dir: Optional[Path] = None) -> list[BackupRecord]:
    backup_root = Path(backup_dir or DEFAULT_BACKUP_DIR)
    if not backup_root.exists():
        return []
    book_id = active_book_id()
    book_dir = _book_backup_dir(backup_root, book_id)
    records = []
    for path in _backup_paths(book_dir):
        try:
            manifest = path.with_suffix(".json")
            records.append(_cached_record(
                str(path), path.stat().st_mtime_ns,
                manifest.stat().st_mtime_ns, book_id,
            ))
        except Exception:
            continue
    return sorted(records, key=lambda r: r.created_at, reverse=True)


def restore_backup(database_path: Path, backup_dir: Optional[Path] = None,
                   audit=None) -> Path:
    """Replace the live book with a recovery point, in one visible step.

    The prepared copy is brought fully up to date before it goes live: copied,
    integrity checked, migrated to the current schema, and its own restore event
    written into it. Only then does it replace the live book, atomically.

    Doing the migration and the audit on the copy is what makes the restore and
    its record one transition rather than two. Restoring first and auditing
    afterwards left a window where the book was replaced and nothing recorded
    it, and worse, a backup predating the audit_log rebuild reinstated the older
    schema, so the event could not be written at all.

    ``audit`` is called with an open connection to the prepared copy, before it
    goes live, and is expected to write the restore event through it.
    """
    book_id = active_book_id()
    record = load_record(Path(database_path), expected_book_id=book_id)
    if not opens_with_active_key(record):
        raise ValueError(
            "This recovery point was made under a different passphrase and "
            "cannot be opened with the one this book uses now."
        )
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
    try:
        if not _integrity(temp):
            raise RuntimeError(
                "The recovery point could not be read back after copying it. "
                "The live book has not been touched."
            )

        # Bring the copy to the current schema before anything writes to it. An
        # older backup can predate migrations the running code depends on.
        from database.schema import create_tables

        conn = db_connection.open_keyed(temp)
        try:
            create_tables(conn)
            if audit is not None:
                audit(conn)
                conn.commit()
        finally:
            conn.close()

        from database.crypto import _fsync_path

        _fsync_path(temp)
        os.replace(temp, live)
        _fsync_path(live)
        _fsync_path(live.parent)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
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
    try:
        book_id = active_book_id()
    except Exception:
        # The sidebar calls this on every page; a book whose identity can't be
        # read must surface as "not healthy", never as a crashed page.
        return {
            "healthy": False,
            "reason": "Backup status is unavailable for this book.",
            "latest": None,
        }
    book_dir = _book_backup_dir(backup_root, book_id)
    candidates = _backup_paths(book_dir)
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
    for path in _backup_paths(backup_root):
        try:
            payload = json.loads(path.with_suffix(".json").read_text())
        except Exception:
            continue
        if not payload.get("book_id"):
            count += 1
    return count


def adopt_legacy_backups(backup_dir: Path = DEFAULT_BACKUP_DIR) -> dict:
    """Bring pre-book-identity backups under the currently open book.

    LedgerTB never guesses which book a legacy backup belongs to; adoption is
    the user asserting ownership, and the assertion is checked: a backup is
    adopted only if it verifies against its manifest AND opens intact with
    THIS book's key. Anything that fails is left exactly where it was.
    """
    backup_root = Path(backup_dir)
    result = {"adopted": [], "skipped": []}
    if not backup_root.exists():
        return result
    book_id = active_book_id()
    book_dir = _book_backup_dir(backup_root, book_id)

    for path in reversed(_backup_paths(backup_root)):
        manifest = path.with_suffix(".json")
        try:
            payload = json.loads(manifest.read_text())
        except Exception:
            continue  # not a readable backup manifest; not ours to touch
        if payload.get("book_id"):
            continue  # already book-scoped, just misplaced; leave it alone
        try:
            _adopt_one(path, manifest, payload, book_dir, book_id)
        except Exception:
            result["skipped"].append(path.name)
        else:
            result["adopted"].append(path.name)
    return result


def _adopt_one(path: Path, manifest: Path, payload: dict,
               book_dir: Path, book_id: str) -> None:
    if payload.get("database_file") != path.name:
        raise ValueError("Backup manifest does not match the database file.")
    if _sha256(path) != payload.get("sha256"):
        raise ValueError("Backup checksum verification failed.")
    # The ownership proof: only this book's key opens its own backups.
    if not _integrity(path):
        raise ValueError("Backup database failed SQLite integrity verification.")

    book_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(book_dir, 0o700)
    final_db = book_dir / path.name
    final_manifest = final_db.with_suffix(".json")
    if final_db.exists() or final_manifest.exists():
        raise ValueError("A backup with this name already exists for the book.")

    # Work on a copy so the original survives any failure below.
    temp_db = book_dir / f".{path.name}.adopt.tmp"
    temp_manifest = book_dir / f".{final_manifest.name}.adopt.tmp"
    shutil.copy2(path, temp_db)
    try:
        os.chmod(temp_db, 0o600)
        _stamp_book_id(temp_db, book_id)
        if not _integrity(temp_db):
            raise RuntimeError("Adopted copy failed SQLite integrity verification.")
        payload = dict(payload)
        payload["book_id"] = book_id
        payload["sha256"] = _sha256(temp_db)
        payload["size_bytes"] = temp_db.stat().st_size
        payload["schema_versions"] = _schema_versions(temp_db)
        payload["adopted_at"] = datetime.now(timezone.utc).isoformat()
        temp_manifest.write_text(json.dumps(payload, indent=2) + "\n")
        os.chmod(temp_manifest, 0o600)
        os.replace(temp_db, final_db)
        os.replace(temp_manifest, final_manifest)
    except Exception:
        temp_db.unlink(missing_ok=True)
        temp_manifest.unlink(missing_ok=True)
        raise
    path.unlink(missing_ok=True)
    manifest.unlink(missing_ok=True)


def _stamp_book_id(path: Path, book_id: str) -> None:
    """Write this book's identity into a pre-016 backup, mirroring migration
    016's shape, and record that migration as applied — otherwise restoring
    the backup would re-run 016 into a table-already-exists failure (or worse,
    assign a fresh random identity and orphan every other recovery point)."""
    conn = db_connection.open_keyed(path)
    try:
        try:
            row = conn.execute(
                "SELECT book_id FROM book_identity WHERE id = 1"
            ).fetchone()
        except Exception:
            row = None
            conn.execute("""
                CREATE TABLE book_identity (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    book_id TEXT NOT NULL UNIQUE CHECK (length(book_id) = 32),
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
        if row and str(row[0]) != book_id:
            raise ValueError("This backup already belongs to a different book.")
        if not row:
            conn.execute(
                "INSERT INTO book_identity (id, book_id) VALUES (1, ?)",
                (book_id,),
            )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version) "
            "VALUES ('016_book_identity')"
        )
        conn.commit()
    finally:
        conn.close()
