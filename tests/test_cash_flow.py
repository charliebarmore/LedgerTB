from datetime import date

import pytest

from constants import AccountSubtype
from models.account import Account
from models.reports import ReportGenerator
from tests.conftest import post_entry


START = date(2026, 1, 1)
END = date(2026, 12, 31)


def _account(client_id, number, name, account_type, subtype):
    account = Account(
        client_id=client_id,
        account_number=number,
        name=name,
        type=account_type,
        subtype=subtype,
    )
    account.save()
    return account.id


def test_indirect_cash_flow_ties_with_working_capital_investing_and_financing(
    client_id, accounts
):
    retained = _account(
        client_id, "3900", "Retained Earnings", "Equity",
        AccountSubtype.RETAINED_EARNINGS,
    )
    distribution = _account(
        client_id, "3100", "Owner Distribution", "Equity",
        AccountSubtype.OWNER_DISTRIBUTION,
    )
    receivable = _account(
        client_id, "1100", "Accounts Receivable", "Asset",
        AccountSubtype.ACCOUNTS_RECEIVABLE,
    )
    payable = _account(
        client_id, "2000A", "Accounts Payable", "Liability",
        AccountSubtype.ACCOUNTS_PAYABLE,
    )
    revenue = _account(
        client_id, "4100", "Service Revenue", "Revenue",
        AccountSubtype.OPERATING_REVENUE,
    )
    expense = _account(
        client_id, "6100", "Operating Expense", "Expense",
        AccountSubtype.OPERATING_EXPENSE,
    )
    depreciation = _account(
        client_id, "7000", "Depreciation Expense", "Expense",
        AccountSubtype.DEPRECIATION_AMORTIZATION,
    )
    equipment = _account(
        client_id, "1500", "Equipment", "Asset",
        AccountSubtype.FIXED_ASSET,
    )
    accumulated = _account(
        client_id, "1510", "Accumulated Depreciation", "Asset",
        AccountSubtype.ACCUMULATED_DEPRECIATION,
    )

    post_entry(client_id, START, [
        (accounts["cash"], 1000, 0), (retained, 0, 1000),
    ], entry_type="Beginning Balance")
    post_entry(client_id, date(2026, 2, 1), [
        (receivable, 500, 0), (revenue, 0, 500),
    ])
    post_entry(client_id, date(2026, 2, 15), [
        (accounts["cash"], 400, 0), (receivable, 0, 400),
    ])
    post_entry(client_id, date(2026, 3, 1), [
        (expense, 200, 0), (payable, 0, 200),
    ])
    post_entry(client_id, date(2026, 3, 15), [
        (payable, 150, 0), (accounts["cash"], 0, 150),
    ])
    post_entry(client_id, date(2026, 4, 1), [
        (equipment, 300, 0), (accounts["cash"], 0, 300),
    ])
    post_entry(client_id, date(2026, 6, 30), [
        (depreciation, 50, 0), (accumulated, 0, 50),
    ])
    post_entry(client_id, date(2026, 7, 1), [
        (distribution, 100, 0), (accounts["cash"], 0, 100),
    ])

    report = ReportGenerator.cash_flow_statement(client_id, START, END)
    operating = {line["key"]: line for line in report["operating"]["lines"]}

    assert report["cash_beginning"] == 1000
    assert report["cash_ending"] == 850
    assert report["actual_cash_change"] == -150
    assert operating["net_income"]["amount"] == 250
    assert operating["depreciation_amortization"]["amount"] == 50
    assert operating["accounts_receivable"]["amount"] == -100
    assert operating["accounts_payable"]["amount"] == 50
    assert "unresolved_operating_reconciliation" not in operating
    assert report["operating"]["total"] == 250
    assert report["investing"]["total"] == -300
    assert report["financing"]["total"] == -100
    assert report["ties"] is True
    assert report["operating_reconciled"] is True
    assert report["classification_complete"] is True
    assert report["ready"] is True


