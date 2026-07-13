import json
from datetime import date, datetime

import pytest

from database.connection import get_connection
from models.account import Account
from models.audit_log import AuditLog
from models.client import Client
from models.fiscal_period import FiscalPeriod
from models.journal_entry import JournalEntry
from services.posting import post_transaction
from services.pattern_learning import PatternLearner
from tests.conftest import post_entry


def _clear_audit(client_id):
    """Client/account creation now writes audit rows; clear them so tests that
    assert on specific audit contents start from a clean slate."""
    conn = get_connection()
    conn.execute("DELETE FROM audit_log WHERE client_id = ?", (client_id,))
    conn.commit()
    conn.close()


def insert_log(client_id, changed_at, action="INSERT"):
    """Insert an audit_log row with an explicit SQLite-format timestamp,
    matching what CURRENT_TIMESTAMP actually produces ("YYYY-MM-DD HH:MM:SS")."""
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO audit_log (client_id, table_name, record_id, action, changed_at)
        VALUES (?, 'journal_entries', 1, ?, ?)
        """,
        (client_id, action, changed_at),
    )
    conn.commit()
    conn.close()


def test_get_all_finds_same_day_entries(client_id):
    """Regression test: datetime.isoformat() uses a 'T' separator, which
    sorts after SQLite's space-separated CURRENT_TIMESTAMP format and used
    to silently exclude every same-day row from the filtered range."""
    _clear_audit(client_id)
    insert_log(client_id, "2026-01-24 21:30:27")

    start = datetime.combine(date(2026, 1, 24), datetime.min.time())
    end = datetime.combine(date(2026, 1, 24), datetime.max.time())

    logs = AuditLog.get_all(client_id=client_id, start_date=start, end_date=end)
    assert len(logs) == 1


def test_get_all_excludes_entries_outside_range(client_id):
    _clear_audit(client_id)
    insert_log(client_id, "2026-01-24 21:30:27")

    start = datetime.combine(date(2026, 2, 1), datetime.min.time())
    end = datetime.combine(date(2026, 2, 28), datetime.max.time())

    logs = AuditLog.get_all(client_id=client_id, start_date=start, end_date=end)
    assert len(logs) == 0


def test_get_earliest_date(client_id):
    _clear_audit(client_id)
    assert AuditLog.get_earliest_date(client_id) is None

    insert_log(client_id, "2026-03-15 09:00:00")
    insert_log(client_id, "2026-01-24 21:30:27")
    insert_log(client_id, "2026-06-01 12:00:00")

    earliest = AuditLog.get_earliest_date(client_id)
    assert earliest == datetime(2026, 1, 24, 21, 30, 27)


# ---- M3: broadened audit coverage + safe (logged, non-fatal) failures ----

def _audit_rows(client_id, table_name):
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT action, record_id FROM audit_log WHERE client_id = ? AND table_name = ? "
            "ORDER BY id",
            (client_id, table_name),
        ).fetchall()
    finally:
        conn.close()


def test_client_creation_is_audited(client_id):
    rows = _audit_rows(client_id, "clients")
    assert any(r["action"] == "INSERT" and r["record_id"] == client_id for r in rows)


def test_account_create_and_update_are_audited(client_id):
    acct = Account(client_id=client_id, account_number="6100", name="Dues", type="Expense")
    acct.save()
    acct.name = "Dues & Subscriptions"
    acct.save()

    actions = [r["action"] for r in _audit_rows(client_id, "accounts") if r["record_id"] == acct.id]
    assert actions == ["INSERT", "UPDATE"]


def test_log_change_safe_swallows_and_logs_failure(monkeypatch, caplog):
    """A failing audit write must not raise (the caller's op must survive), but
    it must be logged, not silently discarded."""
    def boom(**kwargs):
        raise RuntimeError("audit table unavailable")

    monkeypatch.setattr(AuditLog, "log_change", boom)

    import logging
    with caplog.at_level(logging.WARNING):
        result = AuditLog.log_change_safe(
            client_id=1, table_name="accounts", record_id=1, action="INSERT",
        )
    assert result is None                     # swallowed, did not raise
    assert any("Audit log write failed" in rec.message for rec in caplog.records)


def test_audit_failure_rolls_back_business_mutation(client_id, monkeypatch):
    """The shared writer is part of the mutation transaction, not a follow-up."""
    def fail_audit(*args, **kwargs):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(AuditLog, "write", staticmethod(fail_audit))
    account = Account(
        client_id=client_id, account_number="6199", name="Should Roll Back", type="Expense"
    )
    with pytest.raises(RuntimeError, match="audit failure"):
        account.save()

    conn = get_connection()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM accounts WHERE client_id = ? AND account_number = '6199'",
            (client_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_client_pii_audit_is_comprehensive_but_masks_tax_id(client_id):
    client = Client.get_by_id(client_id)
    client.tax_id = "12-3456789"
    client.contact_email = "owner@example.com"
    client.address_line1 = "10 Main Street"
    client.save(seed_accounts=False)

    history = AuditLog.get_history("clients", client_id)
    update = next(log for log in history if log.action == "UPDATE")
    assert update.new_values["contact_email"] == "owner@example.com"
    assert update.new_values["address_line1"] == "10 Main Street"
    assert update.new_values["tax_id_present"] is True
    assert update.new_values["tax_id_last4"] == "6789"
    assert "12-3456789" not in json.dumps(update.new_values)


def test_period_close_and_reopen_are_explicitly_audited(client_id):
    period = FiscalPeriod(
        client_id=client_id, period_name="FY 2026", period_type="Year",
        start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
    )
    period.save()
    FiscalPeriod.set_closed(
        period.id, True, client_id,
        confirmation={"explicit_confirmation": True, "warnings_acknowledged": True},
    )
    FiscalPeriod.set_closed(period.id, False, client_id)

    actions = [log.action for log in AuditLog.get_history("fiscal_periods", period.id)]
    assert actions == ["REOPEN", "CLOSE", "INSERT"]


def test_import_posting_audits_every_persisted_part(client_id, accounts):
    entry, imported = post_transaction(
        client_id=client_id,
        transaction={
            "amount": -42.50, "description": "OFFICE SUPPLY STORE",
            "date": date(2026, 1, 10), "batch_id": "audit-batch",
        },
        target_account_id=accounts["expense"], bank_account_id=accounts["cash"],
        batch_id="audit-batch",
    )
    assert any(log.action == "INSERT" for log in AuditLog.get_history("journal_entries", entry.id))
    assert any(
        log.action == "INSERT"
        for log in AuditLog.get_history("imported_transactions", imported.id)
    )
    rules = AuditLog.get_all(client_id, table_name="categorization_rules")
    assert any(log.action == "INSERT" for log in rules)


def test_reversal_preserves_original_and_records_both_events(client_id, accounts):
    original = post_entry(
        client_id, date(2026, 2, 1),
        [(accounts["cash"], 125, 0), (accounts["revenue"], 0, 125)],
    )
    reversal = JournalEntry.reverse(original.id, client_id, date(2026, 2, 2))

    assert JournalEntry.get_by_id(original.id, client_id) is not None
    assert reversal.lines[0].debit == 0 and reversal.lines[0].credit == 125
    assert reversal.lines[1].debit == 125 and reversal.lines[1].credit == 0
    assert any(
        log.action == "REVERSE"
        and log.new_values["reversal_entry_id"] == reversal.id
        for log in AuditLog.get_history("journal_entries", original.id)
    )
    assert any(
        log.action == "INSERT"
        for log in AuditLog.get_history("journal_entries", reversal.id)
    )
    with pytest.raises(ValueError, match="already reversed"):
        JournalEntry.reverse(original.id, client_id, date(2026, 2, 3))


def test_rule_update_and_delete_are_client_scoped_and_audited(client_id, accounts):
    PatternLearner.learn_pattern(client_id, "MONTHLY SOFTWARE BILL", accounts["expense"])
    rule = PatternLearner.get_all_rules(client_id)[0]
    PatternLearner.update_rule(rule["id"], accounts["revenue"], client_id)
    PatternLearner.delete_rule(rule["id"], client_id)

    actions = [
        log.action for log in AuditLog.get_history("categorization_rules", rule["id"])
    ]
    assert actions == ["DELETE", "UPDATE", "INSERT"]


def test_operational_event_actions_are_supported(client_id):
    log_id = AuditLog.log_event(
        client_id, "EXPORT", "transactions_export",
        {"format": "csv", "row_count": 12},
    )
    event = AuditLog.get_by_id(log_id)
    assert event.action == "EXPORT"
    assert event.new_values == {"format": "csv", "row_count": 12}
