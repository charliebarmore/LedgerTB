-- Durable source identity and duplicate-review metadata for imported rows.
ALTER TABLE imported_transactions ADD COLUMN source_id TEXT;
ALTER TABLE imported_transactions ADD COLUMN source_filename TEXT;
ALTER TABLE imported_transactions ADD COLUMN source_row_number INTEGER;
ALTER TABLE imported_transactions ADD COLUMN row_fingerprint TEXT;
ALTER TABLE imported_transactions ADD COLUMN idempotency_key TEXT;
ALTER TABLE imported_transactions ADD COLUMN duplicate_override INTEGER NOT NULL DEFAULT 0;
ALTER TABLE imported_transactions ADD COLUMN duplicate_override_reason TEXT;
ALTER TABLE imported_transactions ADD COLUMN duplicate_of_id INTEGER;

CREATE UNIQUE INDEX idx_imported_transactions_idempotency
    ON imported_transactions(client_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX idx_imported_transactions_fingerprint
    ON imported_transactions(client_id, row_fingerprint);

-- Duplicate overrides are accounting events, not ordinary field updates.
CREATE TABLE audit_log_import_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    table_name TEXT NOT NULL,
    record_id INTEGER NOT NULL,
    action TEXT NOT NULL CHECK(action IN (
        'INSERT', 'UPDATE', 'DELETE', 'EXPORT', 'BACKUP', 'RESTORE',
        'CLOSE', 'REOPEN', 'REVERSE', 'OVERRIDE'
    )),
    old_values TEXT,
    new_values TEXT,
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    session_id TEXT,
    FOREIGN KEY (client_id) REFERENCES clients(id)
);

INSERT INTO audit_log_import_events
    (id, client_id, table_name, record_id, action, old_values, new_values, changed_at, session_id)
SELECT id, client_id, table_name, record_id, action, old_values, new_values, changed_at, session_id
FROM audit_log;

DROP TABLE audit_log;
ALTER TABLE audit_log_import_events RENAME TO audit_log;

CREATE INDEX idx_audit_log_client ON audit_log(client_id);
CREATE INDEX idx_audit_log_table_record ON audit_log(table_name, record_id);
CREATE INDEX idx_audit_log_changed_at ON audit_log(changed_at);
