-- Migration 003: richer client records.
-- Add tax id, DBA, mailing address, primary contact, and notes to clients.
-- (Runs once, inside the runner's transaction; ADD COLUMN is safe there.)

ALTER TABLE clients ADD COLUMN tax_id TEXT;
ALTER TABLE clients ADD COLUMN dba_name TEXT;
ALTER TABLE clients ADD COLUMN address_line1 TEXT;
ALTER TABLE clients ADD COLUMN address_city TEXT;
ALTER TABLE clients ADD COLUMN address_state TEXT;
ALTER TABLE clients ADD COLUMN address_zip TEXT;
ALTER TABLE clients ADD COLUMN contact_name TEXT;
ALTER TABLE clients ADD COLUMN contact_email TEXT;
ALTER TABLE clients ADD COLUMN contact_phone TEXT;
ALTER TABLE clients ADD COLUMN notes TEXT;
