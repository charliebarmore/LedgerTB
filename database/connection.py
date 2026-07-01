import sqlite3
from pathlib import Path
from config import DATABASE_PATH
from .schema import create_tables


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory enabled."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_database():
    """Initialize the database with tables."""
    conn = get_connection()
    create_tables(conn)
    conn.close()
