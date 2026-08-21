from datetime import date

import pytest

from database.connection import get_connection
from models.account import Account
from models.audit_log import AuditLog
from models.fiscal_period import FiscalPeriod
from models.journal_entry import JournalEntry
from models.reconciliation import BankReconciliation
from models.reports import ReportGenerator
from models.transaction import ImportedTransaction
from services.import_batch_reversal import (
    preview_import_batch_reversal,
    reverse_import_batch,
)
from services.import_corrections import correct_imported_category
from services.import_identity import classify_import_duplicates
from services.posting import post_transaction


def _transaction(batch, row, description, amount, day):
    return {
        "date": date(2026, 1, day),
        "description": description,
        "amount": amount,
        "batch_id": batch,
        "source_id": f"source-{batch}",
        "source_filename": f"{batch}.csv",
        "source_row_number": row,
    }


def _post(client_id, accounts, batch, row, description, amount, day):
    return post_transaction(
        client_id=client_id,
        transaction=_transaction(batch, row, description, amount, day),
        target_account_id=(accounts["expense"] if amount < 0 else accounts["revenue"]),
        bank_account_id=accounts["cash"],
        batch_id=batch,
        learn=False,
    )


def test_reversal_preserves_history_and_stages_a_linked_replacement(
    client_id, accounts
):
    first_entry, first_import = _post(
        client_id, accounts, "JAN", 2, "Office supplies", -40, 5
    )
    second_entry, second_import = _post(
        client_id, accounts, "JAN", 3, "Customer receipt", 125, 6
    )

    preview = preview_import_batch_reversal(client_id, "JAN")
    assert preview.can_reverse
    assert (preview.row_count, preview.posted_count, preview.net_amount) == (2, 2, 85)

    result = reverse_import_batch(
        client_id=client_id,
        batch_id="JAN",
        reversal_date=date(2026, 2, 1),
        reason="Imported to the wrong bank account",
    )

    assert result.row_count == 2
    assert result.reversed_postings == 2
    assert JournalEntry.count(client_id) == 4
    assert Account.get_balance(accounts["cash"], client_id=client_id) == 0
    assert Account.get_balance(accounts["expense"], client_id=client_id) == 0
    assert Account.get_balance(accounts["revenue"], client_id=client_id) == 0

    originals = ImportedTransaction.get_by_batch(client_id, "JAN")
    assert [row.status for row in originals] == ["Reversed", "Reversed"]
    assert {row.journal_entry_id for row in originals} == {first_entry.id, second_entry.id}
    assert all(row.superseded_by_batch == result.replacement_batch for row in originals)
    assert all(row.reversal_journal_entry_id for row in originals)

    replacements = ImportedTransaction.get_by_batch(client_id, result.replacement_batch)
    assert [row.status for row in replacements] == ["Pending", "Pending"]
    assert [row.replaces_transaction_id for row in replacements] == [
        first_import.id, second_import.id
    ]
    assert all(row.journal_entry_id is None for row in replacements)
    assert ImportedTransaction.get_pending_count(client_id) == 2
    assert not preview_import_batch_reversal(client_id, "JAN").can_reverse

    batch_events = AuditLog.get_history("import_batch_reversals", 1)
    assert batch_events[0].action == "REVERSE"
    assert batch_events[0].new_values["reason"] == "Imported to the wrong bank account"


def test_replacement_does_not_match_the_original_but_still_detects_other_duplicates(
    client_id, accounts
):
    _post(client_id, accounts, "JAN", 2, "Repeated charge", -25, 5)
    # A genuinely separate historical copy means ignoring the linked original
    # must not disable all duplicate protection.
    post_transaction(
        client_id=client_id,
        transaction=_transaction("DEC", 2, "Repeated charge", -25, 5),
        target_account_id=accounts["expense"], bank_account_id=accounts["cash"],
        batch_id="DEC", learn=False, duplicate_override=True,
    )
    result = reverse_import_batch(
        client_id=client_id, batch_id="JAN", reversal_date=date(2026, 2, 1),
        reason="Redo import",
    )
    replacement = ImportedTransaction.get_by_batch(
        client_id, result.replacement_batch
    )[0]
    row = {
        "staged_id": replacement.id,
        "date": replacement.transaction_date,
        "description": replacement.description,
        "amount": replacement.amount,
        "bank_account_id": replacement.bank_account_id,
        "source_id": replacement.source_id,
        "source_row_number": replacement.source_row_number,
        "idempotency_key": replacement.idempotency_key,
        "row_fingerprint": replacement.row_fingerprint,
        "replaces_transaction_id": replacement.replaces_transaction_id,
    }
    assert classify_import_duplicates(
        [row], client_id, exclude_ids={replacement.id}
    ) == 1


