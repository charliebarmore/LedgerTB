-- Reusable journal-entry templates and human-triggered recurring schedules.
-- Scheduled occurrences generate draft_entries for review; nothing in this
-- migration creates or posts a journal entry directly.

CREATE TABLE journal_entry_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    source_reference TEXT,
    entry_type TEXT NOT NULL DEFAULT 'Regular'
        CHECK (entry_type IN ('Regular', 'Adjusting')),
    archived_at TIMESTAMP,
    archived_by TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_je_templates_active_name
ON journal_entry_templates(client_id, name COLLATE NOCASE)
WHERE archived_at IS NULL;

CREATE INDEX idx_je_templates_client
ON journal_entry_templates(client_id, archived_at, name COLLATE NOCASE);

CREATE TABLE journal_entry_template_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL REFERENCES journal_entry_templates(id),
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    debit_cents INTEGER NOT NULL DEFAULT 0 CHECK (debit_cents >= 0),
    credit_cents INTEGER NOT NULL DEFAULT 0 CHECK (credit_cents >= 0),
    memo TEXT,
    sort_order INTEGER NOT NULL CHECK (sort_order >= 0),
    CHECK (
        (debit_cents > 0 AND credit_cents = 0)
        OR (credit_cents > 0 AND debit_cents = 0)
    ),
    UNIQUE(template_id, sort_order)
);

CREATE INDEX idx_je_template_lines_template
ON journal_entry_template_lines(template_id, sort_order);

CREATE TABLE recurring_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL UNIQUE REFERENCES journal_entry_templates(id),
    frequency TEXT NOT NULL
        CHECK (frequency IN ('Monthly', 'Quarterly', 'Annually')),
    date_rule TEXT NOT NULL
        CHECK (date_rule IN ('PeriodEnd', 'PeriodStart', 'DayOfMonth')),
    day_of_month INTEGER CHECK (day_of_month BETWEEN 1 AND 31),
    starts_on DATE NOT NULL,
    ends_on DATE,
    reversal_rule TEXT NOT NULL DEFAULT 'None'
        CHECK (reversal_rule IN ('None', 'NextDay')),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by TEXT NOT NULL,
    CHECK (ends_on IS NULL OR ends_on >= starts_on),
    CHECK (
        (date_rule = 'DayOfMonth' AND frequency = 'Monthly'
         AND day_of_month IS NOT NULL)
        OR (date_rule IN ('PeriodEnd', 'PeriodStart')
            AND day_of_month IS NULL)
    ),
    CHECK (reversal_rule = 'None' OR date_rule = 'PeriodEnd')
);

CREATE INDEX idx_recurring_schedules_active
ON recurring_schedules(is_active, starts_on, ends_on);

CREATE TABLE recurring_occurrences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id INTEGER NOT NULL REFERENCES recurring_schedules(id),
    period_name TEXT NOT NULL,
    period_type TEXT NOT NULL
        CHECK (period_type IN ('Month', 'Quarter', 'Year')),
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    scheduled_entry_date DATE NOT NULL,
    disposition TEXT NOT NULL
        CHECK (disposition IN ('Generated', 'Skipped')),
    generated_at TIMESTAMP,
    generated_by TEXT,
    skipped_at TIMESTAMP,
    skipped_by TEXT,
    skip_reason TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (period_end >= period_start),
    CHECK (scheduled_entry_date >= period_start),
    CHECK (scheduled_entry_date <= period_end),
    CHECK (
        (disposition = 'Generated'
         AND generated_at IS NOT NULL AND generated_by IS NOT NULL
         AND skipped_at IS NULL AND skipped_by IS NULL AND skip_reason IS NULL)
        OR
        (disposition = 'Skipped'
         AND skipped_at IS NOT NULL AND skipped_by IS NOT NULL
         AND length(trim(skip_reason)) > 0
         AND generated_at IS NULL AND generated_by IS NULL)
    ),
    UNIQUE(schedule_id, period_start, period_end)
);

CREATE INDEX idx_recurring_occurrences_schedule
ON recurring_occurrences(schedule_id, period_start, period_end);

CREATE TABLE recurring_occurrence_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurrence_id INTEGER NOT NULL REFERENCES recurring_occurrences(id),
    draft_entry_id INTEGER NOT NULL UNIQUE REFERENCES draft_entries(id),
    role TEXT NOT NULL CHECK (role IN ('Primary', 'Reversal')),
    generation_number INTEGER NOT NULL CHECK (generation_number >= 1),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(occurrence_id, role, generation_number)
);

CREATE INDEX idx_recurring_occurrence_drafts_occurrence
ON recurring_occurrence_drafts(occurrence_id, role, generation_number);
