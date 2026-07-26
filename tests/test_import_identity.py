from datetime import date

import pytest

from database.connection import get_connection
from models.audit_log import AuditLog
from models.journal_entry import JournalEntry
from models.transaction import ImportedTransaction
from services.import_identity import (
    canonical_description,
    classify_import_duplicates,
    hash_source,
    row_fingerprint,
)
from services.posting import post_transaction


def _row(source_row=2, description="Coffee Shop 123", amount=-12.34):
    return {
        "date": date(2026, 1, 5),
        "description": description,
        "amount": amount,
        "batch_id": "JAN26",
        "source_id": hash_source(b"january statement"),
        "source_filename": "january.csv",
        "source_row_number": source_row,
    }


def test_fingerprint_is_stable_for_harmless_text_variation(client_id, accounts):
    first = _row(description="  Coffee   Shop 123 ")
    second = _row(description="coffee shop 123")
    assert canonical_description(first["description"]) == "COFFEE SHOP 123"
    assert row_fingerprint(first, client_id, accounts["cash"]) == row_fingerprint(
        second, client_id, accounts["cash"]
    )
    second["amount"] = -12.35
    assert row_fingerprint(first, client_id, accounts["cash"]) != row_fingerprint(
        second, client_id, accounts["cash"]
    )


def test_classifies_duplicate_rows_within_one_upload(client_id, accounts):
    rows = [_row(2), _row(3)]
    for row in rows:
        row["bank_account_id"] = accounts["cash"]

    assert classify_import_duplicates(rows, client_id) == 1
    assert rows[0]["is_duplicate"] is False
    assert rows[1]["duplicate_kind"] == "within_upload"
    assert rows[1]["include"] is False
    assert rows[0]["row_fingerprint"] == rows[1]["row_fingerprint"]
    assert rows[0]["idempotency_key"] != rows[1]["idempotency_key"]


def test_historical_import_is_backfilled_and_detected(client_id, accounts):
    prior = ImportedTransaction(
        client_id=client_id, import_batch="legacy",
        transaction_date=date(2026, 1, 5), description="Coffee Shop 123",
        amount=-12.34, bank_account_id=accounts["cash"], status="Pending",
    )
    prior.save()
    candidate = _row()
    candidate["bank_account_id"] = accounts["cash"]

    assert classify_import_duplicates([candidate], client_id) == 1
    assert candidate["duplicate_kind"] == "previous_import"
    assert candidate["duplicate_info"]["transaction_id"] == prior.id
    conn = get_connection()
    try:
        stored = conn.execute(
            "SELECT row_fingerprint, idempotency_key FROM imported_transactions WHERE id = ?",
            (prior.id,),
        ).fetchone()
        assert stored["row_fingerprint"]
        assert stored["idempotency_key"]
    finally:
        conn.close()


def test_exact_source_row_retry_reuses_existing_journal_entry(client_id, accounts):
    transaction = _row()
    first_entry, first_import = post_transaction(
        client_id, transaction, accounts["expense"], accounts["cash"], batch_id="JAN26"
    )
    retry_entry, retry_import = post_transaction(
        client_id, dict(transaction), accounts["expense"], accounts["cash"], batch_id="JAN26"
    )

    assert retry_entry.id == first_entry.id
    assert retry_import.id == first_import.id
    assert len(JournalEntry.get_all(client_id)) == 1
    assert len(ImportedTransaction.get_by_status(client_id, "Posted")) == 1


def test_override_cannot_double_post_the_same_source_row(client_id, accounts):
    """The override is a judgement call about two similar rows, never a licence
    to import one row twice. Now that ticking it needs no reason, this is the
    property that keeps a re-run of the same file from duplicating the books.
    """
    transaction = _row()
    first_entry, first_import = post_transaction(
        client_id, transaction, accounts["expense"], accounts["cash"], batch_id="JAN26"
    )
    retry_entry, retry_import = post_transaction(
        client_id, dict(transaction), accounts["expense"], accounts["cash"],
        batch_id="JAN26", duplicate_override=True,
    )

    assert retry_entry.id == first_entry.id
    assert retry_import.id == first_import.id
    assert len(JournalEntry.get_all(client_id)) == 1


