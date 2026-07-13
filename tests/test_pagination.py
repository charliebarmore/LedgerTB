from datetime import date, datetime

from database.connection import get_connection
from models.audit_log import AuditLog
from models.journal_entry import JournalEntry
from models.transaction import ImportedTransaction
from tests.conftest import post_entry


def test_transaction_pages_and_summary_cover_full_filtered_set(client_id, accounts):
    amounts = [100.00, -25.00, 50.00, -10.00, -5.00, 20.00]
    transactions = [
        ImportedTransaction(
            client_id=client_id,
            import_batch="page-test",
            transaction_date=date(2026, 1, day + 1),
            description=f"Transaction {day + 1}",
            amount=amount,
            bank_account_id=accounts["cash"],
            status="Posted" if day < 4 else "Pending",
        )
        for day, amount in enumerate(amounts)
    ]
    ImportedTransaction.bulk_insert(transactions)

    first = ImportedTransaction.get_all(client_id, limit=2, offset=0)
    second = ImportedTransaction.get_all(client_id, limit=2, offset=2)
    assert len(first) == len(second) == 2
    assert {t.id for t in first}.isdisjoint(t.id for t in second)

    summary = ImportedTransaction.get_filtered_summary(client_id)
    assert summary == {
        "total_count": 6,
        "total_deposits": 170.0,
        "total_withdrawals": -40.0,
        "posted_count": 4,
        "pending_count": 2,
    }
    posted = ImportedTransaction.get_filtered_summary(client_id, status="Posted")
    assert posted["total_count"] == 4
    assert posted["total_deposits"] == 150.0
    assert posted["total_withdrawals"] == -35.0


def test_journal_pages_and_sql_totals_cover_full_filter(client_id, accounts):
    for day, amount in enumerate((10, 20, 30, 40, 50), start=1):
        post_entry(
            client_id, date(2026, 2, day),
            [(accounts["cash"], amount, 0), (accounts["revenue"], 0, amount)],
        )

    first = JournalEntry.get_all(client_id, limit=2, offset=0)
    third = JournalEntry.get_all(client_id, limit=2, offset=4)
    assert len(first) == 2
    assert len(third) == 1
    assert first[0].entry_date == date(2026, 2, 5)
    assert third[0].entry_date == date(2026, 2, 1)
    assert all(len(entry.lines) == 2 for entry in first + third)

    summary = JournalEntry.get_filtered_summary(
        client_id, date(2026, 2, 2), date(2026, 2, 4), "Regular"
    )
    assert summary["total_count"] == 3
    assert summary["total_debits"] == 90.0
    assert summary["total_credits"] == 90.0
    assert summary["regular_count"] == 3


def test_audit_pages_and_counts_use_complete_filtered_result(client_id):
    conn = get_connection()
    conn.execute("DELETE FROM audit_log WHERE client_id = ?", (client_id,))
    conn.commit()
    conn.close()

    actions = ["INSERT", "UPDATE", "DELETE", "EXPORT", "UPDATE", "CLOSE"]
    for index, action in enumerate(actions):
        AuditLog.log_change(
            client_id, "page_test", index + 1, action,
            new_values={"sequence": index + 1},
        )

    start = datetime(2020, 1, 1)
    end = datetime(2030, 1, 1)
    first = AuditLog.get_all(client_id, start, end, limit=2, offset=0)
    second = AuditLog.get_all(client_id, start, end, limit=2, offset=2)
    assert [log.action for log in first] == ["CLOSE", "UPDATE"]
    assert [log.action for log in second] == ["EXPORT", "DELETE"]

    counts = AuditLog.get_filtered_counts(client_id, start, end, table_name="page_test")
    assert counts == {
        "total": 6, "inserts": 1, "updates": 2, "deletes": 1, "events": 2,
    }
