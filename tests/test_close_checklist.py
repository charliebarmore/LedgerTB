from datetime import date

import pytest

from database.connection import get_connection
from models.audit_log import AuditLog
from models.fiscal_period import FiscalPeriod
from models.transaction import ImportedTransaction


def _period(client_id):
    period = FiscalPeriod(
        client_id=client_id, period_name="FY 2026", period_type="Year",
        start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
    )
    period.save()
    return period


def test_year_close_requires_explicit_confirmation(client_id):
    period = _period(client_id)
    with pytest.raises(ValueError, match="Explicit year-close confirmation"):
        FiscalPeriod.set_closed(period.id, True, client_id)
    assert FiscalPeriod.get_by_id(period.id).is_closed is False


def test_close_checklist_requires_warning_ack_and_is_audited(client_id, accounts):
    period = _period(client_id)
    duplicate_rows = [
        ImportedTransaction(
            client_id=client_id, import_batch="same-upload",
            transaction_date=date(2026, 1, 15), description="SAME MERCHANT",
            amount=-25.0, bank_account_id=accounts["cash"], status="Pending",
        )
        for _ in range(2)
    ]
    ImportedTransaction.bulk_insert(duplicate_rows)

    checklist = FiscalPeriod.get_close_checklist(period.id, client_id)
    assert checklist["trial_balance_balanced"] is True
    assert checklist["pending_imports"] == 2
    assert checklist["uncategorized_items"] == 2
    assert checklist["unresolved_duplicates"] == 1

    with pytest.raises(ValueError, match="acknowledge"):
        FiscalPeriod.set_closed(
            period.id, True, client_id,
            confirmation={"explicit_confirmation": True, "warnings_acknowledged": False},
        )

    FiscalPeriod.set_closed(
        period.id, True, client_id,
        confirmation={"explicit_confirmation": True, "warnings_acknowledged": True},
    )
    close_event = next(
        log for log in AuditLog.get_history("fiscal_periods", period.id)
        if log.action == "CLOSE"
    )
    assert close_event.new_values["close_checklist"]["pending_imports"] == 2
    assert close_event.new_values["warnings_acknowledged"] is True


def test_unbalanced_trial_balance_is_a_hard_close_block(client_id, accounts):
    period = _period(client_id)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO journal_entries (client_id, entry_date, description, entry_type)
        VALUES (?, '2026-03-01', 'corrupt entry', 'Regular')
        """,
        (client_id,),
    )
    entry_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO journal_entry_lines (journal_entry_id, account_id, debit, credit)
        VALUES (?, ?, 10000, 0)
        """,
        (entry_id, accounts["cash"]),
    )
    conn.commit()
    conn.close()

    checklist = FiscalPeriod.get_close_checklist(period.id, client_id)
    assert checklist["trial_balance_balanced"] is False
    assert checklist["total_debits"] == 100.0
    assert checklist["total_credits"] == 0.0
    with pytest.raises(ValueError, match="out of balance"):
        FiscalPeriod.set_closed(
            period.id, True, client_id,
            confirmation={"explicit_confirmation": True, "warnings_acknowledged": True},
        )
    assert FiscalPeriod.get_by_id(period.id).is_closed is False