def test_duplicate_is_blocked_until_overridden_and_the_override_is_audited(client_id, accounts):
    post_transaction(
        client_id, _row(2), accounts["expense"], accounts["cash"], batch_id="JAN26"
    )
    duplicate = _row(3)
    with pytest.raises(ValueError, match="matches a previously imported row"):
        post_transaction(
            client_id, duplicate, accounts["expense"], accounts["cash"], batch_id="JAN26"
        )

    _, imported = post_transaction(
        client_id, duplicate, accounts["expense"], accounts["cash"], batch_id="JAN26",
        duplicate_override=True,
        duplicate_override_reason="Two separate purchases on the statement",
    )
    assert imported.duplicate_override is True
    assert imported.duplicate_of_id is not None
    override = next(
        log for log in AuditLog.get_history("imported_transactions", imported.id)
        if log.action == "OVERRIDE"
    )
    assert override.new_values["reason"] == "Two separate purchases on the statement"
    assert len(JournalEntry.get_all(client_id)) == 2


def test_override_needs_no_reason(client_id, accounts):
    """A statement can legitimately repeat an identical charge; requiring prose
    to import it meant inventing text. The checkbox alone is the decision."""
    post_transaction(
        client_id, _row(2), accounts["expense"], accounts["cash"], batch_id="JAN26"
    )
    _, imported = post_transaction(
        client_id, _row(3), accounts["expense"], accounts["cash"], batch_id="JAN26",
        duplicate_override=True,
    )

    assert imported.duplicate_override is True
    assert imported.duplicate_override_reason is None
    assert len(JournalEntry.get_all(client_id)) == 2


def test_override_without_a_reason_is_still_audited(client_id, accounts):
    """The OVERRIDE event is what makes the choice reviewable, so it must be
    written whether or not a reason was typed."""
    post_transaction(
        client_id, _row(2), accounts["expense"], accounts["cash"], batch_id="JAN26"
    )
    _, imported = post_transaction(
        client_id, _row(3), accounts["expense"], accounts["cash"], batch_id="JAN26",
        duplicate_override=True,
    )

    override = next(
        log for log in AuditLog.get_history("imported_transactions", imported.id)
        if log.action == "OVERRIDE"
    )
    assert override.new_values["reason"] is None
    assert override.new_values["duplicate_of_id"] is not None
    assert override.new_values["source_row_number"] == 3


def test_blank_and_whitespace_reasons_normalize_to_none(client_id, accounts):
    post_transaction(
        client_id, _row(2), accounts["expense"], accounts["cash"], batch_id="JAN26"
    )
    _, imported = post_transaction(
        client_id, _row(3), accounts["expense"], accounts["cash"], batch_id="JAN26",
        duplicate_override=True, duplicate_override_reason="   ",
    )

    assert imported.duplicate_override_reason is None


def test_three_identical_charges_can_all_be_imported(client_id, accounts):
    """The case that prompted this: the AMEX statement has three identical
    CWR Digital charges on one day, all real."""
    post_transaction(
        client_id, _row(2, description="CWR DIGITAL LLC", amount=-79.00),
        accounts["expense"], accounts["cash"], batch_id="AMEX",
    )
    for source_row in (3, 4):
        post_transaction(
            client_id, _row(source_row, description="CWR DIGITAL LLC", amount=-79.00),
            accounts["expense"], accounts["cash"], batch_id="AMEX",
            duplicate_override=True,
        )

    assert len(JournalEntry.get_all(client_id)) == 3
    assert len(ImportedTransaction.get_by_status(client_id, "Posted")) == 3


def test_database_rejects_duplicate_idempotency_key(client_id, accounts):
    first = ImportedTransaction(
        client_id=client_id, transaction_date=date(2026, 1, 1), description="One",
        amount=1, bank_account_id=accounts["cash"], idempotency_key="same-key",
    )
    first.save()
    second = ImportedTransaction(
        client_id=client_id, transaction_date=date(2026, 1, 2), description="Two",
        amount=2, bank_account_id=accounts["cash"], idempotency_key="same-key",
    )
    with pytest.raises(Exception, match="UNIQUE constraint"):
        second.save()
