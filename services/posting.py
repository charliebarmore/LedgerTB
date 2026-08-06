"""Posting service: turn a categorized bank transaction into a balanced,
double-entry journal entry and persist it atomically.

This is the accounting core of the import workflow. It lives here (not in a
Streamlit page) so the debit/credit/transfer logic is a single tested function
and so the journal entry, its import record, and the learned pattern all commit
or roll back together — no orphaned journal entries on partial failure.
"""

from typing import Optional, Tuple

from database.connection import get_connection
from models.audit_log import AuditLog
from models.journal_entry import JournalEntry, JournalEntryLine
from models.transaction import ImportedTransaction
from money import to_dollars
from services.import_identity import ensure_import_identity


def build_journal_entry(
    client_id: int,
    transaction: dict,
    target_account_id: int,
    bank_account_id: int,
    is_transfer: bool = False,
    entry_type: str = "Regular",
    source_reference: Optional[str] = None,
) -> JournalEntry:
    """Build a balanced two-line JournalEntry from a bank transaction.

    Sign convention: ``transaction['amount']`` is negative for money leaving the
    bank account (payment/expense/transfer-out) and positive for money entering
    it (deposit/revenue/transfer-in). The bank account is always one leg; the
    ``target_account_id`` (expense/revenue, or the other account for a transfer)
    is the other. The result is not persisted — call ``post_transaction`` for
    that, or ``.save()`` the returned entry.
    """
    amount = transaction["amount"]
    description = transaction.get("description", "") or ""
    transfer_memo = f"Transfer: {description[:90]}"
    plain_memo = description[:100]
    target_memo = transfer_memo if is_transfer else plain_memo

    lines = []
    if amount < 0:
        # Money leaving the bank account.
        #   Expense:      Debit expense,     Credit bank
        #   Transfer out: Debit destination, Credit bank  (e.g. CC payment)
        lines.append(JournalEntryLine(
            account_id=target_account_id,
            debit=abs(amount),
            credit=0,
            memo=target_memo,
        ))
        lines.append(JournalEntryLine(
            account_id=bank_account_id,
            debit=0,
            credit=abs(amount),
        ))
    else:
        # Money entering the bank account.
        #   Deposit:     Debit bank, Credit revenue
        #   Transfer in: Debit bank, Credit source account
        lines.append(JournalEntryLine(
            account_id=bank_account_id,
            debit=amount,
            credit=0,
        ))
        lines.append(JournalEntryLine(
            account_id=target_account_id,
            debit=0,
            credit=amount,
            memo=target_memo,
        ))

    entry_description = (
        f"[Transfer] {description[:180]}" if is_transfer else description[:200]
    )

    return JournalEntry(
        client_id=client_id,
        entry_date=transaction["date"],
        description=entry_description,
        source_reference=source_reference,
        entry_type=entry_type,
        lines=lines,
    )


