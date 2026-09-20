"""Detect restoration even when a book keeps the same path and client IDs."""
import uuid
from database.connection import get_cursor


class StaleBookError(ValueError):
    pass


def read(cursor):
    if not cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='book_generation'").fetchone():
        return None  # Older read-only books cannot migrate.
    row = cursor.execute('SELECT generation FROM book_generation WHERE id=1').fetchone()
    return row[0] if row else None


def current():
    with get_cursor() as cursor:
        return read(cursor)


def require_current(cursor, expected):
    if expected != read(cursor):
        raise StaleBookError('This book was restored after the review opened. Reload the restored book and review its current transactions before continuing.')


def rotate(connection):
    """Called only inside the audited restore transaction on its prepared copy."""
    value = uuid.uuid4().hex
    connection.execute('UPDATE book_generation SET generation=? WHERE id=1', (value,))
    return value
