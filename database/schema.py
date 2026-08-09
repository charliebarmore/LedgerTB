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

        migration_sql = migration_path.read_text().strip()
        if not migration_sql.endswith(";"):
            migration_sql += ";"
        # version is a filename stem (controlled), but quote-escape defensively.
        safe_version = version.replace("'", "''")

        # Run the migration's statements AND record its version inside a single
        # explicit transaction. Otherwise executescript() commits the DDL on its
        # own and a crash before the separate version-insert would leave the
        # migration applied-but-unrecorded -- re-running it on the next startup,
        # which is unsafe for any non-idempotent migration (e.g. ALTER TABLE).
        script = (
            "BEGIN;\n"
            f"{migration_sql}\n"
            f"INSERT INTO schema_migrations (version) VALUES ('{safe_version}');\n"
            "COMMIT;"
        )
        try:
            conn.executescript(script)
        except Exception:
            conn.rollback()
            raise
