from datetime import date

import pytest

from models.account import Account
from models.client import Client
from models.journal_entry import JournalEntry
from models.reconciliation import BankReconciliation
from models.transaction import ImportedTransaction
from tests.conftest import post_entry


def _bank_line_ids(reconciliation):
    return [line.line_id for line in reconciliation.lines()]


def test_asset_reconciliation_clears_gl_lines_and_completes(client_id, accounts):
    opening = post_entry(
        client_id, date(2025, 12, 31),
        [(accounts["cash"], 1000, 0), (accounts["equity"], 0, 1000)],
        entry_type="Beginning Balance",
    )
    purchase = post_entry(
        client_id, date(2026, 1, 10),
        [(accounts["expense"], 100, 0), (accounts["cash"], 0, 100)],
    )
    ImportedTransaction(
        client_id=client_id, transaction_date=date(2026, 1, 10),
        description="Purchase", amount=-100, bank_account_id=accounts["cash"],
        suggested_account_id=accounts["expense"], status="Posted",
        journal_entry_id=purchase.id,
    ).save()

    reconciliation = BankReconciliation.create(
        client_id, accounts["cash"], date(2026, 1, 1), date(2026, 1, 31), 900
    )
    assert reconciliation.ledger_balance() == 900
    assert reconciliation.cleared_balance() == 0
    reconciliation.save_selected_lines(_bank_line_ids(reconciliation))
    assert reconciliation.cleared_balance() == 900
    assert reconciliation.difference() == 0
    reconciliation.complete()

    completed = BankReconciliation.get_by_id(reconciliation.id, client_id)
    assert completed.status == "Completed"
    imported = ImportedTransaction.get_all(client_id, cleared=True)
    assert len(imported) == 1
    assert imported[0].is_cleared
    assert imported[0].statement_end_date == date(2026, 1, 31)

    opening.lines = JournalEntry.get_by_id(opening.id, client_id).lines
    with pytest.raises(ValueError, match="selected in a bank reconciliation"):
        opening.save()


def test_completion_requires_exact_statement_balance(client_id, accounts):
    post_entry(
        client_id, date(2026, 1, 5),
        [(accounts["cash"], 250, 0), (accounts["revenue"], 0, 250)],
    )
    reconciliation = BankReconciliation.create(
        client_id, accounts["cash"], date(2026, 1, 1), date(2026, 1, 31), 249.99
    )
    reconciliation.save_selected_lines(_bank_line_ids(reconciliation))
    assert reconciliation.difference() == -0.01
    with pytest.raises(ValueError, match="out of balance"):
        reconciliation.complete()
    assert BankReconciliation.get_by_id(reconciliation.id, client_id).status == "Draft"


def test_liability_reconciliation_uses_credit_normal_balance(client_id, accounts):
    post_entry(
        client_id, date(2025, 12, 31),
        [(accounts["equity"], 500, 0), (accounts["credit_card"], 0, 500)],
        entry_type="Beginning Balance",
    )
    post_entry(
        client_id, date(2026, 1, 12),
        [(accounts["expense"], 50, 0), (accounts["credit_card"], 0, 50)],
    )
    reconciliation = BankReconciliation.create(
        client_id, accounts["credit_card"], date(2026, 1, 1), date(2026, 1, 31), 550
    )
    reconciliation.save_selected_lines(_bank_line_ids(reconciliation))
    assert reconciliation.ledger_balance() == 550
    assert reconciliation.difference() == 0


def test_reconciliation_enforces_client_account_and_period_boundaries(db):
    client_a = Client(name="A").save(seed_accounts=False)
    client_b = Client(name="B").save(seed_accounts=False)
    cash_b = Account(client_id=client_b, account_number="1000", name="Cash", type="Asset")
    cash_b.save()

    with pytest.raises(ValueError, match="does not belong"):
        BankReconciliation.create(
            client_a, cash_b.id, date(2026, 1, 1), date(2026, 1, 31), 0
        )
    with pytest.raises(ValueError, match="cannot be after"):
        BankReconciliation.create(
            client_b, cash_b.id, date(2026, 2, 1), date(2026, 1, 31), 0
        )

    first = BankReconciliation.create(
        client_b, cash_b.id, date(2026, 1, 1), date(2026, 1, 31), 0
    )
    with pytest.raises(ValueError, match="already has a draft"):
        BankReconciliation.create(
            client_b, cash_b.id, date(2026, 2, 1), date(2026, 2, 28), 0
        )
    first.complete()
    with pytest.raises(ValueError, match="overlaps"):
        BankReconciliation.create(
            client_b, cash_b.id, date(2026, 1, 15), date(2026, 2, 15), 0
        )


def test_reopen_only_latest_completed_reconciliation(client_id, accounts):
    january = BankReconciliation.create(
        client_id, accounts["cash"], date(2026, 1, 1), date(2026, 1, 31), 0
    )
    january.complete()
    february = BankReconciliation.create(
        client_id, accounts["cash"], date(2026, 2, 1), date(2026, 2, 28), 0
    )
    february.complete()
    with pytest.raises(ValueError, match="newer"):
        january.reopen()
    february.reopen()
    assert BankReconciliation.get_by_id(february.id, client_id).status == "Draft"
