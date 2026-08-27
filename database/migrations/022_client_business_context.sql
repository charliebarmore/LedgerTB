-- Optional client-authored context supplied to AI categorization. Kept
-- separate from engagement notes so only intentionally designated context is
-- sent to the model.
ALTER TABLE clients ADD COLUMN business_context TEXT;
