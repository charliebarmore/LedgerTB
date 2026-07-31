-- Record who performed each action. Single-user today, but books get handed
-- to reviewers and staff; attribution has to be captured when the work
-- happens, not reconstructed later.
ALTER TABLE audit_log ADD COLUMN performed_by TEXT;
ALTER TABLE journal_entries ADD COLUMN created_by TEXT;
ALTER TABLE imported_transactions ADD COLUMN created_by TEXT;