def post_transaction(
    client_id: int,
    transaction: dict,
    target_account_id: int,
    bank_account_id: int,
    is_transfer: bool = False,
    batch_id: Optional[str] = None,
    learn: bool = True,
    duplicate_override: bool = False,
    duplicate_override_reason: Optional[str] = None,
) -> Tuple[JournalEntry, ImportedTransaction]:
    """Post one categorized bank transaction as a balanced journal entry.

    The journal entry (+ lines), the ``imported_transactions`` record, and the
    learned categorization pattern are written on ONE connection inside ONE
    transaction: they all commit together or, on any error, all roll back — so a
    failure can never leave an orphaned journal entry with no import record.

    Returns the persisted ``(JournalEntry, ImportedTransaction)``. Raises on
    validation failure, a closed fiscal period, or any DB error (nothing is
    committed in that case).
    """
    ensure_import_identity(transaction, client_id, bank_account_id)
    # Ticking the override is the decision; a written reason is optional context.
    # The OVERRIDE audit event is what makes the choice reviewable afterwards, and
    # it is recorded either way — requiring prose only meant a statement with two
    # genuinely identical charges could not be imported without inventing text.
    # An exact re-import of the same source row is still refused outright below;
    # that is double-counting, not a judgement call.
    override_reason = (duplicate_override_reason or "").strip() or None

    entry = build_journal_entry(
        client_id=client_id,
        transaction=transaction,
        target_account_id=target_account_id,
        bank_account_id=bank_account_id,
        is_transfer=is_transfer,
        source_reference=f"Import batch {batch_id}" if batch_id else None,
    )

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM imported_transactions
            WHERE client_id = ? AND idempotency_key = ?
            """,
            (client_id, transaction["idempotency_key"]),
        )
        existing = cursor.fetchone()
        staged_row_id = None
        if existing and existing["dismissed_at"] is not None:
            raise ValueError(
                "This staged transaction was dismissed and can no longer be posted."
            )
        if existing and existing["journal_entry_id"] is None and existing["status"] == "Pending":
            # An assistant-staged row awaiting review: this post IS its
            # completion, not a retry — adopt the record instead of refusing.
            staged_row_id = existing["id"]
            existing = None
        if existing:
            existing_entry = JournalEntry.get_by_id(existing["journal_entry_id"], client_id=client_id)
            if existing_entry is None:
                raise ValueError("The prior import exists but its journal entry could not be found.")
            return existing_entry, ImportedTransaction(
                id=existing["id"], client_id=existing["client_id"],
                import_batch=existing["import_batch"],
                transaction_date=transaction["date"], description=existing["description"],
                amount=to_dollars(existing["amount"]),
                bank_account_id=existing["bank_account_id"],
                suggested_account_id=existing["suggested_account_id"],
                status=existing["status"], journal_entry_id=existing["journal_entry_id"],
                source_id=existing["source_id"], source_filename=existing["source_filename"],
                source_row_number=existing["source_row_number"],
                row_fingerprint=existing["row_fingerprint"],
                idempotency_key=existing["idempotency_key"],
                duplicate_override=bool(existing["duplicate_override"]),
                duplicate_override_reason=existing["duplicate_override_reason"],
                duplicate_of_id=existing["duplicate_of_id"],
            )

        cursor.execute(
            """
            SELECT id, journal_entry_id FROM imported_transactions
            WHERE client_id = ? AND row_fingerprint = ?
              AND journal_entry_id IS NOT NULL
            ORDER BY id LIMIT 1
            """,
            (client_id, transaction["row_fingerprint"]),
        )
        duplicate = cursor.fetchone()
        if duplicate and not duplicate_override:
            raise ValueError(
                "This transaction matches a previously imported row. "
                "Review it and provide an override reason to post it again."
            )

        entry.save(conn=conn)

        imported_txn = ImportedTransaction(
            id=staged_row_id,
            client_id=client_id,
            import_batch=batch_id,
            transaction_date=transaction["date"],
            description=(transaction.get("description", "") or "")[:200],
            amount=transaction["amount"],
            bank_account_id=bank_account_id,
            suggested_account_id=target_account_id,
            status="Posted",
            journal_entry_id=entry.id,
            source_id=transaction.get("source_id"),
            source_filename=transaction.get("source_filename"),
            source_row_number=transaction.get("source_row_number"),
            row_fingerprint=transaction["row_fingerprint"],
            idempotency_key=transaction["idempotency_key"],
            duplicate_override=duplicate_override,
            duplicate_override_reason=override_reason or None,
            duplicate_of_id=duplicate["id"] if duplicate else None,
        )
        imported_txn.save(conn=conn)

        if duplicate_override:
            AuditLog.write(
                cursor, client_id, "imported_transactions", imported_txn.id, "OVERRIDE",
                new_values={
                    "reason": override_reason,
                    "duplicate_of_id": imported_txn.duplicate_of_id,
                    "duplicate_of_journal_entry_id": duplicate["journal_entry_id"] if duplicate else None,
                    "source_filename": imported_txn.source_filename,
                    "source_row_number": imported_txn.source_row_number,
                    "row_fingerprint": imported_txn.row_fingerprint,
                },
            )

        # Transfers are not expense/revenue patterns, so we don't learn from them.
        if learn and not is_transfer:
            from services.pattern_learning import PatternLearner
            PatternLearner.learn_pattern(
                client_id, transaction.get("description", ""), target_account_id, conn=conn
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return entry, imported_txn