def test_cash_flow_ignores_closing_entry_when_calculating_period_profit(
    client_id, accounts
):
    revenue = _account(
        client_id, "4100", "Service Revenue", "Revenue",
        AccountSubtype.OPERATING_REVENUE,
    )
    retained = _account(
        client_id, "3900", "Retained Earnings", "Equity",
        AccountSubtype.RETAINED_EARNINGS,
    )
    post_entry(client_id, date(2026, 6, 1), [
        (accounts["cash"], 200, 0), (revenue, 0, 200),
    ])
    post_entry(client_id, END, [
        (revenue, 200, 0), (retained, 0, 200),
    ], entry_type="Closing")

    report = ReportGenerator.cash_flow_statement(client_id, START, END)
    income = ReportGenerator.income_statement(client_id, START, END)
    net_income = report["operating"]["lines"][0]
    assert net_income == {"key": "net_income", "name": "Net Income", "amount": 200}
    assert report["operating"]["total"] == 200
    assert report["noncash_items"] == []
    assert report["ready"] is True
    assert income["net_income"] == net_income["amount"] == 200


def test_mixed_debt_and_interest_payment_splits_by_exact_counterpart_amount(
    client_id, accounts
):
    retained = _account(
        client_id, "3900", "Retained Earnings", "Equity",
        AccountSubtype.RETAINED_EARNINGS,
    )
    debt = _account(
        client_id, "2500", "Term Loan", "Liability",
        AccountSubtype.LONG_TERM_LIABILITY,
    )
    interest = _account(
        client_id, "7100", "Interest Expense", "Expense",
        AccountSubtype.OTHER_EXPENSE,
    )
    post_entry(client_id, START, [
        (accounts["cash"], 500, 0), (retained, 0, 500),
    ], entry_type="Beginning Balance")
    post_entry(client_id, date(2026, 5, 1), [
        (debt, 80, 0), (interest, 20, 0), (accounts["cash"], 0, 100),
    ])

    report = ReportGenerator.cash_flow_statement(client_id, START, END)
    assert report["ties"] is True
    assert report["operating"]["total"] == -20
    assert report["financing"]["total"] == -80
    assert report["unclassified"]["entries"] == []
    assert report["classification_complete"] is True
    assert report["operating_reconciled"] is True
    assert report["ready"] is True


def test_midperiod_beginning_balance_establishes_opening_cash(
    client_id, accounts
):
    retained = _account(
        client_id, "3900", "Retained Earnings", "Equity",
        AccountSubtype.RETAINED_EARNINGS,
    )
    revenue = _account(
        client_id, "4100", "Service Revenue", "Revenue",
        AccountSubtype.OPERATING_REVENUE,
    )
    post_entry(client_id, date(2026, 3, 15), [
        (accounts["cash"], 500, 0), (retained, 0, 500),
    ], entry_type="Beginning Balance")
    post_entry(client_id, date(2026, 3, 20), [
        (accounts["cash"], 100, 0), (revenue, 0, 100),
    ])

    report = ReportGenerator.cash_flow_statement(
        client_id, date(2026, 3, 1), date(2026, 3, 31)
    )
    assert report["cash_beginning"] == 500
    assert report["cash_ending"] == 600
    assert report["actual_cash_change"] == 100
    assert report["operating"]["total"] == 100
    assert report["ready"] is True


def test_blank_subtype_checking_is_included_but_blocks_ready_status(
    client_id, accounts
):
    checking = _account(
        client_id, "1010", "Chase Checking", "Asset", None,
    )
    contribution = _account(
        client_id, "3100", "Owner Contribution", "Equity",
        AccountSubtype.OWNER_CONTRIBUTION,
    )
    post_entry(client_id, date(2026, 2, 1), [
        (checking, 300, 0), (contribution, 0, 300),
    ])

    report = ReportGenerator.cash_flow_statement(client_id, START, END)
    assert report["cash_ending"] == 300
    assert report["actual_cash_change"] == 300
    assert report["financing"]["total"] == 300
    assert report["ties"] is True
    assert report["classification_complete"] is False
    assert report["ready"] is False
    assert report["unresolved_cash_accounts"] == [{
        "account_number": "1010",
        "name": "Chase Checking",
        "subtype": None,
    }]
    assert any("need the Cash subtype" in warning for warning in report["warnings"])


def test_noncash_debt_financed_asset_purchase_is_disclosed(
    client_id, accounts
):
    equipment = _account(
        client_id, "1500", "Equipment", "Asset",
        AccountSubtype.FIXED_ASSET,
    )
    debt = _account(
        client_id, "2500", "Equipment Note", "Liability",
        AccountSubtype.LONG_TERM_LIABILITY,
    )
    post_entry(client_id, date(2026, 8, 1), [
        (equipment, 500, 0), (debt, 0, 500),
    ])

    report = ReportGenerator.cash_flow_statement(client_id, START, END)
    assert report["actual_cash_change"] == 0
    assert report["noncash_items"][0]["accounts"] == ["1500", "2500"]
    assert report["noncash_items"][0]["amount"] == 500
    assert report["ties"] is True
    assert report["ready"] is True


