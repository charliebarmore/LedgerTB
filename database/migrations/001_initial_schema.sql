-- Baseline schema. Matches the live database as of the migration-system
-- cutover: every table/column here already existed in production via the
-- old ad-hoc PRAGMA-check migrations, so this file is written as the "final"
-- desired shape rather than the historical incremental steps. Uses
-- IF NOT EXISTS throughout so it's a safe no-op against that existing
-- database (already-applied) while building the full schema from scratch
-- for a brand new one (fresh install, tests).

CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    entity_type TEXT,
    business_type TEXT,
    fiscal_year_end_month INTEGER DEFAULT 12,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

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
);

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
);

CREATE TABLE IF NOT EXISTS journal_entry_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    journal_entry_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    debit REAL DEFAULT 0,
    credit REAL DEFAULT 0,
    memo TEXT,
    FOREIGN KEY (journal_entry_id) REFERENCES journal_entries(id) ON DELETE CASCADE,
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    FOREIGN KEY (client_id) REFERENCES clients(id)
);

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
);

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
);

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
);

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
);

CREATE INDEX IF NOT EXISTS idx_accounts_client ON accounts(client_id);
CREATE INDEX IF NOT EXISTS idx_journal_entries_client ON journal_entries(client_id);
CREATE INDEX IF NOT EXISTS idx_journal_entries_date ON journal_entries(entry_date);
CREATE INDEX IF NOT EXISTS idx_journal_entry_lines_entry ON journal_entry_lines(journal_entry_id);
CREATE INDEX IF NOT EXISTS idx_journal_entry_lines_account ON journal_entry_lines(account_id);
CREATE INDEX IF NOT EXISTS idx_imported_transactions_status ON imported_transactions(status);
CREATE INDEX IF NOT EXISTS idx_imported_transactions_client ON imported_transactions(client_id);
CREATE INDEX IF NOT EXISTS idx_categorization_rules_pattern ON categorization_rules(pattern);
CREATE INDEX IF NOT EXISTS idx_categorization_rules_client ON categorization_rules(client_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_client ON audit_log(client_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_table_record ON audit_log(table_name, record_id);
CREATE INDEX IF NOT EXISTS idx_fiscal_periods_client ON fiscal_periods(client_id);
