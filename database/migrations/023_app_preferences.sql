-- Book-level display preferences. These live in the encrypted book so the
-- desktop app behaves consistently after backup/restore or on another machine.
CREATE TABLE app_preferences (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    date_format TEXT NOT NULL DEFAULT 'MM/DD/YYYY'
        CHECK(date_format IN ('MM/DD/YYYY', 'DD/MM/YYYY', 'YYYY/MM/DD')),
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
