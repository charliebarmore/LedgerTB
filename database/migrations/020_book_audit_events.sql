-- Two changes to audit_log, both needed before a passphrase change can be
-- recorded. SQLite cannot alter a CHECK or drop a NOT NULL, so the table is
-- rebuilt as migration 015 did, keeping performed_by from 009 and recreating
-- the indexes that go with the dropped table.
--
-- 1. The CHECK learns REKEY. Adding an action to
--    models/audit_log.AUDIT_ACTIONS without this makes every write of it an
--    IntegrityError, which is the trap CLAUDE.md records from migration 015.
--
-- 2. client_id becomes nullable, for events that belong to the book rather
--    than to a client. Changing a passphrase is one; so are backups and
--    restores, which until now were attached to whichever client happened to
--    be first, and silently dropped when a book had no clients at all. A book
--    with no clients yet is exactly the one being set up or handed over, so it
--    is the worst case to lose a security event in.
--
-- Existing rows keep their client_id, so nothing already recorded changes.
CREATE TABLE audit_log_with_book_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER,
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

INSERT INTO audit_log_with_book_events
    (id, client_id, table_name, record_id, action, old_values, new_values,
     changed_at, session_id, performed_by)
SELECT id, client_id, table_name, record_id, action, old_values, new_values,
       changed_at, session_id, performed_by
FROM audit_log;

DROP TABLE audit_log;
ALTER TABLE audit_log_with_book_events RENAME TO audit_log;

CREATE INDEX idx_audit_log_client ON audit_log(client_id);
CREATE INDEX idx_audit_log_table_record ON audit_log(table_name, record_id);
CREATE INDEX idx_audit_log_changed_at ON audit_log(changed_at);
