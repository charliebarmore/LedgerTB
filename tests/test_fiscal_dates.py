from datetime import date, datetime

import pytest

from models.audit_log import AuditLog
from models.fiscal_period import FiscalPeriod
from models.journal_entry import JournalEntry
from models.reconciliation import BankReconciliation
from models.reports import ReportGenerator
from models.transaction import ImportedTransaction
from utils.fiscal_dates import (
    fiscal_year_bounds,
    fiscal_year_ending_year,
    previous_fiscal_year_bounds,
    prior_year_date,
    prior_year_period,
)


def test_calendar_and_noncalendar_fiscal_year_bounds():
    assert fiscal_year_bounds(date(2026, 3, 15), 12) == (
        date(2026, 1, 1), date(2026, 12, 31)
    )
    assert fiscal_year_bounds(date(2026, 3, 15), 6) == (
        date(2025, 7, 1), date(2026, 6, 30)
    )
    assert fiscal_year_bounds(date(2026, 7, 1), 6) == (
        date(2026, 7, 1), date(2027, 6, 30)
    )
    assert previous_fiscal_year_bounds(date(2026, 3, 15), 6) == (
        date(2024, 7, 1), date(2025, 6, 30)
    )
    assert fiscal_year_ending_year(date(2026, 6, 30), 6) == 2026
    assert fiscal_year_ending_year(date(2026, 7, 1), 6) == 2027


def test_prior_year_comparison_dates_include_leap_day_fallback():
    assert prior_year_date(date(2026, 3, 31)) == date(2025, 3, 31)
    assert prior_year_date(date(2024, 2, 29)) == date(2023, 2, 28)
    assert prior_year_period(date(2026, 7, 1), date(2027, 6, 30)) == (
        date(2025, 7, 1), date(2026, 6, 30)
    )


def test_reconciliation_default_uses_client_fiscal_year(client_id, accounts):
    from models.client import Client

    client = Client.get_by_id(client_id)
    client.fiscal_year_end_month = 6
    client.save(seed_accounts=False)
    expected_start, _ = fiscal_year_bounds(date.today(), 6)
    assert BankReconciliation.suggested_start_date(client_id, accounts["cash"]) == expected_start


def test_domain_queries_reject_inverted_ranges(client_id, accounts):
    start = date(2026, 2, 1)
    end = date(2026, 1, 1)

    with pytest.raises(ValueError, match="cannot be after"):
        ImportedTransaction.get_all(client_id, start_date=start, end_date=end)
    with pytest.raises(ValueError, match="cannot be after"):
        ImportedTransaction.get_filtered_summary(client_id, start_date=start, end_date=end)
    with pytest.raises(ValueError, match="cannot be after"):
        JournalEntry.get_all(client_id, start_date=start, end_date=end)
    with pytest.raises(ValueError, match="cannot be after"):
        JournalEntry.get_filtered_summary(client_id, start_date=start, end_date=end)
    with pytest.raises(ValueError, match="cannot be after"):
        ReportGenerator.income_statement(client_id, start, end)
    with pytest.raises(ValueError, match="cannot be after"):
        ReportGenerator.general_ledger(accounts["cash"], start, end, client_id)
    with pytest.raises(ValueError, match="cannot be after"):
        ReportGenerator.trial_balance_worksheet(client_id, start, end)
    with pytest.raises(ValueError, match="cannot be after"):
        AuditLog.get_all(
            client_id, datetime(2026, 2, 1), datetime(2026, 1, 1)
        )


def test_fiscal_period_rejects_inverted_range(client_id):
    period = FiscalPeriod(
        client_id=client_id, period_name="Bad period", period_type="Custom",
        start_date=date(2026, 2, 1), end_date=date(2026, 1, 1),
    )
    with pytest.raises(ValueError, match="cannot be after"):
        period.save()
