from datetime import date

from conftest import post_entry
from models.account import Account
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


def test_income_statement_comparison_merges_lines_and_calculates_changes(
    client_id, accounts
):
    from models.account import Account

    prior_only = Account(
        client_id=client_id, account_number="6100", name="Prior-only expense",
        type="Expense",
    )
    prior_only.save()
    post_entry(client_id, date(2025, 2, 1), [
        (accounts["cash"], 200, 0), (accounts["revenue"], 0, 200),
    ])
    post_entry(client_id, date(2025, 2, 2), [
        (prior_only.id, 50, 0), (accounts["cash"], 0, 50),
    ])
    post_entry(client_id, date(2026, 2, 1), [
        (accounts["cash"], 300, 0), (accounts["revenue"], 0, 300),
    ])
    post_entry(client_id, date(2026, 2, 2), [
        (accounts["expense"], 60, 0), (accounts["cash"], 0, 60),
    ])

    report = ReportGenerator.comparative_income_statement(
        client_id, date(2026, 1, 1), date(2026, 3, 31)
    )
    assert report["prior_available"] is True
    assert report["prior_period"] == {
        "start": date(2025, 1, 1), "end": date(2025, 3, 31)
    }
    assert report["total_revenue"] == {
        "current": 300, "prior": 200, "change": 100, "change_percent": 50,
    }
    assert report["net_income"]["current"] == 240
    assert report["net_income"]["prior"] == 150
    expenses = {line["account_number"]: line for line in report["expenses"]}
    assert expenses["6000"]["prior"] == 0
    assert expenses["6100"]["current"] == 0


def test_comparisons_distinguish_missing_history_from_a_real_zero(
    client_id, accounts
):
    post_entry(client_id, date(2026, 1, 15), [
        (accounts["cash"], 100, 0), (accounts["revenue"], 0, 100),
    ])
    income = ReportGenerator.comparative_income_statement(
        client_id, date(2026, 1, 1), date(2026, 12, 31)
    )
    balance = ReportGenerator.comparative_balance_sheet(
        client_id, date(2026, 12, 31)
    )
    assert income["prior_available"] is False
    assert income["total_revenue"]["prior"] is None
    assert balance["prior_available"] is False
    assert balance["total_assets"]["prior"] is None


def test_balance_sheet_and_trial_balance_compare_same_prior_date(
    client_id, accounts
):
    post_entry(client_id, date(2025, 3, 31), [
        (accounts["cash"], 500, 0), (accounts["equity"], 0, 500),
    ])
    post_entry(client_id, date(2026, 3, 31), [
        (accounts["cash"], 250, 0), (accounts["equity"], 0, 250),
    ])

    balance = ReportGenerator.comparative_balance_sheet(
        client_id, date(2026, 3, 31)
    )
    assert balance["prior_as_of"] == date(2025, 3, 31)
    assert balance["total_assets"]["current"] == 750
    assert balance["total_assets"]["prior"] == 500
    assert balance["current_balanced"] is True
    assert balance["prior_balanced"] is True

    trial = ReportGenerator.comparative_trial_balance(
        client_id, date(2026, 3, 31)
    )
    cash = next(r for r in trial["accounts"] if r["account_number"] == "1000")
    assert cash["current_debit"] == 750
    assert cash["prior_debit"] == 500
    assert trial["current_total_debits"] == trial["current_total_credits"]
    assert trial["prior_total_debits"] == trial["prior_total_credits"]


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


def test_deactivated_accounts_remain_in_historical_reports(client_id, accounts):
    """Deactivation blocks future use; it must not remove posted history."""
    post_entry(client_id, date(2025, 3, 1), [
        (accounts["cash"], 500, 0),
        (accounts["revenue"], 0, 500),
    ])
    revenue = Account.get_by_id(accounts["revenue"], client_id=client_id)
    revenue.deactivate()

    trial_balance = ReportGenerator.trial_balance(client_id, date(2025, 12, 31))
    assert {r.account_number for r in trial_balance} == {"1000", "4000"}
    assert sum(r.debit for r in trial_balance) == sum(r.credit for r in trial_balance) == 500

    income = ReportGenerator.income_statement(
        client_id, date(2025, 1, 1), date(2025, 12, 31)
    )
    assert income["total_revenue"] == 500

    balance_sheet = ReportGenerator.balance_sheet(client_id, date(2025, 12, 31))
    assert balance_sheet["total_assets"] == balance_sheet["total_liabilities_equity"] == 500

    worksheet, _ = ReportGenerator.trial_balance_worksheet(
        client_id, date(2025, 1, 1), date(2025, 12, 31)
    )
    assert {r.account_number for r in worksheet} == {"1000", "4000"}


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


