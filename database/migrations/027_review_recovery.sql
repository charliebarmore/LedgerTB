-- Restore generation is rotated on the prepared copy before atomic replacement.
CREATE TABLE book_generation (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    generation TEXT NOT NULL
);
INSERT INTO book_generation VALUES (1, lower(hex(randomblob(16))));

-- Automatic per-window recovery never overwrites the explicit saved review.
CREATE TABLE import_review_recovery (
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    window_id TEXT NOT NULL,
    revision TEXT NOT NULL,
    book_generation TEXT NOT NULL,
    base_saved_revision TEXT,
    fingerprint TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (client_id, window_id)
);
