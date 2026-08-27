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
# Used for read-only book sessions (firm-mode lock fallback): nothing writes.
READ_ONLY = False

# Assistant access level (the MCP server's mode). When set, every new
# connection gets a SQLite authorizer scoped to the level — the engine itself,
# not tool design, enforces the ceiling the user chose on Data Safety:
#   "read"    — SELECT only
#   "propose" — + INSERT into the proposal inboxes (drafts, staged imports,
#               Close Map explanations, client branding)
#   "post"    — + INSERT into the ledger tables: APPEND-ONLY. At no level,
#               ever, can an assistant connection UPDATE or DELETE ledger
#               history; corrections happen the accounting way, with new
#               visible entries.
ASSISTANT_ACCESS_LEVEL = None
ASSISTANT_ACCESS_LEVELS = ("read", "propose", "post")

_AUTH_OK = getattr(_driver, "SQLITE_OK", 0)
_AUTH_DENY = getattr(_driver, "SQLITE_DENY", 1)
_AUTH_ALLOWED_ACTIONS = {
    getattr(_driver, name, default)
    for name, default in (
        ("SQLITE_SELECT", 21), ("SQLITE_READ", 20), ("SQLITE_FUNCTION", 31),
        ("SQLITE_TRANSACTION", 22), ("SQLITE_SAVEPOINT", 32),
    )
}
_AUTH_INSERT = getattr(_driver, "SQLITE_INSERT", 18)
# INSERT surface per level (cumulative). audit_log rides along from "propose"
# up so every assistant write is recorded. Nothing is UPDATE- or DELETE-able
# at any assistant level; draft resolution belongs to the human app process.
_ASSISTANT_INSERT_TABLES = {
    # audit_log at every level: even a read-level assistant's actions that
    # matter (file exports) get recorded, and the log is append-only anyway.
    "read": frozenset({"audit_log"}),
    # clients/accounts/fiscal_periods at propose+: an assistant may scaffold a
    # new client, its chart, and its period calendar (setup, not ledger); it
    # still cannot alter any of them later (no UPDATE/DELETE at any level).
    "propose": frozenset({"draft_entries", "imported_transactions", "audit_log",
                          "clients", "accounts", "fiscal_periods",
                          "close_review_proposals",
                          "client_branding_proposals"}),
    "post": frozenset({"draft_entries", "imported_transactions", "audit_log",
                       "clients", "accounts", "fiscal_periods",
                       "close_review_proposals",
                       "client_branding_proposals",
                       "journal_entries", "journal_entry_lines"}),
}


def _assistant_authorizer(action, arg1, arg2, dbname, source):
    level = ASSISTANT_ACCESS_LEVEL or "read"
    if action in _AUTH_ALLOWED_ACTIONS:
        return _AUTH_OK
    if action == _AUTH_INSERT and arg1 in _ASSISTANT_INSERT_TABLES[level]:
        return _AUTH_OK
    return _AUTH_DENY


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


def _tighten_permissions(path) -> None:
    """Keep a locally managed book private to its owner.

    Deliberately skipped for books on a shared drive. Firm mode exists so
    colleagues can open the same folder, and 0700 on it locks them out — the
    opposite of the point. On SMB the chmod usually raises and is swallowed,
    so the protection was never really in force there anyway; the safety
    checklist reports shared books honestly instead of pretending otherwise.
    """
    from utils.books import is_local_book

    try:
        if not is_local_book(path):
            return
        os.chmod(path.parent, 0o700)
        if path.exists():
            os.chmod(path, 0o600)
    except Exception:
        pass


def writes_permitted() -> bool:
    """Whether a connection opened now would be writable.

    Read-only book sessions cannot write, and neither can an assistant outside
    a declared write. Callers that need a write lock (an export taking a
    consistent snapshot, for instance) must ask this rather than checking
    READ_ONLY alone, or they will try to BEGIN IMMEDIATE on a connection the
    factory has already made read-only.
    """
    if READ_ONLY:
        return False
    if ASSISTANT_ACCESS_LEVEL:
        from utils import maintenance_lock

        return maintenance_lock.writing_now()
    return True


