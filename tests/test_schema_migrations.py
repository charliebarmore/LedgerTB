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


def test_create_tables_records_migrations(db):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT version FROM schema_migrations ORDER BY version")
    assert [row[0] for row in cur.fetchall()] == ["001_initial_schema", "002_money_to_cents"]
    conn.close()


def test_create_tables_is_idempotent(db):
    """Re-running create_tables (as init_database() does on every app start)
    must not error or re-apply an already-applied migration."""
    conn = get_connection()
    create_tables(conn)
    create_tables(conn)

    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM schema_migrations")
    assert cur.fetchone()[0] == 2  # 001_initial_schema + 002_money_to_cents
    conn.close()


def test_migration_failure_is_atomic(tmp_path, monkeypatch):
    """M10: if a migration errors partway, neither its partial DDL nor its
    version record survives -- so it is cleanly retried, never left
    applied-but-unrecorded."""
    import pytest
    from database import connection as dbc
    from database import schema as schema_mod

    monkeypatch.setattr(dbc, "DATABASE_PATH", tmp_path / "atomic.db")

    migdir = tmp_path / "migs"
    migdir.mkdir()
    # First statement succeeds, second (duplicate CREATE, no IF NOT EXISTS) fails.
    (migdir / "001_boom.sql").write_text(
        "CREATE TABLE will_rollback (id INTEGER);\n"
        "CREATE TABLE will_rollback (id INTEGER);\n"
    )
    monkeypatch.setattr(schema_mod, "MIGRATIONS_DIR", migdir)

    conn = dbc.get_connection()
    with pytest.raises(Exception):
        schema_mod.create_tables(conn)

    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM schema_migrations WHERE version = '001_boom'")
    assert cur.fetchone()[0] == 0  # not recorded
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='will_rollback'")
    assert cur.fetchone() is None  # partial DDL rolled back
    conn.close()
