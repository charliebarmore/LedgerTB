"""Safe reclassification of a journal entry created from an imported row."""

from datetime import date

from database.connection import get_connection
from models.audit_log import AuditLog
from models.journal_entry import JournalEntry, JournalEntryLine
from money import to_dollars


def correct_imported_category(
    *,
    client_id: int,
    journal_entry_id: int,
    target_account_id: int,
    correction_date: date,
    reason: str,
) -> JournalEntry:
    """Post an atomic reclassification without changing the imported bank leg.

    The source journal entry and its reconciliation linkage remain untouched.
    A new two-line entry moves the amount from the currently recorded category
    to the selected category, while the imported row records the new effective
    category and an audit event links the correction entry and reason.
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("A correction reason is required.")
    if not correction_date:
        raise ValueError("A correction date is required.")

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT it.*, old_account.name AS old_account_name,
                   new_account.name AS new_account_name,
                   new_account.is_active AS new_account_active
            FROM imported_transactions it
            LEFT JOIN accounts old_account
              ON old_account.id = it.suggested_account_id
             AND old_account.client_id = it.client_id
            LEFT JOIN accounts new_account
              ON new_account.id = ? AND new_account.client_id = it.client_id
            WHERE it.client_id = ? AND it.journal_entry_id = ?
            """,
            (target_account_id, client_id, journal_entry_id),
        )
        linked_rows = cursor.fetchall()
        if not linked_rows:
            raise ValueError("This journal entry is not linked to an imported transaction.")
        if len(linked_rows) != 1:
            raise ValueError(
                "This journal entry has multiple imported source rows and cannot be "
                "reclassified automatically."
            )
        imported = linked_rows[0]
        if imported["status"] != "Posted":
            raise ValueError("Only posted imported transactions can be corrected.")
        if imported["new_account_name"] is None:
            raise ValueError("The correction account must belong to the selected client.")
        if not imported["new_account_active"]:
            raise ValueError("The correction account must be active.")
        if target_account_id == imported["bank_account_id"]:
            raise ValueError("The correction account cannot be the imported bank account.")

        old_account_id = imported["suggested_account_id"]
        if old_account_id is None:
            cursor.execute(
                """
                SELECT DISTINCT account_id
                FROM journal_entry_lines
                WHERE journal_entry_id = ? AND account_id != ?
                """,
                (journal_entry_id, imported["bank_account_id"]),
            )
            candidates = [row["account_id"] for row in cursor.fetchall()]
            if len(candidates) != 1:
                raise ValueError("The current category could not be determined safely.")
            old_account_id = candidates[0]
        if target_account_id == old_account_id:
            raise ValueError("Choose a different category for the correction.")

        cursor.execute(
            "SELECT name FROM accounts WHERE id = ? AND client_id = ?",
            (old_account_id, client_id),
        )
        old_account = cursor.fetchone()
        if not old_account:
            raise ValueError("The currently recorded category no longer exists.")

        amount = to_dollars(imported["amount"])
        if amount == 0:
            raise ValueError("A zero-amount imported transaction cannot be reclassified.")
        value = abs(amount)
        memo = reason[:200]
        if amount < 0:
            # The original withdrawal debited its category. Move that debit.
            lines = [
                JournalEntryLine(
                    account_id=target_account_id, debit=value, credit=0, memo=memo
                ),
                JournalEntryLine(
                    account_id=old_account_id, debit=0, credit=value, memo=memo
                ),
            ]
        else:
            # The original deposit credited its category. Move that credit.
            lines = [
                JournalEntryLine(
                    account_id=old_account_id, debit=value, credit=0, memo=memo
                ),
                JournalEntryLine(
                    account_id=target_account_id, debit=0, credit=value, memo=memo
                ),
            ]

        # Regular, not Adjusting: a recategorization is routine bookkeeping.
        # The AJE column is reserved for deliberate period-end adjustments,
        # and the audit event below already documents the correction fully.
        correction = JournalEntry(
            client_id=client_id,
            entry_date=correction_date,
            description=f"Category correction: {imported['description']}"[:200],
            source_reference=f"Correction of imported JE #{journal_entry_id}",
            entry_type="Regular",
            lines=lines,
        )
        correction.save(conn=conn)

        cursor.execute(
            """
            UPDATE imported_transactions
            SET suggested_account_id = ?
            WHERE id = ? AND client_id = ?
            """,
            (target_account_id, imported["id"], client_id),
        )
        AuditLog.write(
            cursor,
            client_id,
            "imported_transactions",
            imported["id"],
            "UPDATE",
            old_values={
                "suggested_account_id": old_account_id,
                "journal_entry_id": journal_entry_id,
            },
            new_values={
                "suggested_account_id": target_account_id,
                "journal_entry_id": journal_entry_id,
                "correction_entry_id": correction.id,
                "correction_date": correction_date.isoformat(),
                "reason": reason,
            },
        )
        conn.commit()
        return correction
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
