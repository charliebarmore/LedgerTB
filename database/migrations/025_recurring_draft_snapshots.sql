-- Snapshot instructions per generation, not per mutable template/occurrence.
-- NULL deliberately identifies pre-upgrade drafts: their original reversal
-- choice/reference cannot reliably be recovered from today's template.
ALTER TABLE recurring_occurrence_drafts ADD COLUMN snapshot_reversal_rule TEXT
    CHECK (snapshot_reversal_rule IN ('None', 'NextDay'));
ALTER TABLE recurring_occurrence_drafts ADD COLUMN snapshot_template_name TEXT;
ALTER TABLE recurring_occurrence_drafts ADD COLUMN snapshot_source_reference TEXT;