def test_worksheet_grouped_query_exact_values(client_id, accounts):
    """Lock the exact worksheet output after the grouped-query rewrite (M8):
    beginning balances, in-period non-adjusting activity (incl. a Beginning
    Balance entry, per C1), AJE activity, adjusted TB, and AJE details."""
    from models.account import Account
    prepaid = Account(client_id=client_id, account_number="1400", name="Prepaid", type="Asset")
    prepaid.save()

    # Prior-period opening balance (before the period) -> beginning balance.
    post_entry(client_id, date(2024, 12, 31),
               [(accounts["cash"], 1000, 0), (accounts["equity"], 0, 1000)],
               entry_type="Beginning Balance")
    # Regular in-period revenue.
    post_entry(client_id, date(2025, 3, 1),
               [(accounts["cash"], 500, 0), (accounts["revenue"], 0, 500)])
    # Beginning Balance TYPE dated in-period -> must count as period activity (C1).
    post_entry(client_id, date(2025, 1, 1),
               [(prepaid.id, 300, 0), (accounts["cash"], 0, 300)],
               entry_type="Beginning Balance")
    # Adjusting entry in-period.
    post_entry(client_id, date(2025, 6, 30),
               [(accounts["expense"], 100, 0), (prepaid.id, 0, 100)],
               entry_type="Adjusting")

    rows, aje_details = ReportGenerator.trial_balance_worksheet(
        client_id, date(2025, 1, 1), date(2025, 12, 31))
    by_num = {r.account_number: r for r in rows}

    # cash: beg 1000dr; period +500dr -300cr; adjusted 1200dr
    assert (by_num["1000"].beginning_dr, by_num["1000"].beginning_cr) == (1000, 0)
    assert (by_num["1000"].period_debits, by_num["1000"].period_credits) == (500, 300)
    assert (by_num["1000"].adjusted_dr, by_num["1000"].adjusted_cr) == (1200, 0)
    # equity: 1000cr throughout
    assert (by_num["3000"].adjusted_dr, by_num["3000"].adjusted_cr) == (0, 1000)
    # revenue: 500cr
    assert (by_num["4000"].adjusted_dr, by_num["4000"].adjusted_cr) == (0, 500)
    # prepaid: period 300dr, AJE 100cr, adjusted 200dr
    assert (by_num["1400"].period_debits, by_num["1400"].period_credits) == (300, 0)
    assert (by_num["1400"].aje_debits, by_num["1400"].aje_credits) == (0, 100)
    assert (by_num["1400"].adjusted_dr, by_num["1400"].adjusted_cr) == (200, 0)
    # expense: AJE 100dr, adjusted 100dr
    assert (by_num["6000"].adjusted_dr, by_num["6000"].adjusted_cr) == (100, 0)

    # Whole worksheet ties out.
    assert sum(r.adjusted_dr for r in rows) == sum(r.adjusted_cr for r in rows) == 1500

    # AJE details grouped correctly per account.
    assert prepaid.id in aje_details and len(aje_details[prepaid.id]) == 1
    assert aje_details[prepaid.id][0]["credit"] == 100
    assert aje_details[accounts["expense"]][0]["debit"] == 100


def test_worksheet_does_not_leak_across_clients(client_id, accounts):
    """The grouped queries scope on je.client_id -- another client's entries on a
    same-numbered account must not appear in this client's worksheet."""
    from models.client import Client
    from models.account import Account

    post_entry(client_id, date(2025, 3, 1),
               [(accounts["cash"], 500, 0), (accounts["revenue"], 0, 500)])

    other = Client(name="Other", entity_type="S-Corp", fiscal_year_end_month=12).save(seed_accounts=False)
    o_cash = Account(client_id=other, account_number="1000", name="O Cash", type="Asset"); o_cash.save()
    o_rev = Account(client_id=other, account_number="4000", name="O Rev", type="Revenue"); o_rev.save()
    post_entry(other, date(2025, 3, 1), [(o_cash.id, 9999, 0), (o_rev.id, 0, 9999)])

    rows, _ = ReportGenerator.trial_balance_worksheet(client_id, date(2025, 1, 1), date(2025, 12, 31))
    by_num = {r.account_number: r for r in rows}
    assert by_num["1000"].adjusted_dr == 500  # not 500 + 9999
    assert sum(r.adjusted_dr for r in rows) == 500
