from datetime import date

from conftest import post_entry
from models.account import Account
from models.reports import ReportGenerator
from constants import AccountSubtype


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


def test_grouped_statements_and_multistep_income_are_additive(
    client_id, accounts
):
    operating_revenue = Account(
        client_id=client_id, account_number="4100", name="Product Sales",
        type="Revenue", subtype=AccountSubtype.OPERATING_REVENUE,
    )
    cogs = Account(
        client_id=client_id, account_number="5000", name="Cost of Goods Sold",
        type="Expense", subtype=AccountSubtype.COST_OF_GOODS_SOLD,
    )
    depreciation = Account(
        client_id=client_id, account_number="7000", name="Depreciation Expense",
        type="Expense", subtype=AccountSubtype.DEPRECIATION_AMORTIZATION,
    )
    equipment = Account(
        client_id=client_id, account_number="1500", name="Equipment",
        type="Asset", subtype=AccountSubtype.FIXED_ASSET,
    )
    accumulated = Account(
        client_id=client_id, account_number="1510",
        name="Accumulated Depreciation", type="Asset",
        subtype=AccountSubtype.ACCUMULATED_DEPRECIATION,
    )
    for account in (
        operating_revenue, cogs, depreciation, equipment, accumulated
    ):
        account.save()

    post_entry(client_id, date(2026, 1, 1), [
        (accounts["cash"], 1000, 0), (accounts["equity"], 0, 1000),
    ])
    post_entry(client_id, date(2026, 2, 1), [
        (accounts["cash"], 200, 0), (operating_revenue.id, 0, 200),
    ])
    post_entry(client_id, date(2026, 2, 2), [
        (cogs.id, 80, 0), (accounts["cash"], 0, 80),
    ])
    post_entry(client_id, date(2026, 3, 1), [
        (equipment.id, 500, 0), (accounts["cash"], 0, 500),
    ])
    post_entry(client_id, date(2026, 3, 31), [
        (depreciation.id, 100, 0), (accumulated.id, 0, 100),
    ])

    income = ReportGenerator.income_statement(
        client_id, date(2026, 1, 1), date(2026, 12, 31)
    )
    assert income["total_revenue"] == 200
    assert income["total_expenses"] == 180
    assert income["net_income"] == 20
    assert income["cost_of_goods_sold"] == 80
    assert income["gross_profit"] == 120
    assert income["depreciation_amortization"] == 100
    assert income["operating_income"] == 20
    assert [group["key"] for group in income["revenue_groups"]] == [
        "operating_revenue"
    ]
    assert [group["key"] for group in income["expense_groups"]] == [
        "cost_of_goods_sold", "depreciation_amortization"
    ]

    balance = ReportGenerator.balance_sheet(client_id, date(2026, 12, 31))
    by_group = {group["key"]: group for group in balance["asset_groups"]}
    assert by_group["current_assets"]["subtotal"] == 620
    assert by_group["property_equipment_net"]["subtotal"] == 400
    assert balance["total_assets"] == balance["total_liabilities_equity"] == 1020
    retained = next(
        group for group in balance["equity_groups"]
        if group["key"] == "retained_earnings"
    )
    assert any(
        item["name"] == "Current Year Earnings"
        for item in retained["accounts"]
    )

    income_df = ReportGenerator.income_statement_to_dataframe(income)
    balance_df = ReportGenerator.balance_sheet_to_dataframe(balance)
    assert "  Operating Revenue" in set(income_df["Item"])
    assert income_df.set_index("Item").loc["GROSS PROFIT", "Amount"] == 120
    assert income_df.set_index("Item").loc["OPERATING INCOME", "Amount"] == 20
    assert "  Property and Equipment, Net" in set(balance_df["Item"])

    comparative_income = ReportGenerator.comparative_income_statement(
        client_id, date(2026, 1, 1), date(2026, 12, 31)
    )
    comparative_income_df = (
        ReportGenerator.comparative_income_statement_to_dataframe(
            comparative_income
        )
    )
    comparative_rows = comparative_income_df.set_index("Item")
    assert comparative_rows.loc["GROSS PROFIT", "Current"] == 120
    assert comparative_rows.loc["OPERATING INCOME", "Current"] == 20


def test_unknown_subtypes_land_in_explicit_unclassified_groups(
    client_id, accounts
):
    custom = Account(
        client_id=client_id, account_number="6190", name="Custom Expense",
        type="Expense", subtype="Legacy CPA Group",
    )
    custom.save()
    post_entry(client_id, date(2026, 4, 1), [
        (custom.id, 25, 0), (accounts["cash"], 0, 25),
    ])

    report = ReportGenerator.income_statement(
        client_id, date(2026, 1, 1), date(2026, 12, 31)
    )
    group = next(g for g in report["expense_groups"] if g["key"] == "unclassified")
    assert group["group"] == "Unclassified Expenses"
    assert group["subtotal"] == 25
    assert group["accounts"][0]["statement_subtype"] is None
    assert report["multistep_ready"] is False
    assert report["gross_profit"] is None
    assert report["operating_income"] is None
    assert report["statement_warnings"]


