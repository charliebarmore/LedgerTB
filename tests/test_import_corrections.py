from datetime import date

import pytest

from models.account import Account
from models.audit_log import AuditLog
from models.journal_entry import JournalEntry
from models.reconciliation import BankReconciliation
from models.transaction import ImportedTransaction
from services.import_corrections import correct_imported_category
from services.posting import post_transaction


def _account(client_id, number, name, account_type):
    account = Account(
        client_id=client_id,
        account_number=number,
        name=name,
        type=account_type,
    )
    account.save()
    return account.id


def _post(client_id, accounts, amount, target_account_id):
    return post_transaction(
        client_id=client_id,
        transaction={
            "date": date(2026, 1, 10),
            "description": "Imported merchant",
            "amount": amount,
            "source_id": f"source-{amount}",
            "source_row_number": 2,
        },
        target_account_id=target_account_id,
        bank_account_id=accounts["cash"],
        batch_id="correction-test",
        learn=False,
    )


def test_withdrawal_correction_reclassifies_without_touching_bank_leg(
    client_id, accounts
):
    travel = _account(client_id, "6100", "Travel Expense", "Expense")
    original, imported = _post(client_id, accounts, -75, accounts["expense"])

    correction = correct_imported_category(
        client_id=client_id,
        journal_entry_id=original.id,
        target_account_id=travel,
        correction_date=date(2026, 2, 1),
        reason="Merchant was client travel",
    )

    assert correction.source_reference == f"Correction of imported JE #{original.id}"
    # Regular, not Adjusting: a recategorization is routine bookkeeping and
    # must not clutter the worksheet's AJE column or take an AJE reference.
    assert correction.entry_type == "Regular"
    assert correction.aje_reference is None
    assert [(line.account_id, line.debit, line.credit) for line in correction.lines] == [
        (travel, 75, 0),
        (accounts["expense"], 0, 75),
    ]
    assert all(line.account_id != accounts["cash"] for line in correction.lines)
    assert Account.get_balance(accounts["cash"], client_id=client_id) == -75
    assert Account.get_balance(accounts["expense"], client_id=client_id) == 0
    assert Account.get_balance(travel, client_id=client_id) == 75

    link = ImportedTransaction.get_links_for_journal_entries(client_id, [original.id])[
        original.id
    ]
    assert link["journal_entry_id"] == original.id
    assert link["suggested_account_id"] == travel

    updates = [
        log
        for log in AuditLog.get_history("imported_transactions", imported.id)
        if log.action == "UPDATE"
    ]
    assert updates[0].new_values["correction_entry_id"] == correction.id
    assert updates[0].new_values["reason"] == "Merchant was client travel"


def test_deposit_correction_moves_credit_to_new_revenue(client_id, accounts):
    other_revenue = _account(client_id, "4100", "Other Service Revenue", "Revenue")
    original, _ = _post(client_id, accounts, 125, accounts["revenue"])

    correction = correct_imported_category(
        client_id=client_id,
        journal_entry_id=original.id,
        target_account_id=other_revenue,
        correction_date=date(2026, 2, 2),
        reason="Deposit belongs to the other service line",
    )

    assert [(line.account_id, line.debit, line.credit) for line in correction.lines] == [
        (accounts["revenue"], 125, 0),
        (other_revenue, 0, 125),
    ]
    assert Account.get_balance(accounts["cash"], client_id=client_id) == 125
    assert Account.get_balance(accounts["revenue"], client_id=client_id) == 0
    assert Account.get_balance(other_revenue, client_id=client_id) == 125


def test_imported_posting_cannot_be_edited_in_place(client_id, accounts):
    original, _ = _post(client_id, accounts, -20, accounts["expense"])
    saved = JournalEntry.get_by_id(original.id, client_id=client_id)
    saved.description = "silently changed"

    with pytest.raises(ValueError, match="Correct category"):
        saved.save()

    assert JournalEntry.get_by_id(original.id, client_id=client_id).description == (
        "Imported merchant"
    )


def test_correction_preserves_completed_bank_reconciliation(client_id, accounts):
    travel = _account(client_id, "6100", "Travel Expense", "Expense")
    original, _ = _post(client_id, accounts, -75, accounts["expense"])
    reconciliation = BankReconciliation.create(
        client_id,
        accounts["cash"],
        date(2026, 1, 1),
        date(2026, 1, 31),
        -75,
    )
    reconciliation.save_selected_lines(
        [line.line_id for line in reconciliation.lines()]
    )
    reconciliation.complete()

    correct_imported_category(
        client_id=client_id,
        journal_entry_id=original.id,
        target_account_id=travel,
        correction_date=date(2026, 2, 1),
        reason="Reclassify after reconciliation",
    )

    completed = BankReconciliation.get_by_id(reconciliation.id, client_id)
    assert completed.status == "Completed"
    assert completed.difference() == 0
    cleared = ImportedTransaction.get_all(client_id, cleared=True)
    assert [transaction.journal_entry_id for transaction in cleared] == [original.id]


def test_failed_correction_is_atomic(client_id, accounts):
    original, _ = _post(client_id, accounts, -20, accounts["expense"])

    with pytest.raises(ValueError, match="selected client"):
        correct_imported_category(
            client_id=client_id,
            journal_entry_id=original.id,
            target_account_id=999999,
            correction_date=date(2026, 2, 1),
            reason="Bad account should roll back",
        )

    assert JournalEntry.count(client_id) == 1
    link = ImportedTransaction.get_links_for_journal_entries(client_id, [original.id])[
        original.id
    ]
    assert link["suggested_account_id"] == accounts["expense"]
