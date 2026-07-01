from datetime import date, datetime

from database.connection import get_connection
from models.audit_log import AuditLog


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
    insert_log(client_id, "2026-01-24 21:30:27")

    start = datetime.combine(date(2026, 1, 24), datetime.min.time())
    end = datetime.combine(date(2026, 1, 24), datetime.max.time())

    logs = AuditLog.get_all(client_id=client_id, start_date=start, end_date=end)
    assert len(logs) == 1


def test_get_all_excludes_entries_outside_range(client_id):
    insert_log(client_id, "2026-01-24 21:30:27")

    start = datetime.combine(date(2026, 2, 1), datetime.min.time())
    end = datetime.combine(date(2026, 2, 28), datetime.max.time())

    logs = AuditLog.get_all(client_id=client_id, start_date=start, end_date=end)
    assert len(logs) == 0


def test_get_earliest_date(client_id):
    assert AuditLog.get_earliest_date(client_id) is None

    insert_log(client_id, "2026-03-15 09:00:00")
    insert_log(client_id, "2026-01-24 21:30:27")
    insert_log(client_id, "2026-06-01 12:00:00")

    earliest = AuditLog.get_earliest_date(client_id)
    assert earliest == datetime(2026, 1, 24, 21, 30, 27)
