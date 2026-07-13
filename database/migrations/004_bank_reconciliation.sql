-- Formal bank and credit-card reconciliations.
-- Money remains integer cents, consistent with migration 002.

CREATE TABLE bank_reconciliations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    statement_start_date DATE NOT NULL,
    statement_end_date DATE NOT NULL,
    statement_ending_balance INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'Draft' CHECK(status IN ('Draft', 'Completed')),
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (client_id) REFERENCES clients(id),
    FOREIGN KEY (account_id) REFERENCES accounts(id),
    CHECK(statement_start_date <= statement_end_date),
    UNIQUE(client_id, account_id, statement_end_date)
);

-- An account can have only one working statement at a time.
CREATE UNIQUE INDEX idx_bank_reconciliations_one_draft
    ON bank_reconciliations(client_id, account_id)
    WHERE status = 'Draft';
CREATE INDEX idx_bank_reconciliations_account_end
    ON bank_reconciliations(client_id, account_id, statement_end_date);

CREATE TABLE bank_reconciliation_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reconciliation_id INTEGER NOT NULL,
    journal_entry_line_id INTEGER NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (reconciliation_id) REFERENCES bank_reconciliations(id) ON DELETE CASCADE,
    FOREIGN KEY (journal_entry_line_id) REFERENCES journal_entry_lines(id) ON DELETE RESTRICT
);

CREATE INDEX idx_bank_reconciliation_items_reconciliation
    ON bank_reconciliation_items(reconciliation_id);
