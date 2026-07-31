"""The close package must tie to the books it summarizes."""
from datetime import date
from io import BytesIO

import openpyxl
import pytest

from models.reports import ReportGenerator
from services.close_package import (
    build_close_package,
    get_cash_activity,
    get_period_transactions,
)
from tests.conftest import post_entry


Q1 = (date(2026, 1, 1), date(2026, 3, 31))


@pytest.fixture
def booked_period(client_id, accounts):
    post_entry(
        client_id, date(2025, 12, 31),
        [(accounts["cash"], 500, 0), (accounts["equity"], 0, 500)],
    )
    post_entry(
        client_id, date(2026, 1, 15),
        [(accounts["cash"], 250, 0), (accounts["revenue"], 0, 250)],
    )
    post_entry(
        client_id, date(2026, 2, 10),
        [(accounts["expense"], 40, 0), (accounts["cash"], 0, 40)],
    )
    post_entry(
        client_id, date(2026, 3, 31),
        [(accounts["expense"], 10, 0), (accounts["revenue"], 10, 0),
         (accounts["cash"], 0, 20)],
        entry_type="Adjusting", source_reference="AJE-1",
    )
    return client_id


def test_period_transactions_are_scoped_and_ordered(booked_period, accounts):
    rows = get_period_transactions(booked_period, *Q1)
    # 2026-12-31 entry excluded; three in-period entries = 2 + 2 + 3 lines
    assert len(rows) == 7
    assert rows[0]["entry_date"] == "2026-01-15"
    assert all("2025" not in r["entry_date"] for r in rows)


def test_cash_activity_walks_beginning_to_ending(booked_period, accounts):
    cash_rows = get_cash_activity(booked_period, *Q1)
    row = next(r for r in cash_rows if r.account_name.lower().startswith("cash"))
    assert row.beginning == 500.0
    assert row.receipts == 250.0
    assert row.disbursements == 60.0
    assert row.ending == 690.0


def test_workbook_sheets_and_tie_outs(booked_period, accounts):
    client_id = booked_period
    tb_rows, _aje_details = ReportGenerator.trial_balance_worksheet(client_id, *Q1)
    package = build_close_package(client_id, "Test Co", *Q1, tb_rows)
    wb = openpyxl.load_workbook(BytesIO(package.read()))

    assert wb.sheetnames == [
        "Summary", "Trial Balance", "Transactions",
        "Adjusting Entries", "Receipts & Disbursements",
    ]

    # Transactions sheet: 7 in-period lines under 1 header row
    tx = wb["Transactions"]
    assert tx.max_row == 8

    # AJE sheet: only the Adjusting entry's 3 lines, tagged with its reference
    aje = wb["Adjusting Entries"]
    assert aje.max_row == 4
    assert all(aje.cell(row=i, column=3).value == "AJE-1" for i in range(2, 5))

    # Trial Balance sheet: one row per account plus header and totals
    tb = wb["Trial Balance"]
    assert tb.max_row == len(tb_rows) + 2
    assert tb.cell(row=tb.max_row, column=1).value == "TOTALS"

    # Summary declares balance
    summary = wb["Summary"]
    cells = {summary.cell(row=i, column=1).value: summary.cell(row=i, column=2).value
             for i in range(1, summary.max_row + 1)}
    assert cells["In balance"] == "YES"
    assert cells["Final trial balance — total debits"] == pytest.approx(
        sum(r.adjusted_dr for r in tb_rows)
    )
