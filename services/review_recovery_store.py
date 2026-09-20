"""Encrypted automatic review recovery, independently owned by each UI window."""
from datetime import datetime, timezone
import uuid

from database.connection import get_cursor
from database import connection as dbconn
from models.audit_log import AuditLog
from services import book_generation, import_review_drafts as drafts

MAX_COPIES = 20


def save(client_id, window_id, rows, *, expected_generation, base_revision=None):
    if not expected_generation or not dbconn.ENCRYPTION_AVAILABLE:
        raise ValueError('Recovery requires an upgraded encrypted book.')
    payload = drafts.encode_payload(rows)
    fingerprint = drafts.content_fingerprint(rows)
    with get_cursor(commit=True) as cur:
        cur.execute('BEGIN IMMEDIATE')
        book_generation.require_current(cur, expected_generation)
        old = cur.execute('SELECT revision,fingerprint,base_saved_revision,book_generation FROM import_review_recovery WHERE client_id=? AND window_id=?', (client_id, window_id)).fetchone()
        if old and (old['fingerprint'], old['base_saved_revision'], old['book_generation']) == (fingerprint, base_revision, expected_generation):
            return old['revision']
        if not old and cur.execute('SELECT COUNT(*) FROM import_review_recovery WHERE client_id=?', (client_id,)).fetchone()[0] >= MAX_COPIES:
            raise ValueError('There are 20 recovery copies for this client. Discard an unneeded recovery copy before creating another.')
        revision = uuid.uuid4().hex
        cur.execute('''INSERT INTO import_review_recovery
            (client_id,window_id,revision,book_generation,base_saved_revision,fingerprint,row_count,payload,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(client_id,window_id) DO UPDATE SET
            revision=excluded.revision,book_generation=excluded.book_generation,
            base_saved_revision=excluded.base_saved_revision,fingerprint=excluded.fingerprint,
            row_count=excluded.row_count,payload=excluded.payload,updated_at=excluded.updated_at''',
            (client_id, window_id, revision, expected_generation, base_revision, fingerprint, len(rows), payload,
             datetime.now(timezone.utc).isoformat(timespec='seconds')))
        AuditLog.write(cur, client_id, 'import_review_recovery', client_id, 'UPDATE' if old else 'INSERT',
                       new_values={'window_id': window_id, 'revision': revision, 'row_count': len(rows)})
        return revision


def list_copies(client_id, exclude_window=None):
    with get_cursor() as cur:
        if not cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='import_review_recovery'").fetchone():
            return []
        return [dict(row) for row in cur.execute('''SELECT window_id,revision,row_count,updated_at,
            base_saved_revision,book_generation FROM import_review_recovery
            WHERE client_id=? AND window_id != ? ORDER BY updated_at DESC,window_id''', (client_id, exclude_window or ''))]


def load(client_id, window_id, revision):
    with get_cursor() as cur:
        row = cur.execute('SELECT payload,base_saved_revision FROM import_review_recovery WHERE client_id=? AND window_id=? AND revision=?', (client_id, window_id, revision)).fetchone()
        if row is None:
            raise drafts.ReviewConflict('This recovery copy changed in another window. Refresh and choose the current copy.')
        return drafts.decode_payload(row['payload']), row['base_saved_revision']


def discard(client_id, window_id, *, expected_generation, revision=None):
    with get_cursor(commit=True) as cur:
        cur.execute('BEGIN IMMEDIATE')
        book_generation.require_current(cur, expected_generation)
        old = cur.execute('SELECT revision FROM import_review_recovery WHERE client_id=? AND window_id=?', (client_id, window_id)).fetchone()
        if revision is not None and (not old or old['revision'] != revision):
            raise drafts.ReviewConflict('This recovery copy changed. Refresh before discarding it.')
        if old:
            cur.execute('DELETE FROM import_review_recovery WHERE client_id=? AND window_id=?', (client_id, window_id))
            AuditLog.write(cur, client_id, 'import_review_recovery', client_id, 'DELETE',
                           old_values={'window_id': window_id, 'revision': old['revision']})
