import os
import threading
from contextlib import contextmanager

# SQLCipher is the preferred driver (encrypted database at rest). When it isn't
# installed -- it needs the system SQLCipher library, which a first-time
# evaluator may not have -- fall back to the stdlib driver and run UNENCRYPTED.
# The unlock gate (utils/unlock.require_unlock) is what makes this safe: in
# fallback mode it refuses to open an existing encrypted database (plain sqlite3
# would just fail on it) and shows a persistent "encryption is off" warning.
try:
    import sqlcipher3 as _driver

    ENCRYPTION_AVAILABLE = True
except ImportError:
    import sqlite3 as _driver

    ENCRYPTION_AVAILABLE = False

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

# When True, every new connection is pinned read-only (PRAGMA query_only).
# Set by the MCP server before its first query: assistant access is read-only
# by construction, not by convention.
READ_ONLY = False


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


def get_active_key():
    """The active raw key (hex), or None. Used by the opt-in MCP enablement to
    copy the session's key into the OS credential vault — the passphrase itself
    is never stored anywhere."""
    return _active_key


def get_connection():
    """Open a database connection, keyed with the active passphrase when the
    SQLCipher driver is present.

    Encrypted mode requires set_active_key() to have run first (the unlock gate
    does this); the key PRAGMA is issued before any other statement, as
    SQLCipher requires. Fallback mode (stdlib sqlite3) has no passphrase and no
    key requirement.
    """
    if ENCRYPTION_AVAILABLE and _active_key is None:
        raise DatabaseLocked("Database is locked; unlock with the passphrase first.")
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(DATABASE_PATH.parent, 0o700)
    except OSError:
        pass
    conn = _driver.connect(DATABASE_PATH, check_same_thread=False)
    if ENCRYPTION_AVAILABLE:
        # The key must be applied before touching any table, so this is the
        # first statement on the connection.
        conn.execute(f"PRAGMA key = {key_pragma(_active_key)}")
    try:
        os.chmod(DATABASE_PATH, 0o600)
    except OSError:
        pass
    conn.row_factory = _driver.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if READ_ONLY:
        conn.execute("PRAGMA query_only = ON")
    return conn


def open_keyed(path):
    """Open an arbitrary database file with the active passphrase.

    Used by the backup/restore paths, which must read and write SQLCipher files
    (a backup of an encrypted database must itself be encrypted). Requires the
    passphrase to be set, same as get_connection(). In fallback mode there is no
    key, so backups are plaintext like the database itself.
    """
    if ENCRYPTION_AVAILABLE and _active_key is None:
        raise DatabaseLocked("Database is locked; unlock with the passphrase first.")
    conn = _driver.connect(str(path), check_same_thread=False)
    if ENCRYPTION_AVAILABLE:
        conn.execute(f"PRAGMA key = {key_pragma(_active_key)}")
    conn.row_factory = _driver.Row
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
