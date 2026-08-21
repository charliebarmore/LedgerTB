-- Preserve a complete lineage when an import batch is reversed and sent back
-- through human review. Original rows and journal entries are never deleted.
ALTER TABLE imported_transactions
ADD COLUMN superseded_by_batch TEXT;

ALTER TABLE imported_transactions
ADD COLUMN reversal_journal_entry_id INTEGER REFERENCES journal_entries(id);

ALTER TABLE imported_transactions
ADD COLUMN replaces_transaction_id INTEGER REFERENCES imported_transactions(id);

CREATE INDEX idx_imported_transactions_superseded
ON imported_transactions(client_id, superseded_by_batch);

CREATE INDEX idx_imported_transactions_replaces
ON imported_transactions(client_id, replaces_transaction_id);

CREATE TABLE import_batch_reversals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    original_batch TEXT NOT NULL,
    replacement_batch TEXT NOT NULL,
    reversal_date DATE NOT NULL,
    reason TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (client_id) REFERENCES clients(id),
    UNIQUE (client_id, original_batch),
    UNIQUE (client_id, replacement_batch)
);

CREATE INDEX idx_import_batch_reversals_client
ON import_batch_reversals(client_id, created_at);
