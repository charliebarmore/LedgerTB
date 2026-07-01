import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def create_tables(conn: sqlite3.Connection):
    """Bring the database schema up to date by applying any migrations in
    database/migrations/ that haven't run yet, in filename order (numeric
    prefix). Each migration runs at most once, tracked in schema_migrations.
    """
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    cursor.execute("SELECT version FROM schema_migrations")
    applied = {row[0] for row in cursor.fetchall()}

    for migration_path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = migration_path.stem
        if version in applied:
            continue

        conn.executescript(migration_path.read_text())
        conn.execute(
            "INSERT INTO schema_migrations (version) VALUES (?)",
            (version,)
        )
        conn.commit()
