"""Explicit human review checkpoints inside the encrypted book.

Only row evidence and human review decisions survive. Credentials, consent,
provider responses and request caches never enter this table. A saved copy is
not a posting instruction; normal validation and duplicate checks run on resume.
"""

import hashlib
import json
import uuid
from datetime import date, datetime

from database.connection import get_cursor
from models.audit_log import AuditLog
from money import to_cents, to_dollars

_FIELDS = frozenset(
    {
        "description",
        "bank_account_id",
        "batch_id",
        "source_id",
        "source_filename",
        "source_row_number",
        "row_fingerprint",
        "idempotency_key",
        "staged_id",
        "include",
        "is_transfer",
        "selected_account_id",
        "receipt_text",
        "jev_accepted",
        "ai_review_accepted",
    }
)
MAX_ROWS = 50000
MAX_BYTES = 32 * 1024 * 1024


class ReviewConflict(ValueError):
    pass


def summary(client_id):
    with get_cursor() as cur:
        # A read-only book from an older app cannot run this migration.
        if not cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='import_review_drafts'"
        ).fetchone():
            return None
        row = cur.execute(
            "SELECT revision,row_count,saved_at FROM import_review_drafts WHERE client_id=?",
            (client_id,),
        ).fetchone()
        return dict(row) if row else None


def _encode_rows(rows):
    if not rows or len(rows) > MAX_ROWS:
        raise ValueError("A saved review needs between 1 and 50,000 rows.")
    encoded = []
    for row in rows:
        item = {k: row[k] for k in _FIELDS if k in row}
        item["date"] = date.fromisoformat(str(row["date"])[:10]).isoformat()
        item["amount_cents"] = to_cents(row["amount"])
        encoded.append(item)
    return encoded


def content_fingerprint(rows):
    """Compare saved evidence/decisions, ignoring widget IDs and display order."""
    encoded = _encode_rows(rows)
    for item in encoded:
        item["include"] = bool(item.get("include", True))
        item["is_transfer"] = bool(item.get("is_transfer", False))
        item["selected_account_id"] = item.get("selected_account_id") or 0
    items = sorted(json.dumps(item, sort_keys=True, separators=(",", ":"), allow_nan=False)
                   for item in encoded)
    return hashlib.sha256(json.dumps(items).encode()).hexdigest()


def save(client_id, rows, *, expected_revision=None):
    encoded = _encode_rows(rows)
    payload = json.dumps(
        {"version": 1, "rows": encoded}, separators=(",", ":"), allow_nan=False
    )
    if len(payload.encode()) > MAX_BYTES:
        raise ValueError("The review is too large to save as one copy.")
    revision = uuid.uuid4().hex
    with get_cursor(commit=True) as cur:
        # Lock before comparing the revision: two windows must not overwrite
        # one another between a read and write.
        cur.execute("BEGIN IMMEDIATE")
        old = cur.execute(
            "SELECT revision,row_count FROM import_review_drafts WHERE client_id=?",
            (client_id,),
        ).fetchone()
        if (old["revision"] if old else None) != expected_revision:
            raise ReviewConflict(
                "The saved review changed in another window. Resume that copy before replacing it."
            )
        cur.execute(
            "INSERT INTO import_review_drafts (client_id,revision,row_count,payload,saved_at) VALUES (?,?,?,?,?) ON CONFLICT(client_id) DO UPDATE SET revision=excluded.revision,row_count=excluded.row_count,payload=excluded.payload,saved_at=excluded.saved_at",
            (
                client_id,
                revision,
                len(rows),
                payload,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        AuditLog.write(
            cur,
            client_id,
            "import_review_drafts",
            client_id,
            "UPDATE" if old else "INSERT",
            old_values=dict(old) if old else None,
            new_values={"revision": revision, "row_count": len(rows)},
        )
    return revision


def load(client_id):
    with get_cursor() as cur:
        record = cur.execute(
            "SELECT revision,payload FROM import_review_drafts WHERE client_id=?",
            (client_id,),
        ).fetchone()
    if not record:
        return None
    data = json.loads(record["payload"])
    if data.get("version") != 1 or len(data["rows"]) > MAX_ROWS:
        raise ValueError("Unsupported saved review.")
    rows = []
    for item in data["rows"]:
        row = {k: item[k] for k in _FIELDS if k in item}
        row.update(
            date=date.fromisoformat(item["date"]),
            amount=to_dollars(item["amount_cents"]),
            uid=uuid.uuid4().hex,
        )
        # Prior duplicate overrides expire. A new human decision is required
        # against the current ledger, including anything posted since saving.
        row["duplicate_override"] = False
        rows.append(row)
    return record["revision"], rows


def discard(client_id, expected_revision):
    with get_cursor(commit=True) as cur:
        result = cur.execute(
            "DELETE FROM import_review_drafts WHERE client_id=? AND revision=?",
            (client_id, expected_revision),
        )
        if result.rowcount != 1:
            raise ReviewConflict(
                "The saved review changed. Refresh before discarding it."
            )
        AuditLog.write(
            cur,
            client_id,
            "import_review_drafts",
            client_id,
            "DELETE",
            old_values={"revision": expected_revision},
        )