def test_unclassified_revenue_hides_misleading_multistep_subtotals(
    client_id, accounts
):
    legacy_revenue = Account(
        client_id=client_id, account_number="4190", name="Legacy Sales",
        type="Revenue", subtype="CPA Revenue Group",
    )
    cogs = Account(
        client_id=client_id, account_number="5100", name="Materials",
        type="Expense", subtype=AccountSubtype.COST_OF_GOODS_SOLD,
    )
    legacy_revenue.save()
    cogs.save()
    post_entry(client_id, date(2026, 2, 1), [
        (accounts["cash"], 100, 0), (legacy_revenue.id, 0, 100),
    ])
    post_entry(client_id, date(2026, 2, 2), [
        (cogs.id, 40, 0), (accounts["cash"], 0, 40),
    ])

    report = ReportGenerator.income_statement(
        client_id, date(2026, 1, 1), date(2026, 12, 31)
    )
    labels = [label for _kind, label, _value
              in ReportGenerator.income_statement_rows(report)]
    assert report["net_income"] == 60
    assert report["multistep_ready"] is False
    assert report["gross_profit"] is None
    assert report["operating_income"] is None
    assert "GROSS PROFIT" not in labels
    assert "OPERATING INCOME" not in labels
    assert "Unclassified Revenues" in labels


def test_operating_expenses_section_does_not_repeat_its_group_heading(
    client_id, accounts
):
    operating_expense = Account(
        client_id=client_id,
        account_number="6100",
        name="Rent Expense",
        type="Expense",
        subtype=AccountSubtype.OPERATING_EXPENSE,
    )
    operating_expense.save()
    post_entry(client_id, date(2026, 4, 1), [
        (operating_expense.id, 25, 0), (accounts["cash"], 0, 25),
    ])

    report = ReportGenerator.income_statement(
        client_id, date(2026, 1, 1), date(2026, 12, 31)
    )
    rows = ReportGenerator.income_statement_rows(report)

    assert ("section", "Operating Expenses", None) in rows
    assert not any(
        kind == "group" and label == "Operating Expenses"
        for kind, label, _value in rows
    )
    assert any(
        kind == "group_total" and label == "Total Operating Expenses"
        for kind, label, _value in rows
    )


def test_other_income_only_does_not_render_an_empty_revenue_heading(
    client_id, accounts
):
    other_income = Account(
        client_id=client_id, account_number="4900", name="Interest Income",
        type="Revenue", subtype=AccountSubtype.OTHER_INCOME,
    )
    other_income.save()
    post_entry(client_id, date(2026, 4, 1), [
        (accounts["cash"], 25, 0), (other_income.id, 0, 25),
    ])
    report = ReportGenerator.income_statement(
        client_id, date(2026, 1, 1), date(2026, 12, 31)
    )
    rows = ReportGenerator.income_statement_rows(report)
    assert rows[0][:2] == ('section', 'Other Income and Expenses')
    assert not any(
        kind == 'section' and label == 'Revenue' for kind, label, _ in rows
    )


def test_orphan_accumulated_depreciation_is_not_negative_net_ppe(
    client_id, accounts
):
    accumulated = Account(
        client_id=client_id, account_number="1510",
        name="Accumulated Depreciation", type="Asset",
        subtype=AccountSubtype.ACCUMULATED_DEPRECIATION,
    )
    accumulated.save()
    post_entry(client_id, date(2026, 6, 30), [
        (accounts["equity"], 100, 0), (accumulated.id, 0, 100),
    ])

    report = ReportGenerator.balance_sheet(client_id, date(2026, 12, 31))
    groups = {group["key"]: group for group in report["asset_groups"]}
    assert "property_equipment_net" not in groups
    assert groups["unclassified"]["subtotal"] == -100
    assert groups["unclassified"]["accounts"][0]["name"] == (
        "Accumulated Depreciation"
    )


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

    export = ReportGenerator.comparative_income_statement_to_dataframe(report)
    # No COGS in this book, so a synthetic gross-profit row must not appear.
    assert "GROSS PROFIT" not in set(export["Item"])
    assert "OPERATING INCOME" not in set(export["Item"])


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


# ---- Earnings attribution (docs/EARNINGS-ATTRIBUTION.md) ----
# Current Year Earnings must always equal the income statement's net income;
# Beginning Balance and Closing P&L legs always feed Retained Earnings.


