import sqlite3


def create_tables(conn: sqlite3.Connection):
    """Create all database tables if they don't exist."""
    cursor = conn.cursor()

    # Clients table - for multi-client support
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            entity_type TEXT,
            business_type TEXT,
            fiscal_year_end_month INTEGER DEFAULT 12,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Check if clients table needs business_type column (migration)
    cursor.execute("PRAGMA table_info(clients)")
    client_columns = [col[1] for col in cursor.fetchall()]
    if 'business_type' not in client_columns:
        cursor.execute("ALTER TABLE clients ADD COLUMN business_type TEXT")
        conn.commit()

    # Check if accounts table needs migration (add client_id)
    cursor.execute("PRAGMA table_info(accounts)")
    columns = [col[1] for col in cursor.fetchall()]

    if 'client_id' not in columns and 'id' in columns:
        # Migration: add client_id to existing tables
        _migrate_to_multi_client(conn)
    elif 'id' not in columns:
        # Fresh install - create tables with client_id
        _create_tables_with_client_support(cursor)
        conn.commit()
    # else: tables already have client_id, no action needed

    # Check if accounts table needs description column (migration)
    cursor.execute("PRAGMA table_info(accounts)")
    account_columns = [col[1] for col in cursor.fetchall()]
    if 'description' not in account_columns:
        cursor.execute("ALTER TABLE accounts ADD COLUMN description TEXT")
        conn.commit()

    # Ensure imported_transactions table exists (may be missing in older databases)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS imported_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            import_batch TEXT,
            transaction_date DATE NOT NULL,
            description TEXT,
            amount REAL NOT NULL,
            bank_account_id INTEGER,
            suggested_account_id INTEGER,
            status TEXT DEFAULT 'Pending' CHECK(status IN ('Pending', 'Categorized', 'Posted')),
            journal_entry_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients(id),
            FOREIGN KEY (bank_account_id) REFERENCES accounts(id),
            FOREIGN KEY (suggested_account_id) REFERENCES accounts(id),
            FOREIGN KEY (journal_entry_id) REFERENCES journal_entries(id)
        )
    """)

    # Audit log table - tracks all changes to journal entries
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            table_name TEXT NOT NULL,
            record_id INTEGER NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('INSERT', 'UPDATE', 'DELETE')),
            old_values TEXT,
            new_values TEXT,
            changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            session_id TEXT,
            FOREIGN KEY (client_id) REFERENCES clients(id)
        )
    """)

    # Fiscal periods table - for worksheet date ranges
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fiscal_periods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            period_name TEXT NOT NULL,
            period_type TEXT NOT NULL CHECK(period_type IN ('Year', 'Quarter', 'Month', 'Custom')),
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            is_closed INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients(id)
        )
    """)

    # Add aje_reference column to journal_entries if it doesn't exist
    cursor.execute("PRAGMA table_info(journal_entries)")
    je_columns = [col[1] for col in cursor.fetchall()]
    if 'aje_reference' not in je_columns:
        cursor.execute("ALTER TABLE journal_entries ADD COLUMN aje_reference TEXT")

    # Migrate journal_entries to support 'Beginning Balance' entry type
    # Check current CHECK constraint by testing an insert
    _migrate_entry_type_constraint(conn)

    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_accounts_client ON accounts(client_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_journal_entries_client ON journal_entries(client_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_journal_entries_date ON journal_entries(entry_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_journal_entry_lines_entry ON journal_entry_lines(journal_entry_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_journal_entry_lines_account ON journal_entry_lines(account_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_imported_transactions_status ON imported_transactions(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_imported_transactions_client ON imported_transactions(client_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_categorization_rules_pattern ON categorization_rules(pattern)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_categorization_rules_client ON categorization_rules(client_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_client ON audit_log(client_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_table_record ON audit_log(table_name, record_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fiscal_periods_client ON fiscal_periods(client_id)")

    conn.commit()


def _create_tables_with_client_support(cursor):
    """Create all tables with client_id support (fresh install)."""

    # Accounts table (Chart of Accounts)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            account_number TEXT NOT NULL,
            name TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('Asset', 'Liability', 'Equity', 'Revenue', 'Expense')),
            subtype TEXT,
            description TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients(id),
            UNIQUE(client_id, account_number)
        )
    """)

    # Journal Entries header table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS journal_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            entry_date DATE NOT NULL,
            description TEXT,
            source_reference TEXT,
            entry_type TEXT DEFAULT 'Regular' CHECK(entry_type IN ('Regular', 'Adjusting', 'Closing', 'Beginning Balance')),
            aje_reference TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients(id)
        )
    """)

    # Journal Entry Lines (debits and credits)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS journal_entry_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            journal_entry_id INTEGER NOT NULL,
            account_id INTEGER NOT NULL,
            debit REAL DEFAULT 0,
            credit REAL DEFAULT 0,
            memo TEXT,
            FOREIGN KEY (journal_entry_id) REFERENCES journal_entries(id) ON DELETE CASCADE,
            FOREIGN KEY (account_id) REFERENCES accounts(id)
        )
    """)

    # Vendors table for pattern learning
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vendors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            FOREIGN KEY (client_id) REFERENCES clients(id)
        )
    """)

    # Categorization rules for vendor pattern matching
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS categorization_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            vendor_id INTEGER,
            pattern TEXT NOT NULL,
            default_account_id INTEGER NOT NULL,
            confidence REAL DEFAULT 1.0,
            times_used INTEGER DEFAULT 0,
            FOREIGN KEY (client_id) REFERENCES clients(id),
            FOREIGN KEY (vendor_id) REFERENCES vendors(id),
            FOREIGN KEY (default_account_id) REFERENCES accounts(id)
        )
    """)

    # Imported transactions (staging table for bank imports)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS imported_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            import_batch TEXT,
            transaction_date DATE NOT NULL,
            description TEXT,
            amount REAL NOT NULL,
            bank_account_id INTEGER,
            suggested_account_id INTEGER,
            status TEXT DEFAULT 'Pending' CHECK(status IN ('Pending', 'Categorized', 'Posted')),
            journal_entry_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients(id),
            FOREIGN KEY (bank_account_id) REFERENCES accounts(id),
            FOREIGN KEY (suggested_account_id) REFERENCES accounts(id),
            FOREIGN KEY (journal_entry_id) REFERENCES journal_entries(id)
        )
    """)

    # Audit log table - tracks all changes to journal entries
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            table_name TEXT NOT NULL,
            record_id INTEGER NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('INSERT', 'UPDATE', 'DELETE')),
            old_values TEXT,
            new_values TEXT,
            changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            session_id TEXT,
            FOREIGN KEY (client_id) REFERENCES clients(id)
        )
    """)

    # Fiscal periods table - for worksheet date ranges
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fiscal_periods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            period_name TEXT NOT NULL,
            period_type TEXT NOT NULL CHECK(period_type IN ('Year', 'Quarter', 'Month', 'Custom')),
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            is_closed INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients(id)
        )
    """)


def _migrate_entry_type_constraint(conn: sqlite3.Connection):
    """Migrate journal_entries to support 'Beginning Balance' entry type."""
    cursor = conn.cursor()

    # First check if there's an old constraint table that needs recovery
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='journal_entries_old_constraint'")
    if cursor.fetchone():
        # Recovery needed - old table exists, copy data if new table is empty
        cursor.execute("SELECT COUNT(*) FROM journal_entries")
        new_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM journal_entries_old_constraint")
        old_count = cursor.fetchone()[0]

        if new_count == 0 and old_count > 0:
            # Copy data from old table
            cursor.execute("""
                INSERT INTO journal_entries (id, client_id, entry_date, description, source_reference, entry_type, aje_reference, created_at)
                SELECT id, client_id, entry_date, description, source_reference, entry_type, aje_reference, created_at
                FROM journal_entries_old_constraint
            """)
            conn.commit()

        # Drop the old table
        cursor.execute("DROP TABLE IF EXISTS journal_entries_old_constraint")
        conn.commit()
        return

    # Check if migration is needed by looking at current table schema
    cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='journal_entries'")
    row = cursor.fetchone()
    if not row:
        return  # Table doesn't exist yet

    create_sql = row[0] or ''
    if 'Beginning Balance' in create_sql:
        return  # Already migrated

    # Need to recreate table with new constraint
    # SQLite doesn't support ALTER TABLE to modify CHECK constraints
    try:
        # Get current record count
        cursor.execute("SELECT COUNT(*) FROM journal_entries")
        record_count = cursor.fetchone()[0]

        cursor.execute("ALTER TABLE journal_entries RENAME TO journal_entries_old_constraint")

        cursor.execute("""
            CREATE TABLE journal_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                entry_date DATE NOT NULL,
                description TEXT,
                source_reference TEXT,
                entry_type TEXT DEFAULT 'Regular' CHECK(entry_type IN ('Regular', 'Adjusting', 'Closing', 'Beginning Balance')),
                aje_reference TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (client_id) REFERENCES clients(id)
            )
        """)

        cursor.execute("""
            INSERT INTO journal_entries (id, client_id, entry_date, description, source_reference, entry_type, aje_reference, created_at)
            SELECT id, client_id, entry_date, description, source_reference, entry_type, aje_reference, created_at
            FROM journal_entries_old_constraint
        """)

        # Verify the copy worked
        cursor.execute("SELECT COUNT(*) FROM journal_entries")
        new_count = cursor.fetchone()[0]

        if new_count == record_count:
            cursor.execute("DROP TABLE journal_entries_old_constraint")
            conn.commit()
        else:
            # Copy failed, rollback
            raise Exception(f"Data copy failed: expected {record_count}, got {new_count}")

    except Exception as e:
        # If migration fails, rollback and try to restore
        conn.rollback()
        try:
            # Check if new table exists and is empty
            cursor.execute("SELECT COUNT(*) FROM journal_entries")
            if cursor.fetchone()[0] == 0:
                # Drop empty new table and restore old one
                cursor.execute("DROP TABLE journal_entries")
                cursor.execute("ALTER TABLE journal_entries_old_constraint RENAME TO journal_entries")
                conn.commit()
        except:
            pass


def _migrate_to_multi_client(conn: sqlite3.Connection):
    """Migrate existing single-client database to multi-client support."""
    cursor = conn.cursor()

    # Create a default client for existing data
    cursor.execute("""
        INSERT INTO clients (name, entity_type)
        VALUES ('My Company', 'Default')
    """)
    default_client_id = cursor.lastrowid

    # Rename old tables
    cursor.execute("ALTER TABLE accounts RENAME TO accounts_old")
    cursor.execute("ALTER TABLE journal_entries RENAME TO journal_entries_old")
    cursor.execute("ALTER TABLE journal_entry_lines RENAME TO journal_entry_lines_old")
    cursor.execute("ALTER TABLE vendors RENAME TO vendors_old")
    cursor.execute("ALTER TABLE categorization_rules RENAME TO categorization_rules_old")
    cursor.execute("ALTER TABLE imported_transactions RENAME TO imported_transactions_old")

    # Create new tables with client_id
    _create_tables_with_client_support(cursor)

    # Migrate data
    cursor.execute(f"""
        INSERT INTO accounts (id, client_id, account_number, name, type, subtype, is_active, created_at)
        SELECT id, {default_client_id}, account_number, name, type, subtype, is_active, created_at
        FROM accounts_old
    """)

    cursor.execute(f"""
        INSERT INTO journal_entries (id, client_id, entry_date, description, source_reference, entry_type, created_at)
        SELECT id, {default_client_id}, entry_date, description, source_reference, entry_type, created_at
        FROM journal_entries_old
    """)

    cursor.execute("""
        INSERT INTO journal_entry_lines (id, journal_entry_id, account_id, debit, credit, memo)
        SELECT id, journal_entry_id, account_id, debit, credit, memo
        FROM journal_entry_lines_old
    """)

    cursor.execute(f"""
        INSERT INTO vendors (id, client_id, name, normalized_name)
        SELECT id, {default_client_id}, name, normalized_name
        FROM vendors_old
    """)

    cursor.execute(f"""
        INSERT INTO categorization_rules (id, client_id, vendor_id, pattern, default_account_id, confidence, times_used)
        SELECT id, {default_client_id}, vendor_id, pattern, default_account_id, confidence, times_used
        FROM categorization_rules_old
    """)

    cursor.execute(f"""
        INSERT INTO imported_transactions (id, client_id, import_batch, transaction_date, description, amount, bank_account_id, suggested_account_id, status, journal_entry_id, created_at)
        SELECT id, {default_client_id}, import_batch, transaction_date, description, amount, bank_account_id, suggested_account_id, status, journal_entry_id, created_at
        FROM imported_transactions_old
    """)

    # Drop old tables
    cursor.execute("DROP TABLE accounts_old")
    cursor.execute("DROP TABLE journal_entries_old")
    cursor.execute("DROP TABLE journal_entry_lines_old")
    cursor.execute("DROP TABLE vendors_old")
    cursor.execute("DROP TABLE categorization_rules_old")
    cursor.execute("DROP TABLE imported_transactions_old")

    conn.commit()
    print(f"Migrated existing data to client ID {default_client_id}")