def test_replacement_can_be_reposted_without_duplicate_override(client_id, accounts):
    _post(client_id, accounts, "JAN", 2, "Office supplies", -40, 5)
    result = reverse_import_batch(
        client_id=client_id, batch_id="JAN", reversal_date=date(2026, 2, 1),
        reason="Redo import",
    )
    replacement = ImportedTransaction.get_by_batch(
        client_id, result.replacement_batch
    )[0]
    entry, posted = post_transaction(
        client_id=client_id,
        transaction={
            "date": replacement.transaction_date,
            "description": replacement.description,
            "amount": replacement.amount,
            "source_id": replacement.source_id,
            "source_filename": replacement.source_filename,
            "source_row_number": replacement.source_row_number,
            "row_fingerprint": replacement.row_fingerprint,
            "idempotency_key": replacement.idempotency_key,
            "replaces_transaction_id": replacement.replaces_transaction_id,
        },
        target_account_id=accounts["expense"],
        bank_account_id=accounts["cash"],
        batch_id=result.replacement_batch,
        learn=False,
    )
    assert posted.id == replacement.id
    assert posted.replaces_transaction_id is not None
    assert posted.duplicate_override is False
    assert entry.id is not None
    assert Account.get_balance(accounts["cash"], client_id=client_id) == -40

    ledger = ReportGenerator.general_ledger(
        accounts["cash"], date(2026, 1, 1), date(2026, 2, 28), client_id
    )
    assert [row.import_correction_role for row in ledger] == [
        "original", "replacement", "reversal"
    ]
    assert ledger[0].import_correction_label == (
        f"Original import — reversed by JE #{ledger[2].entry_id}"
    )
    assert ledger[1].import_correction_label == (
        f"Replacement import for JE #{ledger[0].entry_id}"
    )
    assert ledger[2].import_correction_label == (
        f"Import reversal of JE #{ledger[0].entry_id}"
    )

    compact, hidden_count = ReportGenerator.compact_reversed_import_entries(
        ledger, "Asset"
    )
    assert hidden_count == 2
    assert [row.import_correction_role for row in compact] == ["replacement"]
    assert compact[-1].balance == ledger[-1].balance == -40

    exported = ReportGenerator.general_ledger_to_dataframe(ledger)
    assert len(exported) == 3
    assert list(exported["Import Correction"]) == [
        row.import_correction_label for row in ledger
    ]

    # If the selected period contains only one side, it must remain visible so
    # period activity and the running balance cannot be misread.
    february = ReportGenerator.general_ledger(
        accounts["cash"], date(2026, 2, 1), date(2026, 2, 28), client_id
    )
    february_compact, hidden_count = (
        ReportGenerator.compact_reversed_import_entries(february, "Asset")
    )
    assert hidden_count == 0
    assert february_compact == february


def test_a_replacement_batch_can_itself_be_reversed_and_reposted(client_id, accounts):
    _post(client_id, accounts, "JAN", 2, "Office supplies", -40, 5)
    first_redo = reverse_import_batch(
        client_id=client_id, batch_id="JAN", reversal_date=date(2026, 2, 1),
        reason="First redo",
    )
    staged = ImportedTransaction.get_by_batch(
        client_id, first_redo.replacement_batch
    )[0]
    post_transaction(
        client_id=client_id,
        transaction={
            "date": staged.transaction_date, "description": staged.description,
            "amount": staged.amount, "source_id": staged.source_id,
            "source_row_number": staged.source_row_number,
        },
        target_account_id=accounts["expense"], bank_account_id=accounts["cash"],
        batch_id=first_redo.replacement_batch, learn=False,
    )
    second_redo = reverse_import_batch(
        client_id=client_id, batch_id=first_redo.replacement_batch,
        reversal_date=date(2026, 3, 1), reason="Second redo",
    )
    staged_again = ImportedTransaction.get_by_batch(
        client_id, second_redo.replacement_batch
    )[0]
    row = {
        "date": staged_again.transaction_date,
        "description": staged_again.description,
        "amount": staged_again.amount,
        "bank_account_id": staged_again.bank_account_id,
        "source_id": staged_again.source_id,
        "source_row_number": staged_again.source_row_number,
        "replaces_transaction_id": staged_again.replaces_transaction_id,
    }
    assert classify_import_duplicates(
        [row], client_id, exclude_ids={staged_again.id}
    ) == 0

    ledger = ReportGenerator.general_ledger(
        accounts["cash"], date(2026, 1, 1), date(2026, 3, 31), client_id
    )
    replaced_twice = next(
        item for item in ledger
        if item.replacement_for_entry_id and item.reversed_by_entry_id
    )
    assert "Replacement import" in replaced_twice.import_correction_label
    assert "later reversed" in replaced_twice.import_correction_label
    compact, hidden_count = ReportGenerator.compact_reversed_import_entries(
        ledger, "Asset"
    )
    assert hidden_count == 4
    assert compact == []