def _synthetic(report, name):
    """Balance of a synthetic equity line (0 when suppressed)."""
    return sum(
        e["balance"] for e in report["equity"]
        if e["name"] == name and not e["account_number"]
    )


def test_earnings_attribution_clean_year_ties(client_id, accounts):
    post_entry(client_id, date(2025, 3, 1), [
        (accounts["cash"], 700, 0), (accounts["revenue"], 0, 700),
    ])
    post_entry(client_id, date(2026, 4, 1), [
        (accounts["cash"], 1000, 0), (accounts["revenue"], 0, 1000),
    ])

    report = ReportGenerator.balance_sheet(client_id, date(2026, 12, 31))
    income = ReportGenerator.income_statement(
        client_id, date(2026, 1, 1), date(2026, 12, 31)
    )
    assert report["total_assets"] == report["total_liabilities_equity"]
    assert _synthetic(report, "Current Year Earnings") == income["net_income"] == 1000
    assert _synthetic(report, "Retained Earnings") == 700


def test_earnings_attribution_midyear_conversion(client_id, accounts):
    """A conversion's Beginning Balance P&L legs are opening equity: they land
    in Retained Earnings, and Current Year Earnings still ties to the income
    statement (which excludes Beginning Balance entries)."""
    post_entry(client_id, date(2026, 6, 30), [
        (accounts["cash"], 5000, 0), (accounts["revenue"], 0, 5000),
    ], entry_type="Beginning Balance")
    post_entry(client_id, date(2026, 7, 15), [
        (accounts["cash"], 1000, 0), (accounts["revenue"], 0, 1000),
    ])

    report = ReportGenerator.balance_sheet(client_id, date(2026, 12, 31))
    income = ReportGenerator.income_statement(
        client_id, date(2026, 1, 1), date(2026, 12, 31)
    )
    assert report["total_assets"] == report["total_liabilities_equity"] == 6000
    assert income["net_income"] == 1000
    assert _synthetic(report, "Current Year Earnings") == 1000
    assert _synthetic(report, "Retained Earnings") == 5000


def test_earnings_attribution_closing_dated_after_year_end(client_id, accounts):
    """A closing entry posted once the return is done (dated in the next year)
    cancels the year it closes -- no duplicated Retained Earnings line, no
    negative Current Year Earnings."""
    retained = Account(client_id=client_id, account_number="3900",
                       name="Retained Earnings", type="Equity")
    retained.save()
    post_entry(client_id, date(2026, 5, 1), [
        (accounts["cash"], 1000, 0), (accounts["revenue"], 0, 1000),
    ])
    post_entry(client_id, date(2027, 1, 1), [
        (accounts["revenue"], 1000, 0), (retained.id, 0, 1000),
    ], entry_type="Closing")
    post_entry(client_id, date(2027, 8, 1), [
        (accounts["cash"], 500, 0), (accounts["revenue"], 0, 500),
    ])

    report = ReportGenerator.balance_sheet(client_id, date(2027, 12, 31))
    income = ReportGenerator.income_statement(
        client_id, date(2027, 1, 1), date(2027, 12, 31)
    )
    assert report["total_assets"] == report["total_liabilities_equity"] == 1500
    assert income["net_income"] == 500
    assert _synthetic(report, "Current Year Earnings") == 500
    assert _synthetic(report, "Retained Earnings") == 0  # closed year cancels
    real_re = next(e for e in report["equity"] if e["account_number"] == "3900")
    assert real_re["balance"] == 1000
    # Exactly one visible Retained Earnings line: the real account.
    visible = [e for e in report["equity"] if e["name"] == "Retained Earnings"]
    assert len(visible) == 1 and visible[0]["account_number"] == "3900"


def test_earnings_attribution_closing_dated_in_year(client_id, accounts):
    """Statements stay pre-closing-style within the open year: Current Year
    Earnings shows the full year and the synthetic Retained Earnings line
    carries the close's offset beside the real equity account."""
    retained = Account(client_id=client_id, account_number="3900",
                       name="Retained Earnings", type="Equity")
    retained.save()
    post_entry(client_id, date(2026, 5, 1), [
        (accounts["cash"], 1000, 0), (accounts["revenue"], 0, 1000),
    ])
    post_entry(client_id, date(2026, 12, 31), [
        (accounts["revenue"], 1000, 0), (retained.id, 0, 1000),
    ], entry_type="Closing")

    report = ReportGenerator.balance_sheet(client_id, date(2026, 12, 31))
    income = ReportGenerator.income_statement(
        client_id, date(2026, 1, 1), date(2026, 12, 31)
    )
    assert report["total_assets"] == report["total_liabilities_equity"] == 1000
    assert income["net_income"] == 1000
    assert _synthetic(report, "Current Year Earnings") == 1000
    assert _synthetic(report, "Retained Earnings") == -1000
    real_re = next(e for e in report["equity"] if e["account_number"] == "3900")
    assert real_re["balance"] == 1000


