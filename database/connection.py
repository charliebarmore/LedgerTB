import os
import threading
from contextlib import contextmanager

import sqlcipher3

from config import DATABASE_PATH
from .crypto import key_pragma
from .schema import create_tables


class DatabaseLocked(RuntimeError):
    """Raised when a connection is requested before the passphrase is set.

    The app's unlock gate (utils/unlock.require_unlock) sets the key before any
    page touches the database, so this surfacing means a code path ran a query
    without going through the gate.
    """


# The active session's derived SQLCipher raw key (hex). Held only in this process
# (set once after the unlock/setup gate derives it from the passphrase) -- never
# written to disk or the OS keychain, so a launch passphrase leaves nothing at
# rest. Every connection is keyed from it with the raw-key fast path. A lock
# guards the swap because Streamlit may run across threads.
_active_key = None
_key_lock = threading.Lock()


def set_active_key(raw_key_hex: str) -> None:
    """Set the derived raw key (hex) used to key every subsequent connection.

    The caller derives this from the passphrase once via crypto.derive_key.
    """
    global _active_key
    with _key_lock:
        _active_key = raw_key_hex


def clear_active_key() -> None:
    """Forget the passphrase (locks the database again)."""
    global _active_key
    with _key_lock:
        _active_key = None


def has_active_key() -> bool:
    return _active_key is not None


def get_connection():
    """Open a SQLCipher connection keyed with the active passphrase.

    Requires set_active_key() to have run first (the unlock gate does this).
    The key PRAGMA is issued before any other statement, as SQLCipher requires.
    """
    if _active_key is None:
        raise DatabaseLocked("Database is locked; unlock with the passphrase first.")
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(DATABASE_PATH.parent, 0o700)
    except OSError:
        pass
    conn = sqlcipher3.connect(DATABASE_PATH, check_same_thread=False)
    # The key must be applied before touching any table, so this is the first
    # statement on the connection.
    conn.execute(f"PRAGMA key = {key_pragma(_active_key)}")
    try:
        os.chmod(DATABASE_PATH, 0o600)
    except OSError:
        pass
    conn.row_factory = sqlcipher3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def open_keyed(path):
    """Open an arbitrary database file with the active passphrase.

    Used by the backup/restore paths, which must read and write SQLCipher files
    (a backup of an encrypted database must itself be encrypted). Requires the
    passphrase to be set, same as get_connection().
    """
    if _active_key is None:
        raise DatabaseLocked("Database is locked; unlock with the passphrase first.")
    conn = sqlcipher3.connect(str(path), check_same_thread=False)
    conn.execute(f"PRAGMA key = {key_pragma(_active_key)}")
    conn.row_factory = sqlcipher3.Row
    return conn


@contextmanager
def get_cursor(commit: bool = False):
    """Yield a cursor on a fresh connection, always closing the connection.

    Guarantees the connection is closed even if the body raises (which would
    otherwise leak it -- e.g. a malformed stored date tripping fromisoformat, or
    a JSON decode error, mid-method). Rolls back on error. Pass ``commit=True``
    for write operations so the transaction is committed on clean exit.

    Usage:
        with get_cursor() as cur:            # read
            cur.execute(...); row = cur.fetchone()
        with get_cursor(commit=True) as cur: # write
            cur.execute(...)
    """
    conn = get_connection()
    try:
        yield conn.cursor()
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database():
    """Initialize the database with tables (requires the passphrase to be set)."""
    conn = get_connection()
    create_tables(conn)
    conn.close()
