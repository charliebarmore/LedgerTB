-- Draft entries: proposals awaiting human review. An assistant (MCP) may file
-- a draft; only a person, in the app, can turn one into a journal entry.
-- Lines are stored as JSON (account_number/debit_cents/credit_cents/memo) and
-- validated both when filed and again at approval.
CREATE TABLE draft_entries (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    proposed_by TEXT NOT NULL,
    proposed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    entry_date TEXT NOT NULL,
    entry_type TEXT NOT NULL DEFAULT 'Regular',
    description TEXT NOT NULL,
    rationale TEXT,
    lines_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    resolved_at TIMESTAMP,
    resolved_by TEXT,
    posted_entry_id INTEGER REFERENCES journal_entries(id)
);

CREATE INDEX idx_draft_entries_pending
    ON draft_entries(client_id, status);