def test_earnings_attribution_non_calendar_fiscal_year(db):
    """June fiscal year end: the fiscal-year window, not the calendar year,
    splits Retained Earnings from Current Year Earnings."""
    from models.client import Client

    june_client = Client(
        name="June FYE Co", entity_type="S-Corp", fiscal_year_end_month=6
    ).save(seed_accounts=False)
    cash = Account(client_id=june_client, account_number="1000",
                   name="Cash", type="Asset")
    cash.save()
    revenue = Account(client_id=june_client, account_number="4000",
                      name="Revenue", type="Revenue")
    revenue.save()

    # FY ending 2026-06-30 (prior year), conversion BB, and FY2027 activity.
    post_entry(june_client, date(2026, 5, 1), [
        (cash.id, 300, 0), (revenue.id, 0, 300),
    ])
    post_entry(june_client, date(2026, 9, 30), [
        (cash.id, 2000, 0), (revenue.id, 0, 2000),
    ], entry_type="Beginning Balance")
    post_entry(june_client, date(2026, 10, 15), [
        (cash.id, 400, 0), (revenue.id, 0, 400),
    ])

    report = ReportGenerator.balance_sheet(june_client, date(2027, 6, 30))
    income = ReportGenerator.income_statement(
        june_client, date(2026, 7, 1), date(2027, 6, 30)
    )
    assert report["total_assets"] == report["total_liabilities_equity"] == 2700
    assert income["net_income"] == 400
    assert _synthetic(report, "Current Year Earnings") == 400
    assert _synthetic(report, "Retained Earnings") == 300 + 2000


def test_earnings_attribution_survives_reversing_a_closing_entry(client_id, accounts):
    """Reversing a closing entry restores the pre-closing state exactly: the
    reversal keeps the Closing type, so the pair nets to zero inside Retained
    Earnings instead of double-counting income as ordinary activity."""
    from models.journal_entry import JournalEntry

    retained = Account(client_id=client_id, account_number="3900",
                       name="Retained Earnings", type="Equity")
    retained.save()
    post_entry(client_id, date(2026, 5, 1), [
        (accounts["cash"], 1000, 0), (accounts["revenue"], 0, 1000),
    ])
    closing = post_entry(client_id, date(2026, 12, 31), [
        (accounts["revenue"], 1000, 0), (retained.id, 0, 1000),
    ], entry_type="Closing")
    reversal = JournalEntry.reverse(closing.id, client_id, date(2026, 12, 31))
    assert reversal.entry_type == "Closing"

    report = ReportGenerator.balance_sheet(client_id, date(2026, 12, 31))
    income = ReportGenerator.income_statement(
        client_id, date(2026, 1, 1), date(2026, 12, 31)
    )
    assert income["net_income"] == 1000  # not doubled by the reversal
    assert _synthetic(report, "Current Year Earnings") == 1000
    assert _synthetic(report, "Retained Earnings") == 0
    # The real account netted to zero, so the balance sheet drops the line.
    assert not [e for e in report["equity"] if e["account_number"] == "3900"]
    assert report["total_assets"] == report["total_liabilities_equity"] == 1000


def test_earnings_attribution_survives_reversing_a_beginning_balance(client_id, accounts):
    """A Beginning Balance entry and its reversal net to zero everywhere --
    no phantom negative income in the current year."""
    from models.journal_entry import JournalEntry

    bb = post_entry(client_id, date(2026, 6, 30), [
        (accounts["cash"], 5000, 0), (accounts["revenue"], 0, 5000),
    ], entry_type="Beginning Balance")
    post_entry(client_id, date(2026, 7, 15), [
        (accounts["cash"], 1000, 0), (accounts["revenue"], 0, 1000),
    ])
    reversal = JournalEntry.reverse(bb.id, client_id, date(2026, 6, 30))
    assert reversal.entry_type == "Beginning Balance"

    report = ReportGenerator.balance_sheet(client_id, date(2026, 12, 31))
    income = ReportGenerator.income_statement(
        client_id, date(2026, 1, 1), date(2026, 12, 31)
    )
    assert income["net_income"] == 1000
    assert _synthetic(report, "Current Year Earnings") == 1000
    assert _synthetic(report, "Retained Earnings") == 0
    assert report["total_assets"] == report["total_liabilities_equity"] == 1000