def test_partially_financed_purchase_reports_only_cash_on_the_face(
    client_id, accounts
):
    equipment = _account(
        client_id, "1500", "Equipment", "Asset", AccountSubtype.FIXED_ASSET,
    )
    debt = _account(
        client_id, "2500", "Equipment Note", "Liability",
        AccountSubtype.LONG_TERM_LIABILITY,
    )
    entry = post_entry(client_id, date(2026, 8, 1), [
        (equipment, 500, 0), (debt, 0, 400), (accounts["cash"], 0, 100),
    ])

    report = ReportGenerator.cash_flow_statement(client_id, START, END)
    assert report["actual_cash_change"] == -100
    assert report["investing"]["total"] == -100
    assert report["financing"]["total"] == 0
    assert report["noncash_items"] == [{
        "entry_id": entry.id,
        "entry_date": "2026-08-01",
        "description": "test entry",
        "accounts": ["1500", "2500"],
        "amount": 400,
    }]
    assert report["ties"] is True
    assert report["ready"] is True


def test_asset_exchange_discloses_same_section_noncash_component(
    client_id, accounts
):
    new_equipment = _account(
        client_id, "1500", "New Equipment", "Asset", AccountSubtype.FIXED_ASSET,
    )
    traded_equipment = _account(
        client_id, "1510", "Traded Equipment", "Asset", AccountSubtype.FIXED_ASSET,
    )
    entry = post_entry(client_id, date(2026, 8, 1), [
        (new_equipment, 500, 0),
        (traded_equipment, 0, 300),
        (accounts["cash"], 0, 200),
    ])

    report = ReportGenerator.cash_flow_statement(client_id, START, END)
    assert report["investing"]["total"] == -200
    assert report["noncash_items"] == [{
        "entry_id": entry.id,
        "entry_date": "2026-08-01",
        "description": "test entry",
        "accounts": ["1500", "1510"],
        "amount": 300,
    }]
    assert report["ties"] is True
    assert report["ready"] is True


def test_debt_proceeds_and_repayments_are_presented_gross(client_id, accounts):
    debt = _account(
        client_id, "2500", "Term Loan", "Liability",
        AccountSubtype.LONG_TERM_LIABILITY,
    )
    post_entry(client_id, date(2026, 2, 1), [
        (accounts["cash"], 500, 0), (debt, 0, 500),
    ])
    post_entry(client_id, date(2026, 3, 1), [
        (debt, 100, 0), (accounts["cash"], 0, 100),
    ])

    report = ReportGenerator.cash_flow_statement(client_id, START, END)
    lines = {line["name"]: line["amount"] for line in report["financing"]["lines"]}
    assert lines == {
        "Term Loan — Proceeds": 500,
        "Term Loan — Repayments": -100,
    }
    assert report["financing"]["total"] == 400
    assert report["ready"] is True


def test_named_noncash_operating_adjustment_reconciles_owner_paid_expense(
    client_id, accounts
):
    expense = _account(
        client_id, "6100", "Rent Expense", "Expense",
        AccountSubtype.OPERATING_EXPENSE,
    )
    contribution = _account(
        client_id, "3100", "Owner Contribution", "Equity",
        AccountSubtype.OWNER_CONTRIBUTION,
    )
    entry = post_entry(client_id, date(2026, 4, 1), [
        (expense, 100, 0), (contribution, 0, 100),
    ])

    report = ReportGenerator.cash_flow_statement(client_id, START, END)
    adjustment = next(
        line for line in report["operating"]["lines"]
        if line["key"] == "noncash_operating_activity"
    )
    assert adjustment["name"] == (
        f"Noncash Operating Activity — Entry #{entry.id}"
    )
    assert adjustment["amount"] == 100
    assert report["operating"]["total"] == 0
    assert report["noncash_items"][0]["amount"] == 100
    assert report["operating_reconciled"] is True
    assert report["classification_complete"] is True
    assert report["ready"] is True


