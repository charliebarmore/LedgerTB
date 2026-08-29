"""Audit-preserving reversal and re-review of an imported transaction batch."""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from uuid import uuid4

from database.connection import get_connection
from models.audit_log import AuditLog
from models.journal_entry import JournalEntry, JournalEntryLine
from models.transaction import ImportedTransaction
from money import to_dollars
from services.import_identity import ensure_import_identity
from utils.actor import current_actor


@dataclass(frozen=True)
class BatchReversalPreview:
    batch_id: str
    row_count: int = 0
    posted_count: int = 0
    unposted_count: int = 0
    net_amount: float = 0.0
    blockers: tuple[str, ...] = field(default_factory=tuple)
    replacement_batch: Optional[str] = None

    @property
    def can_reverse(self) -> bool:
        return bool(self.row_count) and not self.blockers and not self.replacement_batch


@dataclass(frozen=True)
class BatchReversalResult:
    original_batch: str
    replacement_batch: str
    row_count: int
    reversed_postings: int
    replacement_transaction_ids: tuple[int, ...]


def _batch_rows(cursor, client_id: int, batch_id: str):
    cursor.execute(
        """SELECT * FROM imported_transactions
           WHERE client_id = ? AND import_batch = ?
           ORDER BY source_row_number IS NULL, source_row_number, id""",
        (client_id, batch_id),
    )
    return cursor.fetchall()


def _find_blockers(cursor, client_id: int, batch_id: str, rows) -> list[str]:
    blockers = []
    if not rows:
        return ["Import batch not found for the selected client."]

    cursor.execute(
        """SELECT replacement_batch FROM import_batch_reversals
           WHERE client_id = ? AND original_batch = ?""",
        (client_id, batch_id),
    )
    prior = cursor.fetchone()
    if prior:
        blockers.append(
            f"This batch was already reversed into {prior['replacement_batch']}."
        )

    if any(row["superseded_by_batch"] for row in rows):
        blockers.append("One or more rows in this batch were already superseded.")

    posted = [row for row in rows if row["status"] == "Posted"]
    if any(row["journal_entry_id"] is None for row in posted):
        blockers.append("A posted row is missing its journal-entry link.")

    entry_ids = sorted({row["journal_entry_id"] for row in posted
                        if row["journal_entry_id"] is not None})
    if entry_ids:
        placeholders = ", ".join("?" for _ in entry_ids)
        cursor.execute(
            f"""SELECT id FROM journal_entries
                WHERE client_id = ? AND id IN ({placeholders})""",
            [client_id, *entry_ids],
        )
        found = {row["id"] for row in cursor.fetchall()}
        missing = set(entry_ids) - found
        if missing:
            blockers.append("A linked journal entry could not be found.")

        references = [f"Reversal of JE #{entry_id}" for entry_id in entry_ids]
        ref_placeholders = ", ".join("?" for _ in references)
        cursor.execute(
            f"""SELECT source_reference FROM journal_entries
                WHERE client_id = ? AND source_reference IN ({ref_placeholders})""",
            [client_id, *references],
        )
        if cursor.fetchone():
            blockers.append("A posting in this batch has already been reversed.")

        cursor.execute(
            f"""SELECT 1
                FROM bank_reconciliation_items bri
                JOIN journal_entry_lines jel ON jel.id = bri.journal_entry_line_id
                WHERE jel.journal_entry_id IN ({placeholders}) LIMIT 1""",
            entry_ids,
        )
        if cursor.fetchone():
            blockers.append(
                "A posting is selected in a bank reconciliation. Unselect it, or "
                "reopen the completed reconciliation, before reversing this batch."
            )

        correction_refs = [f"Correction of imported JE #{entry_id}"
                           for entry_id in entry_ids]
        correction_placeholders = ", ".join("?" for _ in correction_refs)
        cursor.execute(
            f"""SELECT id FROM journal_entries
                WHERE client_id = ?
                  AND source_reference IN ({correction_placeholders})""",
            [client_id, *correction_refs],
        )
        correction_ids = {row["id"] for row in cursor.fetchall()}
        cursor.execute(
            f"""SELECT status, posted_entry_id FROM draft_entries
                WHERE client_id = ? AND original_entry_id IN ({placeholders})
                  AND status IN ('pending', 'approved')""",
            [client_id, *entry_ids],
        )
        drafts = cursor.fetchall()
        has_pending_correction = any(row["status"] == "pending" for row in drafts)
        approved_without_entry = any(
            row["status"] == "approved" and row["posted_entry_id"] is None
            for row in drafts
        )
        correction_ids.update(
            row["posted_entry_id"] for row in drafts
            if row["status"] == "approved" and row["posted_entry_id"] is not None
        )
        has_active_correction = False
        for correction_id in correction_ids:
            cursor.execute(
                """SELECT 1 FROM journal_entries
                   WHERE client_id = ? AND source_reference = ? LIMIT 1""",
                (client_id, f"Reversal of JE #{correction_id}"),
            )
            if cursor.fetchone() is None:
                has_active_correction = True
                break
        if (has_pending_correction or approved_without_entry
                or has_active_correction):
            blockers.append(
                "One or more posted transactions already have a category correction. "
                "Reverse that correction first, then undo this import."
            )

    return blockers


