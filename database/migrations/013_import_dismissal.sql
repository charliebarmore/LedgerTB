-- A reviewed assistant-staged row may be intentionally dismissed without
-- deleting its provenance or identity.  The underlying status remains one of
-- the legacy CHECK-constrained values; dismissed_at is the durable terminal
-- marker and the model exposes its logical status as "Dismissed".
ALTER TABLE imported_transactions ADD COLUMN dismissed_at TIMESTAMP;
ALTER TABLE imported_transactions ADD COLUMN dismissed_by TEXT;

CREATE INDEX idx_imported_transactions_pending_review
    ON imported_transactions(client_id, status, dismissed_at);
