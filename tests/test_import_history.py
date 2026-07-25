"""Import History: batch summaries and source-order drill-down.

These back the Import History view, whose whole job is answering "what did I
already upload, and did all of it land?" — so the tests care most about counts
and totals being faithful to what was imported.
"""
from datetime import date

from models.transaction import ImportedTransaction
from services.posting import post_transaction


def _txn(batch, row_number, description, amount, day):
    return {
        "date": date(2026, 1, day),
        "description": description,
        "amount": amount,
        "batch_id": batch,
        "source_filename": f"{batch}.csv",
        "source_row_number": row_number,
    }


def _post(client_id, accounts, batch, row_number, description, amount, day,
          bank="cash", target="expense"):
    return post_transaction(
        client_id,
        _txn(batch, row_number, description, amount, day),
        target_account_id=accounts[target],
        bank_account_id=accounts[bank],
        batch_id=batch,
        learn=False,
    )


def test_no_batches_for_a_client_with_no_imports(client_id, accounts):
    assert ImportedTransaction.get_batch_summaries(client_id) == []


def test_batch_summary_reports_counts_and_totals(client_id, accounts):
    _post(client_id, accounts, "AMEX", 2, "CANVA", -15.00, 7)
    _post(client_id, accounts, "AMEX", 3, "OBSIDIAN", -5.00, 8)
    _post(client_id, accounts, "AMEX", 4, "CASH REWARD", 1.81, 9)

    summaries = ImportedTransaction.get_batch_summaries(client_id)
    assert len(summaries) == 1
    batch = summaries[0]

    assert batch["import_batch"] == "AMEX"
    assert batch["row_count"] == 3
    assert batch["source_filename"] == "AMEX.csv"
    assert batch["account_name"] == "Cash"
    # Net is what reconciles against a statement; deposits/withdrawals split it.
    assert batch["net_amount"] == -18.19
    assert batch["deposits"] == 1.81
    assert batch["withdrawals"] == -20.00
    assert batch["first_date"] == date(2026, 1, 7)
    assert batch["last_date"] == date(2026, 1, 9)


def test_posted_and_pending_rows_are_counted_separately(client_id, accounts):
    """A half-finished batch must not look complete."""
    _post(client_id, accounts, "MIXED", 2, "POSTED ONE", -10.00, 5)

    pending = ImportedTransaction(
        client_id=client_id,
        import_batch="MIXED",
        transaction_date=date(2026, 1, 6),
        description="STILL PENDING",
        amount=-20.00,
        bank_account_id=accounts["cash"],
        status="Pending",
        source_filename="MIXED.csv",
        source_row_number=3,
    )
    pending.save()

    batch = ImportedTransaction.get_batch_summaries(client_id)[0]
    assert batch["row_count"] == 2
    assert batch["posted_count"] == 1
    assert batch["pending_count"] == 1
    # The total covers every row, posted or not — it mirrors the source file.
    assert batch["net_amount"] == -30.00


def test_each_batch_is_summarized_separately(client_id, accounts):
    _post(client_id, accounts, "RELAY", 2, "GO DADDY", -26.18, 12)
    _post(client_id, accounts, "AMEX", 2, "CANVA", -15.00, 7)

    summaries = ImportedTransaction.get_batch_summaries(client_id)
    assert {b["import_batch"] for b in summaries} == {"RELAY", "AMEX"}
    assert all(b["row_count"] == 1 for b in summaries)


def test_batch_spanning_two_accounts_is_flagged(client_id, accounts):
    """A multi-account CSV must not silently report just one account."""
    _post(client_id, accounts, "MULTI", 2, "ONE", -10.00, 5, bank="cash")
    _post(client_id, accounts, "MULTI", 3, "TWO", -20.00, 6, bank="credit_card")

    batch = ImportedTransaction.get_batch_summaries(client_id)[0]
    assert batch["account_count"] == 2
    assert batch["row_count"] == 2


def test_batches_are_isolated_per_client(client_id, accounts):
    from models.client import Client

    _post(client_id, accounts, "AMEX", 2, "CANVA", -15.00, 7)
    other = Client(name="Other Co", entity_type="S-Corp",
                   fiscal_year_end_month=12).save(seed_accounts=False)

    assert ImportedTransaction.get_batch_summaries(other) == []
    assert len(ImportedTransaction.get_batch_summaries(client_id)) == 1


def test_drill_down_returns_rows_in_source_file_order(client_id, accounts):
    """Row order must mirror the uploaded file so a line-by-line check reads."""
    _post(client_id, accounts, "AMEX", 4, "THIRD", -3.00, 9)
    _post(client_id, accounts, "AMEX", 2, "FIRST", -1.00, 7)
    _post(client_id, accounts, "AMEX", 3, "SECOND", -2.00, 8)

    rows = ImportedTransaction.get_by_batch(client_id, "AMEX")
    assert [r.description for r in rows] == ["FIRST", "SECOND", "THIRD"]
    assert [r.source_row_number for r in rows] == [2, 3, 4]


def test_drill_down_carries_posting_status_and_entry_link(client_id, accounts):
    entry, _ = _post(client_id, accounts, "AMEX", 2, "CANVA", -15.00, 7)

    row = ImportedTransaction.get_by_batch(client_id, "AMEX")[0]
    assert row.status == "Posted"
    assert row.journal_entry_id == entry.id
    assert row.amount == -15.00
    assert row.bank_account_name == "Cash"


def test_drill_down_sorts_rows_without_a_source_row_number_last(client_id, accounts):
    """Rows imported before source_row_number existed must not break ordering."""
    legacy = ImportedTransaction(
        client_id=client_id,
        import_batch="AMEX",
        transaction_date=date(2026, 1, 1),
        description="LEGACY ROW",
        amount=-9.00,
        bank_account_id=accounts["cash"],
        status="Pending",
    )
    legacy.save()
    _post(client_id, accounts, "AMEX", 2, "NUMBERED", -1.00, 7)

    rows = ImportedTransaction.get_by_batch(client_id, "AMEX")
    assert [r.description for r in rows] == ["NUMBERED", "LEGACY ROW"]


def test_drill_down_of_unknown_batch_is_empty(client_id, accounts):
    _post(client_id, accounts, "AMEX", 2, "CANVA", -15.00, 7)
    assert ImportedTransaction.get_by_batch(client_id, "NOPE") == []