def preview_import_batch_reversal(client_id: int, batch_id: str) -> BatchReversalPreview:
    """Return a read-only safety check for one client-scoped import batch."""
    batch_id = (batch_id or "").strip()
    if not batch_id:
        return BatchReversalPreview(batch_id="", blockers=("Choose an import batch.",))

    conn = get_connection()
    try:
        cursor = conn.cursor()
        rows = _batch_rows(cursor, client_id, batch_id)
        blockers = _find_blockers(cursor, client_id, batch_id, rows)
        cursor.execute(
            """SELECT replacement_batch FROM import_batch_reversals
               WHERE client_id = ? AND original_batch = ?""",
            (client_id, batch_id),
        )
        prior = cursor.fetchone()
        return BatchReversalPreview(
            batch_id=batch_id,
            row_count=len(rows),
            posted_count=sum(row["status"] == "Posted" for row in rows),
            unposted_count=sum(row["status"] != "Posted" for row in rows),
            net_amount=to_dollars(sum(row["amount"] for row in rows)),
            blockers=tuple(blockers),
            replacement_batch=prior["replacement_batch"] if prior else None,
        )
    finally:
        conn.close()


def reverse_import_batch(
    *, client_id: int, batch_id: str, reversal_date: date, reason: str,
    replacement_bank_account_id: Optional[int] = None,
) -> BatchReversalResult:
    """Reverse all posted rows and stage a fresh replacement batch atomically."""
    batch_id = (batch_id or "").strip()
    reason = (reason or "").strip()
    if not batch_id:
        raise ValueError("Choose an import batch.")
    if not reversal_date:
        raise ValueError("A reversal date is required.")
    if not reason:
        raise ValueError("A reason is required so the audit trail explains the reversal.")

    replacement_batch = f"redo-{uuid4().hex[:12]}"
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        rows = _batch_rows(cursor, client_id, batch_id)
        blockers = _find_blockers(cursor, client_id, batch_id, rows)
        if blockers:
            raise ValueError(" ".join(blockers))

        posted_entry_ids = sorted({
            row["journal_entry_id"] for row in rows
            if row["status"] == "Posted" and row["journal_entry_id"] is not None
        })
        if posted_entry_ids:
            placeholders = ", ".join("?" for _ in posted_entry_ids)
            cursor.execute(
                f"""SELECT MAX(entry_date) latest_entry_date
                    FROM journal_entries
                    WHERE client_id = ? AND id IN ({placeholders})""",
                [client_id, *posted_entry_ids],
            )
            latest_value = cursor.fetchone()["latest_entry_date"]
            latest_entry_date = date.fromisoformat(latest_value)
            if reversal_date < latest_entry_date:
                raise ValueError(
                    "The reversal date cannot be earlier than the latest posted "
                    f"entry in the batch ({latest_entry_date.isoformat()})."
                )

        if replacement_bank_account_id is not None:
            cursor.execute(
                """SELECT id, type, is_active FROM accounts
                   WHERE id = ? AND client_id = ?""",
                (replacement_bank_account_id, client_id),
            )
            replacement_account = cursor.fetchone()
            if not replacement_account:
                raise ValueError(
                    "The replacement bank or credit-card account does not belong "
                    "to the selected client."
                )
            if replacement_account["type"] not in ("Asset", "Liability"):
                raise ValueError(
                    "Choose an asset or liability account for the replacement import."
                )
            if not replacement_account["is_active"]:
                raise ValueError("The replacement account must be active.")

        cursor.execute(
            """INSERT INTO import_batch_reversals
               (client_id, original_batch, replacement_batch, reversal_date,
                reason, created_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (client_id, batch_id, replacement_batch, reversal_date.isoformat(),
             reason[:1000], current_actor()),
        )
        reversal_record_id = cursor.lastrowid
        replacement_ids = []
        reversed_postings = 0

        for row in rows:
            reversal_entry_id = None
            if row["status"] == "Posted":
                source_entry_id = row["journal_entry_id"]
                cursor.execute(
                    """SELECT * FROM journal_entries
                       WHERE id = ? AND client_id = ?""",
                    (source_entry_id, client_id),
                )
                source_entry = cursor.fetchone()
                cursor.execute(
                    """SELECT account_id, debit, credit
                       FROM journal_entry_lines
                       WHERE journal_entry_id = ? ORDER BY id""",
                    (source_entry_id,),
                )
                source_lines = cursor.fetchall()
                reversal = JournalEntry(
                    client_id=client_id,
                    entry_date=reversal_date,
                    description=(f"Import reversal: {source_entry['description']}"[:200]),
                    source_reference=f"Reversal of JE #{source_entry_id}",
                    entry_type="Regular",
                    lines=[
                        JournalEntryLine(
                            account_id=line["account_id"],
                            debit=to_dollars(line["credit"]),
                            credit=to_dollars(line["debit"]),
                            memo=f"Batch {batch_id}: {reason}"[:200],
                        )
                        for line in source_lines
                    ],
                )
                reversal.save(conn=conn)
                reversal_entry_id = reversal.id
                reversed_postings += 1
                AuditLog.write(
                    cursor, client_id, "journal_entries", source_entry_id, "REVERSE",
                    old_values={"reversed": False},
                    new_values={
                        "reversed": True,
                        "reversal_entry_id": reversal.id,
                        "reversal_date": reversal_date.isoformat(),
                        "import_batch": batch_id,
                        "replacement_batch": replacement_batch,
                        "reason": reason,
                    },
                )

            cursor.execute(
                """UPDATE imported_transactions
                   SET superseded_by_batch = ?, reversal_journal_entry_id = ?
                   WHERE id = ? AND client_id = ? AND superseded_by_batch IS NULL""",
                (replacement_batch, reversal_entry_id, row["id"], client_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("An import row changed during reversal. Nothing was changed.")
            # Record only the columns the UPDATE actually set: the stored
            # status column is untouched — "Reversed" is derived from
            # superseded_by_batch at read time (models/transaction.py).
            AuditLog.write(
                cursor, client_id, "imported_transactions", row["id"], "UPDATE",
                old_values={
                    "superseded_by_batch": None,
                    "reversal_journal_entry_id": None,
                },
                new_values={
                    "superseded_by_batch": replacement_batch,
                    "reversal_journal_entry_id": reversal_entry_id,
                    "reason": reason,
                },
            )

            identity = {
                "date": date.fromisoformat(row["transaction_date"]),
                "description": row["description"],
                "amount": to_dollars(row["amount"]),
                # A replacement has a new durable source identity, but retains
                # the original line number for display and stable retry behavior.
                "source_id": f"replacement:{replacement_batch}:{row['id']}",
                "source_row_number": (row["source_row_number"]
                                      if row["source_row_number"] is not None else 0),
            }
            replacement_bank_id = (
                replacement_bank_account_id
                if replacement_bank_account_id is not None
                else row["bank_account_id"]
            )
            ensure_import_identity(identity, client_id, replacement_bank_id)
            replacement = ImportedTransaction(
                client_id=client_id,
                import_batch=replacement_batch,
                transaction_date=identity["date"],
                description=row["description"],
                amount=identity["amount"],
                bank_account_id=replacement_bank_id,
                suggested_account_id=row["suggested_account_id"],
                status="Pending",
                source_id=identity["source_id"],
                source_filename=row["source_filename"],
                source_row_number=identity["source_row_number"],
                row_fingerprint=identity["row_fingerprint"],
                idempotency_key=identity["idempotency_key"],
                replaces_transaction_id=row["id"],
            )
            replacement.save(conn=conn)
            replacement_ids.append(replacement.id)

        AuditLog.write(
            cursor, client_id, "import_batch_reversals", reversal_record_id, "REVERSE",
            old_values={"original_batch": batch_id, "reversed": False},
            new_values={
                "original_batch": batch_id,
                "replacement_batch": replacement_batch,
                "reversal_date": reversal_date.isoformat(),
                "reason": reason,
                "row_count": len(rows),
                "reversed_postings": reversed_postings,
                "replacement_bank_account_id": replacement_bank_account_id,
            },
        )
        conn.commit()
        return BatchReversalResult(
            original_batch=batch_id,
            replacement_batch=replacement_batch,
            row_count=len(rows),
            reversed_postings=reversed_postings,
            replacement_transaction_ids=tuple(replacement_ids),
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
