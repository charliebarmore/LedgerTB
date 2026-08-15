-- Correction proposals point to the journal entry they are intended to
-- correct. The relationship survives approval/rejection so a reviewer can
-- follow the original -> proposal -> posted correction chain later.
ALTER TABLE draft_entries
ADD COLUMN original_entry_id INTEGER REFERENCES journal_entries(id);

CREATE INDEX idx_draft_entries_original
ON draft_entries(client_id, original_entry_id, id);
