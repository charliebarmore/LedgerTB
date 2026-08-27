from datetime import date

import pytest

from models.audit_log import AuditLog
from services.preferences import (
    DEFAULT_DATE_FORMAT,
    get_preferences,
    save_date_format,
)
from utils.dates import display_date
from utils.report_dates import as_of_for_preset, period_for_preset


def test_date_format_preference_defaults_persists_and_is_audited(db):
    assert get_preferences().date_format == DEFAULT_DATE_FORMAT

    saved = save_date_format("YYYY/MM/DD")

    assert saved.date_format == "YYYY/MM/DD"
    assert get_preferences().date_format == "YYYY/MM/DD"
    history = AuditLog.get_history("app_preferences", 1)
    assert len(history) == 1
    assert history[0].client_id is None
    assert history[0].new_values == {"date_format": "YYYY/MM/DD"}


def test_date_format_preference_rejects_unknown_values(db):
    with pytest.raises(ValueError, match="available date formats"):
        save_date_format("M/D/YY")


@pytest.mark.parametrize(
    ("date_format", "expected"),
    [
        ("MM/DD/YYYY", "08/27/2026"),
        ("DD/MM/YYYY", "27/08/2026"),
        ("YYYY/MM/DD", "2026/08/27"),
    ],
)
def test_display_date_honors_preference(date_format, expected):
    assert display_date(date(2026, 8, 27), date_format) == expected
    assert display_date("2026-08-27", date_format) == expected


def test_report_date_presets_cover_fiscal_and_calendar_periods():
    today = date(2026, 8, 27)
    assert period_for_preset("This Fiscal Year", today, 6) == (
        date(2026, 7, 1), today,
    )
    assert period_for_preset("Last Fiscal Year", today, 6) == (
        date(2025, 7, 1), date(2026, 6, 30),
    )
    assert period_for_preset("Last Calendar Year", today, 6) == (
        date(2025, 1, 1), date(2025, 12, 31),
    )
    assert as_of_for_preset("End of Last Month", today, 6) == date(2026, 7, 31)
    assert as_of_for_preset("End of Last Fiscal Year", today, 6) == date(
        2026, 6, 30
    )


def test_financial_statement_renders_safe_inline_and_new_tab_links(monkeypatch):
    from utils import ui

    rendered = []
    monkeypatch.setattr(ui.st, "html", rendered.append)

    ui.financial_statement([
        (
            "item",
            "1000 - Cash <Operating>",
            [123.45],
            "Asset",
            "?report=General+Ledger&account_id=7",
        )
    ])

    html = rendered[0]
    assert "class='pb-drill'" in html
    assert "target='_blank'" in html
    assert "user-select: text" in html
    assert "Cash &lt;Operating&gt;" in html
    assert "account_id=7" in html
