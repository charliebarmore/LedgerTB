-- Review checkpoints for assistant activity. A person periodically attests
-- "I have reviewed everything the assistant did through audit row N"; the
-- marks are append-only so the review history is itself a record. Unreviewed
-- work = assistant-attributed audit rows past the latest mark.
CREATE TABLE assistant_review_marks (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    through_audit_id INTEGER NOT NULL,
    reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_by TEXT NOT NULL
);

CREATE INDEX idx_assistant_review_marks_latest
    ON assistant_review_marks(client_id, through_audit_id);
