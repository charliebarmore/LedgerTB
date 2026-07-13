-- Expand audit actions beyond CRUD so exports, closes, reopens, restores, and
-- reversals are represented explicitly instead of masquerading as updates.

CREATE TABLE audit_log_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    table_name TEXT NOT NULL,
    record_id INTEGER NOT NULL,
    action TEXT NOT NULL CHECK(action IN (
        'INSERT', 'UPDATE', 'DELETE', 'EXPORT', 'BACKUP', 'RESTORE',
        'CLOSE', 'REOPEN', 'REVERSE'
    )),
    old_values TEXT,
    new_values TEXT,
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    session_id TEXT,
    FOREIGN KEY (client_id) REFERENCES clients(id)
);

INSERT INTO audit_log_events
    (id, client_id, table_name, record_id, action, old_values, new_values, changed_at, session_id)
SELECT id, client_id, table_name, record_id, action, old_values, new_values, changed_at, session_id
FROM audit_log;

DROP TABLE audit_log;
ALTER TABLE audit_log_events RENAME TO audit_log;

CREATE INDEX idx_audit_log_client ON audit_log(client_id);
CREATE INDEX idx_audit_log_table_record ON audit_log(table_name, record_id);
CREATE INDEX idx_audit_log_changed_at ON audit_log(changed_at);
