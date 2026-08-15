-- Teach the audit_log CHECK the REKEY action, so changing a book's passphrase
-- can be recorded. SQLite cannot alter a CHECK, so the table is rebuilt exactly
-- as migration 015 did, keeping performed_by from 009 and recreating the
-- indexes that go with the dropped table.
--
-- This is the invariant in CLAUDE.md being honoured rather than worked around:
-- adding an action to models/audit_log.AUDIT_ACTIONS without this rebuild would
-- make every REKEY write an IntegrityError, and a security event that fails
-- silently is worse than one that was never designed.
CREATE TABLE audit_log_with_rekey (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    table_name TEXT NOT NULL,
    record_id INTEGER NOT NULL,
    action TEXT NOT NULL CHECK(action IN (
        'INSERT', 'UPDATE', 'DELETE', 'EXPORT', 'BACKUP', 'RESTORE',
        'CLOSE', 'REOPEN', 'REVERSE', 'OVERRIDE', 'REVIEW', 'REKEY'
    )),
    old_values TEXT,
    new_values TEXT,
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    session_id TEXT,
    performed_by TEXT,
    FOREIGN KEY (client_id) REFERENCES clients(id)
);

INSERT INTO audit_log_with_rekey
    (id, client_id, table_name, record_id, action, old_values, new_values,
     changed_at, session_id, performed_by)
SELECT id, client_id, table_name, record_id, action, old_values, new_values,
       changed_at, session_id, performed_by
FROM audit_log;

DROP TABLE audit_log;
ALTER TABLE audit_log_with_rekey RENAME TO audit_log;

CREATE INDEX idx_audit_log_client ON audit_log(client_id);
CREATE INDEX idx_audit_log_table_record ON audit_log(table_name, record_id);
CREATE INDEX idx_audit_log_changed_at ON audit_log(changed_at);
