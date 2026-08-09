-- Stable identity for the entire encrypted book. Backups use this value to
-- prevent a valid backup belonging to one book from replacing another book.
-- It lives inside the database so renaming or moving the file does not change
-- its identity, and therefore does not orphan its recovery points.
CREATE TABLE book_identity (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    book_id TEXT NOT NULL UNIQUE CHECK (length(book_id) = 32),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO book_identity (id, book_id)
VALUES (1, lower(hex(randomblob(16))));
