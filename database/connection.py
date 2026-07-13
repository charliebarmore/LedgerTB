import sqlite3
import os
from contextlib import contextmanager
from pathlib import Path
from config import DATABASE_PATH
from .schema import create_tables


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory enabled."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(DATABASE_PATH.parent, 0o700)
    except OSError:
        pass
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    try:
        os.chmod(DATABASE_PATH, 0o600)
    except OSError:
        pass
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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
    """Initialize the database with tables."""
    conn = get_connection()
    create_tables(conn)
    conn.close()
