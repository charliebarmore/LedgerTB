from database.connection import get_connection
from database.schema import create_tables


def test_create_tables_builds_full_schema(db):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = {row[0] for row in cur.fetchall()}
    conn.close()

    expected = {
        "accounts", "audit_log", "categorization_rules", "clients",
        "fiscal_periods", "imported_transactions", "journal_entries",
        "journal_entry_lines", "schema_migrations", "vendors",
    }
    assert expected.issubset(tables)


def test_create_tables_records_migration_once(db):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT version FROM schema_migrations")
    assert [row[0] for row in cur.fetchall()] == ["001_initial_schema"]
    conn.close()


def test_create_tables_is_idempotent(db):
    """Re-running create_tables (as init_database() does on every app start)
    must not error or re-apply an already-applied migration."""
    conn = get_connection()
    create_tables(conn)
    create_tables(conn)

    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM schema_migrations")
    assert cur.fetchone()[0] == 1
    conn.close()
