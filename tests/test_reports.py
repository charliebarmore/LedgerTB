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
