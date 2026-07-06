from datetime import date

from conftest import post_entry
from models.reports import ReportGenerator


def test_balance_sheet_balances_with_no_activity(client_id, accounts):
    report = ReportGenerator.balance_sheet(client_id, date(2025, 12, 31))
    assert report["total_assets"] == 0
    assert report["total_liabilities_equity"] == 0


def test_balance_sheet_balances_mid_year(client_id, accounts):
    # Owner contributes cash, then the business earns revenue and pays an expense.
    post_entry(client_id, date(2025, 1, 1), [
        (accounts["cash"], 1000, 0),
        (accounts["equity"], 0, 1000),
    ])
    post_entry(client_id, date(2025, 3, 15), [
        (accounts["cash"], 500, 0),
        (accounts["revenue"], 0, 500),
    ])
    post_entry(client_id, date(2025, 6, 1), [
        (accounts["expense"], 200, 0),
        (accounts["cash"], 0, 200),
    ])

    for as_of in [date(2025, 1, 31), date(2025, 3, 31), date(2025, 6, 30), date(2025, 12, 31)]:
        report = ReportGenerator.balance_sheet(client_id, as_of)
        assert report["total_assets"] == report["total_liabilities_equity"], (
            f"balance sheet out of balance as of {as_of}: "
            f"assets={report['total_assets']} L+E={report['total_liabilities_equity']}"
        )


def test_balance_sheet_carries_unclosed_prior_year_into_retained_earnings(client_id, accounts):
    """No closing entry is ever posted for FY2025 -- the balance sheet should
    still balance in FY2026 by rolling FY2025's net income into Retained Earnings."""
    post_entry(client_id, date(2025, 2, 1), [
        (accounts["cash"], 1000, 0),
        (accounts["revenue"], 0, 1000),
    ])
    post_entry(client_id, date(2025, 11, 1), [
        (accounts["expense"], 300, 0),
        (accounts["cash"], 0, 300),
    ])

    report = ReportGenerator.balance_sheet(client_id, date(2026, 6, 30))
    assert report["total_assets"] == report["total_liabilities_equity"]

    equity_names = {e["name"] for e in report["equity"]}
    assert "Retained Earnings" in equity_names
    assert "Current Year Earnings" not in equity_names  # nothing posted in FY2026

    retained = next(e for e in report["equity"] if e["name"] == "Retained Earnings")
    assert retained["balance"] == 700  # 1000 revenue - 300 expense


def test_income_statement_respects_date_range(client_id, accounts):
    post_entry(client_id, date(2025, 1, 15), [
        (accounts["cash"], 100, 0),
        (accounts["revenue"], 0, 100),
    ])
    post_entry(client_id, date(2025, 6, 15), [
        (accounts["cash"], 250, 0),
        (accounts["revenue"], 0, 250),
    ])

    january_only = ReportGenerator.income_statement(client_id, date(2025, 1, 1), date(2025, 1, 31))
    assert january_only["total_revenue"] == 100

    full_year = ReportGenerator.income_statement(client_id, date(2025, 1, 1), date(2025, 12, 31))
    assert full_year["total_revenue"] == 350


def test_trial_balance_respects_as_of_date(client_id, accounts):
    post_entry(client_id, date(2025, 1, 1), [
        (accounts["cash"], 1000, 0),
        (accounts["equity"], 0, 1000),
    ])
    post_entry(client_id, date(2025, 8, 1), [
        (accounts["cash"], 0, 400),
        (accounts["expense"], 400, 0),
    ])

    before_second_entry = ReportGenerator.trial_balance(client_id, date(2025, 6, 30))
    cash_row = next(r for r in before_second_entry if r.account_number == "1000")
    assert cash_row.debit == 1000

    after_second_entry = ReportGenerator.trial_balance(client_id, date(2025, 12, 31))
    cash_row = next(r for r in after_second_entry if r.account_number == "1000")
    assert cash_row.debit == 600


def test_worksheet_includes_in_period_beginning_balance_and_closing_entries(client_id, accounts):
    """Regression for C1: a Beginning Balance or Closing entry dated inside the
    period must appear on the Trial Balance Worksheet, not silently vanish while
    the worksheet still reports 'balanced'. Previously the period bucket filtered
    entry_type = 'Regular', so both legs of such entries dropped together (leaving
    debits == credits) and the equity/opening balance disappeared."""
    # Opening balances posted as a Beginning Balance entry dated within the period.
    post_entry(client_id, date(2025, 1, 1), [
        (accounts["cash"], 5000, 0),
        (accounts["equity"], 0, 5000),
    ], entry_type="Beginning Balance")
    # Ordinary in-period activity.
    post_entry(client_id, date(2025, 3, 15), [
        (accounts["cash"], 1000, 0),
        (accounts["revenue"], 0, 1000),
    ])
    # A Closing entry dated within the period must also be captured.
    post_entry(client_id, date(2025, 12, 31), [
        (accounts["revenue"], 1000, 0),
        (accounts["equity"], 0, 1000),
    ], entry_type="Closing")

    rows, _ = ReportGenerator.trial_balance_worksheet(
        client_id, date(2025, 1, 1), date(2025, 12, 31)
    )
    by_name = {r.account_name: r for r in rows}

    # The equity legs of the Beginning Balance + Closing entries must not disappear.
    assert "Owner's Equity" in by_name
    assert by_name["Owner's Equity"].adjusted_cr == 6000  # 5000 opening + 1000 closed

    # Revenue was earned (1000) then closed out (1000) -> nets to zero, present or dropped is fine.
    # Cash must reflect both cash legs: 5000 + 1000 = 6000.
    assert by_name["Cash"].adjusted_dr == 6000

    # Adjusted TB must tie out AND match the plain trial balance totals.
    total_dr = sum(r.adjusted_dr for r in rows)
    total_cr = sum(r.adjusted_cr for r in rows)
    assert total_dr == total_cr

    plain = ReportGenerator.trial_balance(client_id, date(2025, 12, 31))
    assert total_dr == sum(r.debit for r in plain)
    assert total_cr == sum(r.credit for r in plain)