def test_mixed_posted_and_pending_batch_is_rebuilt_in_full(client_id, accounts):
    _post(client_id, accounts, "MIXED", 2, "Posted", -10, 5)
    pending = ImportedTransaction(
        client_id=client_id, import_batch="MIXED",
        transaction_date=date(2026, 1, 6), description="Pending", amount=-20,
        bank_account_id=accounts["cash"], status="Pending",
        source_id="source-MIXED", source_filename="MIXED.csv", source_row_number=3,
    )
    pending.save()

    result = reverse_import_batch(
        client_id=client_id, batch_id="MIXED", reversal_date=date(2026, 2, 1),
        reason="Start review over",
    )
    assert result.reversed_postings == 1
    assert len(ImportedTransaction.get_by_batch(client_id, result.replacement_batch)) == 2
    assert ImportedTransaction.get_pending_count(client_id) == 2
    assert ImportedTransaction.get_by_status(client_id, "Pending")
    assert pending.id not in {row.id for row in ImportedTransaction.get_by_status(
        client_id, "Pending"
    )}


def test_replacement_can_move_to_the_correct_bank_account(client_id, accounts):
    _post(client_id, accounts, "JAN", 2, "Office supplies", -40, 5)
    result = reverse_import_batch(
        client_id=client_id, batch_id="JAN", reversal_date=date(2026, 2, 1),
        reason="Imported to the wrong card",
        replacement_bank_account_id=accounts["credit_card"],
    )
    replacement = ImportedTransaction.get_by_batch(
        client_id, result.replacement_batch
    )[0]
    assert replacement.bank_account_id == accounts["credit_card"]
    assert replacement.row_fingerprint is not None

    with pytest.raises(ValueError, match="asset or liability"):
        reverse_import_batch(
            client_id=client_id, batch_id=result.replacement_batch,
            reversal_date=date(2026, 2, 2), reason="Invalid target",
            replacement_bank_account_id=accounts["expense"],
        )


def test_reconciled_or_corrected_batches_are_blocked(client_id, accounts):
    original, _ = _post(client_id, accounts, "JAN", 2, "Purchase", -75, 10)
    reconciliation = BankReconciliation.create(
        client_id, accounts["cash"], date(2026, 1, 1), date(2026, 1, 31), -75
    )
    reconciliation.save_selected_lines(
        [line.line_id for line in reconciliation.lines()]
    )
    preview = preview_import_batch_reversal(client_id, "JAN")
    assert any("bank reconciliation" in blocker for blocker in preview.blockers)
    with pytest.raises(ValueError, match="bank reconciliation"):
        reverse_import_batch(
            client_id=client_id, batch_id="JAN", reversal_date=date(2026, 2, 1),
            reason="Blocked",
        )

    # Separate batch: a durable category correction is also a blocker.
    travel = Account(
        client_id=client_id, account_number="6100", name="Travel", type="Expense"
    )
    travel.save()
    corrected, _ = _post(client_id, accounts, "FEB", 2, "Hotel", -90, 12)
    correction = correct_imported_category(
        client_id=client_id, journal_entry_id=corrected.id,
        target_account_id=travel.id, correction_date=date(2026, 2, 2),
        reason="Travel expense",
    )
    preview = preview_import_batch_reversal(client_id, "FEB")
    assert any("category correction" in blocker for blocker in preview.blockers)
    JournalEntry.reverse(correction.id, client_id, date(2026, 2, 3))
    assert not any(
        "category correction" in blocker
        for blocker in preview_import_batch_reversal(client_id, "FEB").blockers
    )
    assert JournalEntry.get_by_id(original.id, client_id) is not None


def test_closed_reversal_period_and_mid_operation_failure_roll_back(
    client_id, accounts, monkeypatch
):
    _post(client_id, accounts, "JAN", 2, "First", -10, 5)
    _post(client_id, accounts, "JAN", 3, "Second", -20, 6)
    FiscalPeriod(
        client_id=client_id, period_name="February 2026", period_type="Year",
        start_date=date(2026, 2, 1), end_date=date(2026, 2, 28), is_closed=True,
    ).save()
    with pytest.raises(ValueError, match="is closed"):
        reverse_import_batch(
            client_id=client_id, batch_id="JAN", reversal_date=date(2026, 2, 15),
            reason="Closed date",
        )
    assert JournalEntry.count(client_id) == 2
    assert all(row.status == "Posted" for row in ImportedTransaction.get_by_batch(
        client_id, "JAN"
    ))

    original_save = JournalEntry.save
    calls = 0

    def fail_second(entry, conn=None):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic failure")
        return original_save(entry, conn=conn)

    monkeypatch.setattr(JournalEntry, "save", fail_second)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        reverse_import_batch(
            client_id=client_id, batch_id="JAN", reversal_date=date(2026, 3, 1),
            reason="Atomic failure test",
        )
    assert JournalEntry.count(client_id) == 2
    assert all(row.status == "Posted" for row in ImportedTransaction.get_by_batch(
        client_id, "JAN"
    ))
    conn = get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM import_batch_reversals").fetchone()[0] == 0
    finally:
        conn.close()
