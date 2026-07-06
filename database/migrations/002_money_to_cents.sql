-- Migration 002: store money as INTEGER cents instead of REAL dollars.
-- Binary floats can't represent most cent values exactly, which risks penny
-- drift in sums and lets a not-quite-balanced entry slip past a tolerance check.
-- Integer cents makes storage, SQL SUM()s, and balance checks exact.
--
-- SQLite can't change a column's type in place, so each affected table is
-- rebuilt: create a copy with INTEGER money columns, copy rows converting
-- dollars to cents (ROUND(x*100)), drop the old table, rename the copy. These
-- two tables are leaf tables (nothing references them), so the rebuild is safe.
-- The whole migration runs inside the runner's single transaction.

-- journal_entry_lines: debit/credit REAL -> INTEGER cents
CREATE TABLE journal_entry_lines_cents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    journal_entry_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    debit INTEGER DEFAULT 0,
    credit INTEGER DEFAULT 0,
    memo TEXT,
    FOREIGN KEY (journal_entry_id) REFERENCES journal_entries(id) ON DELETE CASCADE,
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

INSERT INTO journal_entry_lines_cents (id, journal_entry_id, account_id, debit, credit, memo)
    SELECT id, journal_entry_id, account_id,
           CAST(ROUND(debit * 100) AS INTEGER),
           CAST(ROUND(credit * 100) AS INTEGER),
           memo
    FROM journal_entry_lines;

DROP TABLE journal_entry_lines;
ALTER TABLE journal_entry_lines_cents RENAME TO journal_entry_lines;

CREATE INDEX IF NOT EXISTS idx_journal_entry_lines_entry ON journal_entry_lines(journal_entry_id);
CREATE INDEX IF NOT EXISTS idx_journal_entry_lines_account ON journal_entry_lines(account_id);

-- imported_transactions: amount REAL -> INTEGER cents
CREATE TABLE imported_transactions_cents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    import_batch TEXT,
    transaction_date DATE NOT NULL,
    description TEXT,
    amount INTEGER NOT NULL,
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

INSERT INTO imported_transactions_cents
    (id, client_id, import_batch, transaction_date, description, amount,
     bank_account_id, suggested_account_id, status, journal_entry_id, created_at)
    SELECT id, client_id, import_batch, transaction_date, description,
           CAST(ROUND(amount * 100) AS INTEGER),
           bank_account_id, suggested_account_id, status, journal_entry_id, created_at
    FROM imported_transactions;

DROP TABLE imported_transactions;
ALTER TABLE imported_transactions_cents RENAME TO imported_transactions;

CREATE INDEX IF NOT EXISTS idx_imported_transactions_status ON imported_transactions(status);
CREATE INDEX IF NOT EXISTS idx_imported_transactions_client ON imported_transactions(client_id);
