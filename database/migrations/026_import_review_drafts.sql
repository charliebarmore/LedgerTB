-- Human-saved review copies; no journal or imported-transaction mutations.
CREATE TABLE IF NOT EXISTS import_review_drafts (
    client_id INTEGER PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
    revision TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    payload TEXT NOT NULL,
    saved_at TEXT NOT NULL
);
