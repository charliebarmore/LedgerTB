-- Firm-level document branding for generated reports (single row, id = 1).
-- The logo lives in the database so it is encrypted at rest and travels with
-- backups like everything else.
CREATE TABLE firm_branding (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    firm_name TEXT NOT NULL DEFAULT '',
    tagline TEXT NOT NULL DEFAULT '',
    accent_hex TEXT NOT NULL DEFAULT '',
    logo BLOB,
    logo_mime TEXT,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
