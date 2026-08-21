"""Stable identities and duplicate classification for statement imports."""

import hashlib
import json
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from typing import Iterable, Optional

from database.connection import get_connection
from money import to_cents


def hash_source(content: bytes) -> str:
    """Return a content identity without retaining statement contents."""
    return hashlib.sha256(content).hexdigest()


def canonical_description(value: str) -> str:
    """Normalize harmless text variation while preserving identifying digits."""
    normalized = unicodedata.normalize("NFKC", value or "").upper()
    return " ".join(normalized.split())


def _iso_date(value) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value or "")[:10]


def _digest(parts: dict) -> str:
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def row_fingerprint(transaction: dict, client_id: int, bank_account_id: Optional[int]) -> str:
    """Identify the accounting facts of one imported transaction."""
    return _digest({
        "client_id": int(client_id),
        "bank_account_id": int(bank_account_id) if bank_account_id is not None else None,
        "date": _iso_date(transaction.get("date") or transaction.get("transaction_date")),
        "amount_cents": to_cents(transaction.get("amount", 0)),
        "description": canonical_description(transaction.get("description", "")),
    })


def ensure_import_identity(transaction: dict, client_id: int, bank_account_id: Optional[int]) -> dict:
    """Attach stable fingerprint and retry identity to a transaction dict."""
    transaction["row_fingerprint"] = row_fingerprint(transaction, client_id, bank_account_id)
    source_id = transaction.get("source_id")
    source_row = transaction.get("source_row_number")
    if source_id and source_row is not None:
        transaction["idempotency_key"] = _digest({
            "client_id": int(client_id),
            "bank_account_id": int(bank_account_id) if bank_account_id is not None else None,
            "source_id": str(source_id),
            "source_row_number": int(source_row),
        })
    else:
        # Non-file callers still get safe retry behavior. The import UI always
        # supplies source identity, which permits distinct identical rows.
        transaction["idempotency_key"] = transaction["row_fingerprint"]
    return transaction


def _mark_duplicate(transaction: dict, kind: str, info: dict) -> None:
    transaction["is_duplicate"] = True
    transaction["duplicate_kind"] = kind
    transaction["duplicate_info"] = info
    transaction["duplicate_override"] = False
    transaction["duplicate_override_reason"] = ""
    transaction["include"] = False


def classify_import_duplicates(transactions: Iterable[dict], client_id: int,
                               exclude_ids=frozenset()) -> int:
    """Mark duplicates within an upload and against durable imported history.

    Existing pre-migration rows are fingerprinted in place so historical imports
    participate in future checks. Only derived metadata is backfilled; no
    statement contents or user-entered accounting fields are changed.

    ``exclude_ids``: imported_transactions ids to ignore in the history — used
    when re-classifying rows that are themselves already staged in that table
    (assistant imports), which would otherwise each match their own record.
    """
    transactions = list(transactions)
    for transaction in transactions:
        ensure_import_identity(transaction, client_id, transaction.get("bank_account_id"))
        transaction["is_duplicate"] = False
        transaction.pop("duplicate_kind", None)
        transaction.pop("duplicate_info", None)

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, transaction_date, description, amount, bank_account_id,
                   journal_entry_id, row_fingerprint, idempotency_key,
                   superseded_by_batch
            FROM imported_transactions WHERE client_id = ?
            """,
            (client_id,),
        )
        existing = [r for r in cursor.fetchall() if r["id"] not in exclude_ids]
        by_fingerprint = defaultdict(list)
        by_idempotency = {}
        for row in existing:
            fingerprint = row["row_fingerprint"]
            if not fingerprint:
                fingerprint = row_fingerprint({
                    "transaction_date": row["transaction_date"],
                    "description": row["description"],
                    "amount": row["amount"] / 100,
                }, client_id, row["bank_account_id"])
                legacy_key = _digest({"legacy_imported_transaction_id": row["id"]})
                cursor.execute(
                    """UPDATE imported_transactions
                       SET row_fingerprint = ?, idempotency_key = COALESCE(idempotency_key, ?)
                       WHERE id = ?""",
                    (fingerprint, legacy_key, row["id"]),
                )
            # Reversed/superseded imports no longer represent an active ledger
            # posting. Keep their idempotency keys so the exact same source row
            # still cannot be replayed, but do not make a replacement look like
            # a duplicate merely because it has the same accounting facts.
            if not row["superseded_by_batch"]:
                by_fingerprint[fingerprint].append(row)
            if row["idempotency_key"]:
                by_idempotency[row["idempotency_key"]] = row
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    seen = {}
    duplicate_count = 0
    for index, transaction in enumerate(transactions, start=1):
        fingerprint = transaction["row_fingerprint"]
        exact = by_idempotency.get(transaction["idempotency_key"])
        replaced_id = transaction.get("replaces_transaction_id")
        if (exact and replaced_id and exact["id"] == replaced_id
                and exact["superseded_by_batch"]):
            exact = None
        if exact:
            _mark_duplicate(transaction, "previous_import", {
                "transaction_id": exact["id"],
                "entry_id": exact["journal_entry_id"],
                "entry_date": exact["transaction_date"],
                "exact_retry": True,
            })
        elif fingerprint in seen:
            first = seen[fingerprint]
            _mark_duplicate(transaction, "within_upload", {
                "source_row_number": first.get("source_row_number"),
                "upload_position": first.get("_upload_position"),
            })
        elif by_fingerprint.get(fingerprint):
            eligible = [
                prior for prior in by_fingerprint[fingerprint]
                if not (replaced_id and prior["id"] == replaced_id
                        and prior["superseded_by_batch"])
            ]
            prior = eligible[0] if eligible else None
            if prior is None:
                transaction["_upload_position"] = index
                seen.setdefault(fingerprint, transaction)
                continue
            _mark_duplicate(transaction, "previous_import", {
                "transaction_id": prior["id"],
                "entry_id": prior["journal_entry_id"],
                "entry_date": prior["transaction_date"],
                "exact_retry": False,
            })
        if transaction.get("is_duplicate"):
            duplicate_count += 1
        transaction["_upload_position"] = index
        seen.setdefault(fingerprint, transaction)
    return duplicate_count