class _CoordinatedConnection:
    """Connection proxy that releases its OS shared lock on close.

    SQLite's connection object cannot carry arbitrary attributes, and the
    SQLCipher driver does not expose a portable connection subclass hook.  A
    transparent proxy keeps the lease lifetime identical to the database
    connection lifetime while delegating the full driver API.
    """

    __slots__ = ("_connection", "_maintenance_lease", "_closed")

    def __init__(self, connection, maintenance_lease):
        object.__setattr__(self, "_connection", connection)
        object.__setattr__(self, "_maintenance_lease", maintenance_lease)
        object.__setattr__(self, "_closed", False)

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def __setattr__(self, name, value):
        if name in self.__slots__:
            object.__setattr__(self, name, value)
        else:
            setattr(self._connection, name, value)

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback):
        return self._connection.__exit__(exc_type, exc, traceback)

    def close(self):
        if self._closed:
            return
        object.__setattr__(self, "_closed", True)
        try:
            self._connection.close()
        finally:
            from utils import maintenance_lock

            maintenance_lock.release_connection(self._maintenance_lease)
            object.__setattr__(self, "_maintenance_lease", None)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


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
    from utils import maintenance_lock

    # Acquire before opening SQLite and hold until close.  This covers a
    # connection that predates rotation; an open-time existence check did not.
    try:
        maintenance_lease = maintenance_lock.acquire_connection(DATABASE_PATH)
    except maintenance_lock.MaintenanceBusy as exc:
        raise DatabaseLocked(str(exc)) from exc
    assistant_may_write = False
    if ASSISTANT_ACCESS_LEVEL:
        # An assistant may only write inside a declared write, which holds a
        # shared OS lock across the complete tool invocation. Anything else gets
        # a read-only connection, so an undeclared mutation fails loudly.
        assistant_may_write = maintenance_lock.writing_now()
    conn = None
    try:
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _tighten_permissions(DATABASE_PATH)
        conn = _driver.connect(DATABASE_PATH, check_same_thread=False)
        # Again now the file exists: on a book's first run there was nothing to
        # chmod a moment ago.
        _tighten_permissions(DATABASE_PATH)
        if ENCRYPTION_AVAILABLE:
            # The key must be applied before touching any table, so this is the
            # first statement on the connection.
            conn.execute(f"PRAGMA key = {key_pragma(_active_key)}")
        conn.row_factory = _driver.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # Wait rather than fail when another connection holds the write lock. A
        # close-package export holds one for the length of the render, and the
        # driver's 5-second default meant the person working in the app got
        # "database is locked" while their assistant was busy.
        conn.execute("PRAGMA busy_timeout = 30000")
        # Query spills must never land beside an encrypted book as cleartext.
        conn.execute("PRAGMA temp_store = MEMORY")
        if READ_ONLY or (ASSISTANT_ACCESS_LEVEL and not assistant_may_write):
            conn.execute("PRAGMA query_only = ON")
        if ASSISTANT_ACCESS_LEVEL:
            # Set last so the connection-setup pragmas above are unaffected.
            conn.set_authorizer(_assistant_authorizer)
        return _CoordinatedConnection(conn, maintenance_lease)
    except Exception:
        if conn is not None:
            conn.close()
        maintenance_lock.release_connection(maintenance_lease)
        raise


def open_keyed(path, key=None):
    """Open an arbitrary database file with the active passphrase.

    ``key`` overrides it, for the one case that needs a file keyed with
    something other than what the session is currently holding: a backup taken
    under a NEW passphrase, just before the live book is re-encrypted with it.

    Used by the backup/restore paths, which must read and write SQLCipher files
    (a backup of an encrypted database must itself be encrypted). Requires the
    passphrase to be set, same as get_connection(). In fallback mode there is no
    key, so backups are plaintext like the database itself.
    """
    chosen = key or _active_key
    if ENCRYPTION_AVAILABLE and chosen is None:
        raise DatabaseLocked("Database is locked; unlock with the passphrase first.")
    from utils import maintenance_lock

    # Most callers use this for independent backup files. A few integrity and
    # migration checks open the live book directly, and those reads must join
    # the same lease protocol as get_connection().
    is_live_book = (
        os.path.normcase(os.path.abspath(os.fspath(path)))
        == os.path.normcase(os.path.abspath(os.fspath(DATABASE_PATH)))
    )
    maintenance_lease = None
    if is_live_book:
        try:
            maintenance_lease = maintenance_lock.acquire_connection(DATABASE_PATH)
        except maintenance_lock.MaintenanceBusy as exc:
            raise DatabaseLocked(str(exc)) from exc

    conn = None
    try:
        conn = _driver.connect(str(path), check_same_thread=False)
        if ENCRYPTION_AVAILABLE:
            conn.execute(f"PRAGMA key = {key_pragma(chosen)}")
        conn.row_factory = _driver.Row
        if is_live_book:
            return _CoordinatedConnection(conn, maintenance_lease)
        return conn
    except Exception:
        if conn is not None:
            conn.close()
        maintenance_lock.release_connection(maintenance_lease)
        raise


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
    with _cursor(commit) as cursor:
        yield cursor


@contextmanager
def _cursor(commit: bool):
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