def test_unsubtyped_noncash_account_blocks_classification_and_names_the_plug(
    client_id, accounts
):
    receivable = _account(
        client_id, "1100", "Legacy Receivable", "Asset", None,
    )
    revenue = _account(
        client_id, "4100", "Service Revenue", "Revenue",
        AccountSubtype.OPERATING_REVENUE,
    )
    post_entry(client_id, date(2026, 4, 1), [
        (receivable, 100, 0), (revenue, 0, 100),
    ])

    report = ReportGenerator.cash_flow_statement(client_id, START, END)
    plug = next(
        line for line in report["operating"]["lines"]
        if line["key"] == "unresolved_operating_reconciliation"
    )
    assert "1100" in plug["name"]
    assert report["operating_reconciled"] is False
    assert report["classification_complete"] is False
    assert report["ready"] is False
    assert any("1100" in warning for warning in report["warnings"])


def test_comparative_cash_flow_merges_lines_and_periods(client_id, accounts):
    revenue = _account(
        client_id, "4100", "Service Revenue", "Revenue",
        AccountSubtype.OPERATING_REVENUE,
    )
    post_entry(client_id, date(2025, 3, 1), [
        (accounts["cash"], 100, 0), (revenue, 0, 100),
    ])
    post_entry(client_id, date(2026, 3, 1), [
        (accounts["cash"], 200, 0), (revenue, 0, 200),
    ])

    report = ReportGenerator.comparative_cash_flow_statement(
        client_id, START, END
    )
    assert report["prior_available"] is True
    assert report["prior_period"] == {
        "start": date(2025, 1, 1), "end": date(2025, 12, 31)
    }
    net_income = next(
        line for line in report["operating"]["lines"]
        if line["key"] == "net_income"
    )
    assert net_income["current"] == 200
    assert net_income["prior"] == 100
    assert net_income["change"] == 100
    assert report["actual_cash_change"]["current"] == 200
    assert report["actual_cash_change"]["prior"] == 100
    assert report["current_ready"] is True
    assert report["prior_ready"] is True


def test_offsetting_unclassified_entries_remain_visible_in_exports(
    client_id, accounts
):
    suspense = _account(
        client_id, "1999", "Legacy Suspense", "Asset", None,
    )
    post_entry(client_id, date(2026, 5, 1), [
        (suspense, 100, 0), (accounts["cash"], 0, 100),
    ])
    post_entry(client_id, date(2026, 6, 1), [
        (accounts["cash"], 100, 0), (suspense, 0, 100),
    ])

    report = ReportGenerator.cash_flow_statement(client_id, START, END)
    assert report["unclassified"]["lines"] == []
    assert len(report["unclassified"]["entries"]) == 2
    assert report["classification_complete"] is False

    export = ReportGenerator.cash_flow_statement_to_dataframe(report)
    labels = export["Item"].tolist()
    assert "UNCLASSIFIED CASH ACTIVITY" in labels
    assert "UNCLASSIFIED ENTRY DETAILS" in labels
    assert export.set_index("Item").loc["STATUS", "Amount"] == "REVIEW WARNINGS"
    assert sum("counterpart account needs a statement subtype" in label
               for label in labels) == 2

    comparison = ReportGenerator.comparative_cash_flow_statement(
        client_id, START, END
    )
    comparison_export = (
        ReportGenerator.comparative_cash_flow_statement_to_dataframe(comparison)
    )
    comparison_labels = comparison_export["Item"].tolist()
    assert "CURRENT UNCLASSIFIED ENTRY DETAILS" in comparison_labels
    assert comparison_export.set_index("Item").loc["STATUS", "Current"] == (
        "REVIEW WARNINGS"
    )


def _disposal_accounts(client_id):
    retained = _account(
        client_id, "3900", "Retained Earnings", "Equity",
        AccountSubtype.RETAINED_EARNINGS,
    )
    equipment = _account(
        client_id, "1500", "Equipment", "Asset", AccountSubtype.FIXED_ASSET,
    )
    accumulated = _account(
        client_id, "1510", "Accumulated Depreciation", "Asset",
        AccountSubtype.ACCUMULATED_DEPRECIATION,
    )
    return retained, equipment, accumulated


