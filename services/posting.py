"""Posting service: turn a categorized bank transaction into a balanced,
double-entry journal entry and persist it atomically.

This is the accounting core of the import workflow. It lives here (not in a
Streamlit page) so the debit/credit/transfer logic is a single tested function
and so the journal entry, its import record, and the learned pattern all commit
or roll back together — no orphaned journal entries on partial failure.
"""

from typing import Optional, Tuple

from database.connection import get_connection
from models.journal_entry import JournalEntry, JournalEntryLine
from models.transaction import ImportedTransaction


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
        entry.save(conn=conn)

        imported_txn = ImportedTransaction(
            client_id=client_id,
            import_batch=batch_id,
            transaction_date=transaction["date"],
            description=(transaction.get("description", "") or "")[:200],
            amount=transaction["amount"],
            bank_account_id=bank_account_id,
            suggested_account_id=target_account_id,
            status="Posted",
            journal_entry_id=entry.id,
        )
        imported_txn.save(conn=conn)

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

    # Best-effort audit AFTER the atomic write commits — mirrors the audit
    # behavior of a standalone JournalEntry.save() and never fails the post.
    from models.audit_log import AuditLog
    AuditLog.log_change_safe(
        client_id=client_id,
        table_name="journal_entries",
        record_id=entry.id,
        action="INSERT",
        new_values={
            "entry_date": entry.entry_date.isoformat(),
            "description": entry.description,
            "source_reference": entry.source_reference,
            "entry_type": entry.entry_type,
            "total_debits": entry.total_debits(),
            "total_credits": entry.total_credits(),
        },
    )

    return entry, imported_txn
