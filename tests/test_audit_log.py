from datetime import date, datetime

from database.connection import get_connection
from models.account import Account
from models.audit_log import AuditLog
from models.client import Client


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
