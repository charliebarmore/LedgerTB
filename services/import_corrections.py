"""Safe reclassification of a journal entry created from an imported row."""

import json
from datetime import date

from database.connection import get_connection
from models.audit_log import AuditLog
from models.journal_entry import JournalEntry, JournalEntryLine
from money import to_dollars


def import_history_links(cursor, client_id):
    """Map immutable ledger history to its imported source using audit links.

    Existing corrections already record structured correction_entry_id values.
    REVERSE events preserve the relationship even if display text changes.
    """
    rows = cursor.execute(
        "SELECT id, journal_entry_id, reversal_journal_entry_id FROM imported_transactions "
        "WHERE client_id = ?", (client_id,)
    ).fetchall()
    links = {}
    for row in rows:
        for key in ('journal_entry_id', 'reversal_journal_entry_id'):
            if row[key] is not None:
                links[row[key]] = row['id']
    events = cursor.execute(
        "SELECT table_name, record_id, action, new_values FROM audit_log "
        "WHERE client_id = ? AND action IN ('UPDATE', 'REVERSE') ORDER BY id",
        (client_id,)
    ).fetchall()
    reversals = []
    for event in events:
        values = json.loads(event['new_values'] or '{}')
        if event['table_name'] == 'imported_transactions':
            correction_id = values.get('correction_entry_id')
            if correction_id:
                links[correction_id] = event['record_id']
        elif event['table_name'] == 'journal_entries' and event['action'] == 'REVERSE':
            if values.get('reversal_entry_id'):
                reversals.append((event['record_id'], values['reversal_entry_id']))
    # Human-approved assistant corrections have a durable draft relationship.
    # Include corrections of corrections as well as reversals of either kind.
    reversals.extend(
        (row['original_entry_id'], row['posted_entry_id'])
        for row in cursor.execute(
            "SELECT original_entry_id, posted_entry_id FROM draft_entries "
            "WHERE client_id = ? AND status = 'approved' "
            "AND original_entry_id IS NOT NULL AND posted_entry_id IS NOT NULL",
            (client_id,)
        ).fetchall()
    )
    # Resolve transitive relationships independently of creation/audit ordering.
    while reversals:
        pending = []
        for original, reversal in reversals:
            if original in links:
                links[reversal] = links[original]
            else:
                pending.append((original, reversal))
        if len(pending) == len(reversals):
            break
        reversals = pending
    return links


def effective_import_category(cursor, imported, links):
    """Recover the category from the complete, cent-exact ledger chain.

    Refuse legacy edited/ambiguous history rather than guessing a category.
    """
    ids = [entry_id for entry_id, source_id in links.items()
           if source_id == imported['id']]
    amounts = dict(cursor.execute(
        'SELECT l.account_id, SUM(l.debit - l.credit) FROM journal_entry_lines l '
        'JOIN journal_entries e ON e.id = l.journal_entry_id '
        'WHERE e.client_id = ? AND e.id IN (' + ','.join('?' for _ in ids) + ') '
        'GROUP BY l.account_id', [imported['client_id'], *ids]
    ).fetchall())
    bank = amounts.pop(imported['bank_account_id'], 0)
    categories = {account: amount for account, amount in amounts.items() if amount}
    if bank != imported['amount'] or len(categories) != 1:
        raise ValueError('This import was reversed or its correction history needs review. '
                         'Review the linked entries before changing its category.')
    account_id, amount = next(iter(categories.items()))
    if amount != -imported['amount']:
        raise ValueError('The imported category could not be determined safely.')
    return account_id


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
        cursor.execute("BEGIN IMMEDIATE")
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
        if (imported["status"] != "Posted" or imported["superseded_by_batch"]
                or imported["reversal_journal_entry_id"]):
            raise ValueError("Only posted imported transactions can be corrected.")
        if imported["new_account_name"] is None:
            raise ValueError("The correction account must belong to the selected client.")
        if not imported["new_account_active"]:
            raise ValueError("The correction account must be active.")
        if target_account_id == imported["bank_account_id"]:
            raise ValueError("The correction account cannot be the imported bank account.")

        old_account_id = effective_import_category(
            cursor, imported, import_history_links(cursor, client_id)
        )
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
