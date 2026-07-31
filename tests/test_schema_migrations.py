from database.connection import get_connection
from database.schema import create_tables
from database.schema import MIGRATIONS_DIR


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
        "bank_reconciliations", "bank_reconciliation_items",
        "import_profiles",
    }
    assert expected.issubset(tables)


def test_create_tables_records_migrations(db):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT version FROM schema_migrations ORDER BY version")
    assert [row[0] for row in cur.fetchall()] == [
        "001_initial_schema", "002_money_to_cents", "003_client_info",
        "004_bank_reconciliation", "005_audit_events", "006_import_idempotency",
        "007_import_profiles", "008_multiple_import_profiles",
        "009_activity_actor"]
    conn.close()


def test_create_tables_is_idempotent(db):
    """Re-running create_tables (as init_database() does on every app start)
    must not error or re-apply an already-applied migration."""
    conn = get_connection()
    create_tables(conn)
    create_tables(conn)

    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM schema_migrations")
    assert cur.fetchone()[0] == 9
    conn.close()


def test_actor_columns_exist(db):
    conn = get_connection()
    cur = conn.cursor()
    for table, column in [("audit_log", "performed_by"),
                          ("journal_entries", "created_by"),
                          ("imported_transactions", "created_by")]:
        cur.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in cur.fetchall()}
        assert column in columns, f"{table} missing {column}"
    conn.close()


def test_multiple_profile_migration_preserves_existing_mapping():
    """The original one-per-account profile becomes a named legacy format."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE clients (id INTEGER PRIMARY KEY);"
        "CREATE TABLE accounts (id INTEGER PRIMARY KEY, client_id INTEGER);"
        "INSERT INTO clients VALUES (1);"
        "INSERT INTO accounts VALUES (10, 1);"
    )
    conn.executescript((MIGRATIONS_DIR / "007_import_profiles.sql").read_text())
    conn.execute(
        """
        INSERT INTO import_profiles
            (client_id, bank_account_id, date_column, description_column,
             amount_format, amount_column, sign_convention)
        VALUES (1, 10, 'Posted Date', 'Merchant', 'single', 'Net Amount', 'bank')
        """
    )
    conn.executescript(
        (MIGRATIONS_DIR / "008_multiple_import_profiles.sql").read_text()
    )

    row = conn.execute("SELECT * FROM import_profiles").fetchone()
    assert row["name"] == "Default"
    assert row["date_column"] == "Posted Date"
    assert row["amount_column"] == "Net Amount"
    assert row["header_signature"] is None
    conn.close()


def test_migration_failure_is_atomic(tmp_path, monkeypatch):
    """M10: if a migration errors partway, neither its partial DDL nor its
    version record survives -- so it is cleanly retried, never left
    applied-but-unrecorded."""
    import pytest
    from database import connection as dbc
    from database import schema as schema_mod

    monkeypatch.setattr(dbc, "DATABASE_PATH", tmp_path / "atomic.db")
    from database.crypto import derive_key
    dbc.set_active_key(derive_key("test-passphrase"))

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
