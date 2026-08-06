-- The audit_log CHECK constraint (last rebuilt in 006) never learned the
-- REVIEW action added later in code, so every Book Review audit event — and
-- any other REVIEW-action write — failed with an IntegrityError since the
-- feature shipped. SQLite cannot alter a CHECK, so rebuild the table with
-- the constraint matching models/audit_log.AUDIT_ACTIONS, keeping the
-- performed_by column from 009 and recreating the indexes dropped with the
-- old table.
CREATE TABLE audit_log_with_review (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    table_name TEXT NOT NULL,
    record_id INTEGER NOT NULL,
    action TEXT NOT NULL CHECK(action IN (
        'INSERT', 'UPDATE', 'DELETE', 'EXPORT', 'BACKUP', 'RESTORE',
        'CLOSE', 'REOPEN', 'REVERSE', 'OVERRIDE', 'REVIEW'
    )),
    old_values TEXT,
    new_values TEXT,
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    session_id TEXT,
    performed_by TEXT,
    FOREIGN KEY (client_id) REFERENCES clients(id)
);

INSERT INTO audit_log_with_review
    (id, client_id, table_name, record_id, action, old_values, new_values,
     changed_at, session_id, performed_by)
SELECT id, client_id, table_name, record_id, action, old_values, new_values,
       changed_at, session_id, performed_by
FROM audit_log;

DROP TABLE audit_log;
ALTER TABLE audit_log_with_review RENAME TO audit_log;

CREATE INDEX idx_audit_log_client ON audit_log(client_id);
CREATE INDEX idx_audit_log_table_record ON audit_log(table_name, record_id);
CREATE INDEX idx_audit_log_changed_at ON audit_log(changed_at);