def test_sale_at_book_value_is_not_a_noncash_exchange(client_id, accounts):
    """Removing accumulated depreciation on a disposal is part of the carrying
    amount given up, not a noncash investing activity to disclose."""
    retained, equipment, accumulated = _disposal_accounts(client_id)
    post_entry(client_id, START, [
        (accounts["cash"], 1000, 0), (equipment, 500, 0),
        (accumulated, 0, 400), (retained, 0, 1100),
    ], entry_type="Beginning Balance")
    post_entry(client_id, date(2026, 6, 1), [
        (accounts["cash"], 100, 0), (accumulated, 400, 0), (equipment, 0, 500),
    ])

    report = ReportGenerator.cash_flow_statement(client_id, START, END)
    assert report["investing"]["total"] == 100
    assert report["noncash_items"] == []
    assert report["ties"] is True
    assert report["ready"] is True


def test_fully_depreciated_write_off_is_not_a_noncash_exchange(
    client_id, accounts
):
    retained, equipment, accumulated = _disposal_accounts(client_id)
    post_entry(client_id, START, [
        (accounts["cash"], 1000, 0), (equipment, 500, 0),
        (accumulated, 0, 500), (retained, 0, 1000),
    ], entry_type="Beginning Balance")
    post_entry(client_id, date(2026, 6, 1), [
        (accumulated, 500, 0), (equipment, 0, 500),
    ])

    report = ReportGenerator.cash_flow_statement(client_id, START, END)
    assert report["noncash_items"] == []
    assert report["ready"] is True


def test_trade_in_with_accumulated_depreciation_discloses_carrying_amount(
    client_id, accounts
):
    """Trade in old equipment (cost 40, accumulated depreciation 30) plus 40
    cash for new equipment costing 50: the noncash consideration is the old
    asset's 10 carrying amount, not its gross cost."""
    retained, old_equipment, accumulated = _disposal_accounts(client_id)
    new_equipment = _account(
        client_id, "1520", "New Equipment", "Asset", AccountSubtype.FIXED_ASSET,
    )
    post_entry(client_id, START, [
        (accounts["cash"], 1000, 0), (old_equipment, 40, 0),
        (accumulated, 0, 30), (retained, 0, 1010),
    ], entry_type="Beginning Balance")
    entry = post_entry(client_id, date(2026, 8, 1), [
        (new_equipment, 50, 0), (accumulated, 30, 0),
        (old_equipment, 0, 40), (accounts["cash"], 0, 40),
    ])

    report = ReportGenerator.cash_flow_statement(client_id, START, END)
    assert report["investing"]["total"] == -40
    assert [(item["entry_id"], item["amount"]) for item in report["noncash_items"]] == [
        (entry.id, 10),
    ]
    assert report["ready"] is True


@pytest.mark.parametrize(
    ("result_subtype", "result_type", "result_debit", "result_credit", "cash_paid"),
    [
        (AccountSubtype.GAIN_ON_ASSET_DISPOSAL, "Revenue", 0, 5, 35),
        (AccountSubtype.LOSS_ON_ASSET_DISPOSAL, "Expense", 5, 0, 45),
    ],
)
def test_trade_in_with_gain_or_loss_still_discloses_carrying_amount(
    client_id, accounts, result_subtype, result_type,
    result_debit, result_credit, cash_paid,
):
    """A disposal gain or loss is an operating adjuster, not a reason to lose
    the noncash investing disclosure for the asset exchanged."""
    retained, old_equipment, accumulated = _disposal_accounts(client_id)
    new_equipment = _account(
        client_id, "1520", "New Equipment", "Asset", AccountSubtype.FIXED_ASSET,
    )
    result_account = _account(
        client_id, "4900", "Disposal Result", result_type, result_subtype,
    )
    post_entry(client_id, START, [
        (accounts["cash"], 1000, 0), (old_equipment, 40, 0),
        (accumulated, 0, 30), (retained, 0, 1010),
    ], entry_type="Beginning Balance")
    entry = post_entry(client_id, date(2026, 8, 1), [
        (new_equipment, 50, 0), (accumulated, 30, 0),
        (result_account, result_debit, result_credit),
        (old_equipment, 0, 40), (accounts["cash"], 0, cash_paid),
    ])

    report = ReportGenerator.cash_flow_statement(client_id, START, END)
    assert report["investing"]["total"] == -cash_paid
    assert [(item["entry_id"], item["amount"])
            for item in report["noncash_items"]] == [(entry.id, 10)]
    assert report["ready"] is True
