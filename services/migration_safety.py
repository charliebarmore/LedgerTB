"""Detection and narrowly scoped removal of legacy plaintext migration copies."""

from dataclasses import dataclass
from pathlib import Path

from database import connection as dbconn
from database.crypto import (
    database_state,
    is_plaintext_sqlite,
    plaintext_backup_path,
)


class PlaintextBackupRemovalError(RuntimeError):
    """The migration copy could not be safely identified or removed."""


@dataclass(frozen=True)
class PlaintextBackupRemoval:
    path: Path
    size_bytes: int


def active_plaintext_backup() -> Path:
    return plaintext_backup_path(Path(dbconn.DATABASE_PATH))


def remove_active_plaintext_backup() -> PlaintextBackupRemoval:
    """Remove only the active book's known plaintext migration copy.

    The live book must be encrypted, unlocked with the current key, and pass a
    full integrity check. The adjacent backup must be an ordinary plaintext
    SQLite file, not a symlink or an unrelated file with the reserved suffix.
    """
    book = Path(dbconn.DATABASE_PATH)
    backup = plaintext_backup_path(book)
    if not backup.exists() and not backup.is_symlink():
        raise PlaintextBackupRemovalError("No plaintext migration copy exists.")
    if not is_plaintext_sqlite(backup):
        raise PlaintextBackupRemovalError(
            "The adjacent migration-copy path is not an ordinary plaintext "
            "SQLite file. LedgerTB left it untouched for manual inspection."
        )
    if database_state(book) != "encrypted":
        raise PlaintextBackupRemovalError(
            "The live book is not encrypted; the plaintext copy was not removed."
        )

    try:
        conn = dbconn.open_keyed(book)
        try:
            intact = conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            conn.close()
    except Exception as exc:
        raise PlaintextBackupRemovalError(
            "The encrypted live book could not be verified; the plaintext "
            "copy was not removed."
        ) from exc
    if not intact:
        raise PlaintextBackupRemovalError(
            "The encrypted live book failed its integrity check; the plaintext "
            "copy was not removed."
        )

    size_bytes = backup.stat().st_size
    backup.unlink()
    return PlaintextBackupRemoval(path=backup, size_bytes=size_bytes)
