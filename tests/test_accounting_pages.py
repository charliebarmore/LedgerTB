from datetime import date, timedelta
from pathlib import Path

from constants import AccountSubtype
from streamlit.testing.v1 import AppTest
import streamlit as st

from models.account import Account
from models.draft_entry import DraftEntry
from models.journal_entry import JournalEntry
from models.transaction import ImportedTransaction
from services.posting import post_transaction
from services import mcp_tools
from tests.conftest import page_path, post_entry


def _select_client(monkeypatch, client_id):
    import utils.client_selector as selector

    monkeypatch.setattr(selector, "render_client_selector", lambda: client_id)
    monkeypatch.setattr(st, "page_link", lambda *args, **kwargs: None)


def test_paginated_accounting_pages_render(client_id, accounts, monkeypatch):
    _select_client(monkeypatch, client_id)
    ImportedTransaction.bulk_insert([
        ImportedTransaction(
            client_id=client_id, import_batch="page-ui",
            transaction_date=date(2026, 1, 1) + timedelta(days=index),
            description=f"Transaction {index}", amount=index + 1,
            bank_account_id=accounts["cash"], status="Pending",
        )
        for index in range(55)
    ])
    for index in range(26):
        amount = index + 1
        post_entry(
            client_id, date(2026, 1, 1) + timedelta(days=index),
            [(accounts["cash"], amount, 0), (accounts["revenue"], 0, amount)],
        )

    transactions = AppTest.from_file(page_path("pages/6_Transactions.py"), default_timeout=30).run()
    assert not transactions.exception
    assert any(metric.label == "Filtered Transactions" for metric in transactions.metric)
    assert any(button.label == "Next" and not button.disabled for button in transactions.button)

    journals = AppTest.from_file(page_path("pages/2_Journal_Entries.py"), default_timeout=30)
    journals.session_state["journal_active_tab"] = "View Entries"
    journals.run()
    assert not journals.exception
    assert any(metric.label == "Filtered Entries" for metric in journals.metric)
    assert any(button.label == "Next" and not button.disabled for button in journals.button)

    audit = AppTest.from_file(page_path("pages/8_Audit_Trail.py"), default_timeout=30).run()
    assert not audit.exception
    assert any(metric.label == "Total Changes" for metric in audit.metric)
    assert any(button.label == "Next" and not button.disabled for button in audit.button)


def test_statement_editor_reserves_space_for_its_scrollbar():
    """The rightmost amount must not render beneath the editor scrollbar."""
    source = Path(page_path("pages/4_Import_Transactions.py")).read_text()

    assert ".st-key-document_transaction_editor .dvn-scroller" in source
    assert "scrollbar-gutter: stable both-edges" in source


def test_chart_of_accounts_uses_correct_plural_labels(
    client_id, accounts, monkeypatch
):
    _select_client(monkeypatch, client_id)
    page = AppTest.from_file(
        page_path("pages/3_Chart_of_Accounts.py"), default_timeout=30
    ).run()
    assert not page.exception
    labels = " ".join(expander.label for expander in page.expander)
    assert "Liabilities" in labels
    assert "Equities" in labels
    assert "Liabilitys" not in labels
    assert "Equitys" not in labels
    assert any(
        "Review statement subtypes" in expander.label
        for expander in page.expander
    )


def test_changing_account_type_clears_incompatible_subtype(
    client_id, accounts, monkeypatch
):
    _select_client(monkeypatch, client_id)
    expense = Account.get_by_id(accounts["expense"], client_id=client_id)
    expense.subtype = AccountSubtype.OPERATING_EXPENSE
    expense.save()

    page = AppTest.from_file(
        page_path("pages/3_Chart_of_Accounts.py"), default_timeout=30
    )
    page.session_state["editing_account"] = expense.id
    page.run()
    assert not page.exception

    page.selectbox(key=f"edit_account_type_{expense.id}").set_value("Asset").run()
    assert not page.exception
    subtype = page.selectbox(key=f"edit_account_subtype_{expense.id}")
    assert subtype.value is None
    assert AccountSubtype.OPERATING_EXPENSE not in subtype.options

    next(button for button in page.button if button.label == "Save Changes").click().run()
    assert not page.exception
    saved = Account.get_by_id(expense.id, client_id=client_id)
    assert saved.type == "Asset"
    assert saved.subtype is None


def test_add_account_subtypes_follow_type_without_form_submission(
    client_id, accounts, monkeypatch
):
    _select_client(monkeypatch, client_id)
    page = AppTest.from_file(
        page_path("pages/3_Chart_of_Accounts.py"), default_timeout=30
    ).run()
    page.selectbox(key="add_account_subtype").set_value(
        AccountSubtype.CASH
    ).run()
    page.selectbox(key="add_account_type").set_value("Liability").run()
    assert not page.exception
    subtype = next(
        box for box in page.selectbox
        if box.label == "Statement Subtype (optional)"
    )
    assert subtype.options == ["Review later"] + AccountSubtype.for_type("Liability")
    assert subtype.value is None
    assert AccountSubtype.OPERATING_EXPENSE not in subtype.options


def test_report_statements_render_with_balance_checks(client_id, accounts, monkeypatch):
    """Each statement view renders as a statement and its check line passes."""
    _select_client(monkeypatch, client_id)
    post_entry(
        client_id, date(2026, 1, 15),
        [(accounts["cash"], 500, 0), (accounts["revenue"], 0, 500)],
    )
    post_entry(
        client_id, date(2026, 2, 3),
        [(accounts["expense"], 120, 0), (accounts["cash"], 0, 120)],
    )

    for view, expected in [
        ("Trial Balance", "Trial balance is in balance."),
        ("Income Statement", None),
        ("Balance Sheet", "Balance sheet is balanced."),
    ]:
        page = AppTest.from_file(page_path("pages/5_Reports.py"), default_timeout=30)
        page.session_state["active_report"] = view
        page.run()
        assert not page.exception, view
        if expected:
            assert any(expected in str(s.value) for s in page.success), view
        # the GL drill-down selectbox replaced the per-account buttons
        assert any("gl_pick" in (box.key or "") for box in page.selectbox), view


def test_cash_flow_report_renders_quality_check_and_export(
    client_id, accounts, monkeypatch
):
    _select_client(monkeypatch, client_id)
    post_entry(
        client_id, date(2026, 1, 15),
        [(accounts["cash"], 500, 0), (accounts["revenue"], 0, 500)],
    )

    page = AppTest.from_file(
        page_path("pages/5_Reports.py"), default_timeout=30
    )
    page.session_state["active_report"] = "Cash Flow"
    page.run()

    assert not page.exception
    assert any("Cash flow is tied" in str(item.value) for item in page.success)
    assert any(
        button.label == "Download Excel"
        for button in page.get("download_button")
    )


def test_legacy_income_statement_defaults_to_classic_layout(
    client_id, accounts, monkeypatch
):
    _select_client(monkeypatch, client_id)
    legacy_revenue = Account(
        client_id=client_id, account_number="4199", name="Legacy Revenue",
        type="Revenue", subtype=None,
    )
    legacy_revenue.save()
    post_entry(client_id, date(2026, 1, 15), [
        (accounts["cash"], 500, 0), (legacy_revenue.id, 0, 500),
    ])

    page = AppTest.from_file(
        page_path("pages/5_Reports.py"), default_timeout=30
    )
    page.session_state["active_report"] = "Income Statement"
    page.run()

    assert not page.exception
    assert page.toggle(key="is_group_subtypes").value is False
    assert any("classic layout" in str(item.value) for item in page.info)

    page.toggle(key="is_group_subtypes").set_value(True).run()
    assert not page.exception
    assert page.toggle(key="is_group_subtypes").value is True


def test_cash_flow_report_prints_reconciliation_difference(
    client_id, accounts, monkeypatch
):
    _select_client(monkeypatch, client_id)
    post_entry(client_id, date(2026, 1, 15), [
        (accounts["cash"], 500, 0), (accounts["revenue"], 0, 500),
    ])

    from models.reports import ReportGenerator

    original = ReportGenerator.comparative_cash_flow_statement

    def untied(*args, **kwargs):
        report = original(*args, **kwargs)
        report["reconciliation_difference"]["current"] = 25
        report["current_ties"] = False
        report["current_ready"] = False
        return report

    monkeypatch.setattr(
        ReportGenerator, "comparative_cash_flow_statement", staticmethod(untied)
    )
    page = AppTest.from_file(
        page_path("pages/5_Reports.py"), default_timeout=30
    )
    page.session_state["active_report"] = "Cash Flow"
    page.run()

    assert not page.exception
    html = "\n".join(
        str(item.body) for item in page.get("html") if hasattr(item, "body")
    )
    assert "Cash Flow Reconciliation Difference" in html


def test_cash_flow_report_surfaces_prior_year_quality_warnings(
    client_id, accounts, monkeypatch
):
    _select_client(monkeypatch, client_id)
    post_entry(client_id, date(2026, 1, 15), [
        (accounts["cash"], 500, 0), (accounts["revenue"], 0, 500),
    ])

    from models.reports import ReportGenerator

    original = ReportGenerator.comparative_cash_flow_statement

    def prior_warning(*args, **kwargs):
        report = original(*args, **kwargs)
        report["prior_available"] = True
        report["prior_ready"] = False
        report["prior_warnings"] = ["Prior-only classification warning."]
        report["prior_noncash_items"] = [{
            "entry_id": 99,
            "entry_date": "2025-02-01",
            "description": "Prior equipment note",
            "accounts": ["1500", "2500"],
            "amount": 300,
        }]
        return report

    monkeypatch.setattr(
        ReportGenerator,
        "comparative_cash_flow_statement",
        staticmethod(prior_warning),
    )
    page = AppTest.from_file(
        page_path("pages/5_Reports.py"), default_timeout=30
    )
    page.session_state["active_report"] = "Cash Flow"
    page.run()

    assert not page.exception
    assert page.toggle(key="cf_compare_py").value is True
    assert any(
        "Prior-year cash flow has items to review" in str(item.value)
        for item in page.warning
    )
    assert any(
        "Prior year: Prior-only classification warning." in str(item.value)
        for item in page.caption
    )
    assert any(
        "Prior-year noncash investing and financing activity" in item.label
        for item in page.expander
    )


def test_period_picker_drives_the_worksheet_dates(client_id, accounts, monkeypatch):
    """Picking a Period must move From/To (keyed date inputs ignore value=)."""
    _select_client(monkeypatch, client_id)
    page = AppTest.from_file(page_path("pages/1_Trial_Balance_Worksheet.py"), default_timeout=30)
    page.run()
    assert not page.exception

    from models.fiscal_period import FiscalPeriod
    periods = FiscalPeriod.get_all(client_id)
    year = next(p for p in periods if p.period_type == "Year")
    may = next(p for p in periods if "May" in p.period_name)
    assert page.session_state["period_start"] == year.start_date

    page.selectbox(key="period_selector").set_value(may.id).run()
    assert not page.exception
    assert page.session_state["period_start"] == may.start_date
    assert page.session_state["period_end"] == may.end_date

    # A hand-edited date survives unrelated reruns…
    page.date_input(key="period_start").set_value(may.start_date.replace(day=15)).run()
    assert page.session_state["period_start"] == may.start_date.replace(day=15)
    # …but picking another period resets the range again.
    q3 = next(p for p in periods if "Q3" in p.period_name)
    page.selectbox(key="period_selector").set_value(q3.id).run()
    assert page.session_state["period_start"] == q3.start_date


def test_general_ledger_defaults_to_all_accounts(client_id, accounts, monkeypatch):
    """The GL is the whole book by default; the picker only narrows it."""
    _select_client(monkeypatch, client_id)
    post_entry(
        client_id, date(2026, 1, 15),
        [(accounts["cash"], 500, 0), (accounts["revenue"], 0, 500)],
    )
    post_entry(
        client_id, date(2026, 2, 3),
        [(accounts["expense"], 120, 0), (accounts["cash"], 0, 120)],
    )

    page = AppTest.from_file(page_path("pages/5_Reports.py"), default_timeout=30)
    page.session_state["active_report"] = "General Ledger"
    page.run()
    assert not page.exception
    filter_box = next(b for b in page.selectbox if b.label == "Account filter")
    assert filter_box.value is None  # nothing selected = all accounts
    assert page.checkbox(key="gl_hide_reversed_imports").value is True
    assert any("Excel downloads always include" in c.value for c in page.caption)
    body = " ".join(str(m.value) for m in page.markdown)
    for name in ("Cash", "Revenue", "Expense"):
        assert name in body, f"{name} section missing from the all-accounts GL"
    assert any(c.value.startswith("3 accounts") for c in page.caption)

    # Drill-down state still narrows to one account.
    drilled = AppTest.from_file(page_path("pages/5_Reports.py"), default_timeout=30)
    drilled.session_state["active_report"] = "General Ledger"
    drilled.session_state["gl_account_id"] = accounts["cash"]
    drilled.run()
    assert not drilled.exception
    filter_box = next(b for b in drilled.selectbox if b.label == "Account filter")
    assert filter_box.value == accounts["cash"]


def test_year_close_checklist_page_renders(client_id, accounts, monkeypatch):
    _select_client(monkeypatch, client_id)
    worksheet = AppTest.from_file(page_path("pages/1_Trial_Balance_Worksheet.py"), default_timeout=30
    ).run()
    assert not worksheet.exception
    assert any(
        heading.value == "Year-close checklist" for heading in worksheet.subheader
    )


def test_journal_form_clears_keyed_line_widgets_after_save(
    client_id, accounts, monkeypatch
):
    _select_client(monkeypatch, client_id)
    journal = AppTest.from_file(page_path("pages/2_Journal_Entries.py"), default_timeout=30
    ).run()
    assert not journal.exception

    journal.selectbox(key="account_0_g0").set_value(accounts["cash"]).run()
    journal.number_input(key="debit_0_g0").set_value(125.0).run()
    journal.selectbox(key="account_1_g0").set_value(accounts["revenue"]).run()
    journal.number_input(key="credit_1_g0").set_value(125.0).run()
    journal.text_input(key="je_hdr_desc_g0").set_value("Test entry").run()
    next(button for button in journal.button if button.label == "Save Entry").click().run()

    assert not journal.exception
    assert JournalEntry.count(client_id) == 1
    # Saving starts a new widget generation: fresh keys, so the browser cannot
    # re-impose the saved entry's values on the cleared form.
    assert journal.session_state["je_form_gen"] == 1
    assert journal.selectbox(key="account_0_g1").value is None
    assert journal.selectbox(key="account_1_g1").value is None
    assert journal.number_input(key="debit_0_g1").value == 0.0
    assert journal.number_input(key="credit_1_g1").value == 0.0
    # Header widgets reset too — description clears and type returns to Regular.
    assert journal.text_input(key="je_hdr_desc_g1").value == ""
    assert journal.selectbox(key="je_hdr_type_g1").value == "Regular"


def test_journal_totals_reflect_committed_values_immediately(
    client_id, accounts, monkeypatch
):
    """The running totals must include a value on the very run it commits.

    They were computed from je_lines, which the widgets update only later in
    the script, so the totals trailed the visible boxes by one interaction —
    a balanced entry kept showing "Not balanced" until the user did something
    else. Regression for the committed-state read.
    """
    _select_client(monkeypatch, client_id)
    journal = AppTest.from_file(page_path("pages/2_Journal_Entries.py"), default_timeout=30
    ).run()
    assert not journal.exception

    journal.number_input(key="debit_0_g0").set_value(67.35).run()
    debit_metric = next(m for m in journal.metric if m.label == "Total Debits")
    assert debit_metric.value == "$67.35"

    journal.number_input(key="credit_1_g0").set_value(67.35).run()
    diff_metric = next(m for m in journal.metric if m.label == "Difference")
    assert diff_metric.value == "$0.00"


def test_hand_keyed_adjusting_entry_gets_aje_reference(client_id, accounts, monkeypatch):
    """AJEs keyed on this page must get the next AJE-00x, like worksheet AJEs."""
    _select_client(monkeypatch, client_id)
    journal = AppTest.from_file(page_path("pages/2_Journal_Entries.py"), default_timeout=30
    ).run()
    journal.selectbox(key="je_hdr_type_g0").set_value("Adjusting").run()
    journal.selectbox(key="account_0_g0").set_value(accounts["expense"]).run()
    journal.number_input(key="debit_0_g0").set_value(10.0).run()
    journal.selectbox(key="account_1_g0").set_value(accounts["revenue"]).run()
    journal.number_input(key="credit_1_g0").set_value(10.0).run()
    next(b for b in journal.button if b.label == "Save Entry").click().run()

    assert not journal.exception
    entry = JournalEntry.get_all(client_id=client_id)[0]
    assert entry.entry_type == "Adjusting"
    assert entry.aje_reference == "AJE-001"


def test_edit_button_lands_on_the_form(client_id, accounts, monkeypatch):
    """Edit must switch to the New Entry view — with st.tabs it loaded the
    form invisibly and the click appeared to do nothing."""
    _select_client(monkeypatch, client_id)
    entry = post_entry(
        client_id, date(2026, 3, 21),
        [(accounts["cash"], 60, 0), (accounts["revenue"], 0, 60)],
    )
    journal = AppTest.from_file(page_path("pages/2_Journal_Entries.py"), default_timeout=30
    )
    journal.session_state["journal_active_tab"] = "View Entries"
    journal.run()

    journal.button(key=f"edit_entry_{entry.id}").click().run()
    assert not journal.exception
    assert journal.session_state["journal_active_tab"] == "New Entry"
    assert journal.session_state["editing_entry_id"] == entry.id
    assert any("Edit Journal Entry" in h.value for h in journal.subheader)


def test_editing_an_aje_preserves_its_reference(client_id, accounts, monkeypatch):
    """The update statement overwrites aje_reference; editing must carry it."""
    from models.journal_entry import JournalEntryLine

    entry = JournalEntry(
        client_id=client_id,
        entry_date=date(2026, 3, 31),
        description="Worksheet AJE",
        entry_type="Adjusting",
        aje_reference="AJE-007",
        lines=[
            JournalEntryLine(account_id=accounts["expense"], debit=25, credit=0),
            JournalEntryLine(account_id=accounts["revenue"], debit=0, credit=25),
        ],
    )
    entry.save()

    _select_client(monkeypatch, client_id)
    journal = AppTest.from_file(page_path("pages/2_Journal_Entries.py"), default_timeout=30
    )
    journal.session_state["edit_entry_id"] = entry.id
    journal.run()
    assert not journal.exception
    next(b for b in journal.button if b.label == "Save Entry").click().run()

    assert not journal.exception
    saved = JournalEntry.get_by_id(entry.id, client_id=client_id)
    assert saved.aje_reference == "AJE-007"


def test_dashboard_balances_show_totals_and_equation(client_id, accounts, monkeypatch):
    """Every section totals, equity is shown, and the equation check passes."""
    post_entry(
        client_id, date(2026, 1, 10),
        [(accounts["cash"], 900, 0), (accounts["equity"], 0, 900)],
    )
    post_entry(
        client_id, date(2026, 2, 5),
        [(accounts["cash"], 300, 0), (accounts["revenue"], 0, 300)],
    )
    post_entry(
        client_id, date(2026, 2, 20),
        [(accounts["expense"], 120, 0), (accounts["cash"], 0, 120)],
    )
    post_entry(
        client_id, date(2026, 3, 3),
        [(accounts["expense"], 50, 0), (accounts["credit_card"], 0, 50)],
    )

    _select_client(monkeypatch, client_id)
    dashboard = AppTest.from_file(page_path("pages/7_Dashboard.py"), default_timeout=30).run()
    assert not dashboard.exception

    # The balances summary renders as financial statements via st.html.
    html = "\n".join(str(e.body) for e in dashboard.get("html") if hasattr(e, "body"))
    for label in ["Total assets", "Total liabilities", "Total equity",
                  "Total revenue", "Total expenses", "Net income (fiscal YTD)",
                  ">Equity<"]:
        assert label in html, f"missing {label!r}"
    assert "$1,080.00" in html  # total assets: 900 + 300 - 120

    success = "\n".join(str(s.value) for s in dashboard.success)
    assert "In balance" in success
    assert "assets $1,080.00" in success
    # liabilities 50 + equity 900 + net income (300 - 170) = 1,080
    assert "liabilities $50.00" in success
    assert "net income $130.00" in success

    captions = "\n".join(str(c.value) for c in dashboard.caption)
    assert "As of" in captions


def test_journal_delete_requires_confirmation(client_id, accounts, monkeypatch):
    _select_client(monkeypatch, client_id)
    entry = post_entry(
        client_id,
        date(2026, 1, 15),
        [(accounts["cash"], 40, 0), (accounts["revenue"], 0, 40)],
    )
    journal = AppTest.from_file(page_path("pages/2_Journal_Entries.py"), default_timeout=30
    )
    journal.session_state["journal_active_tab"] = "View Entries"
    journal.run()

    journal.button(key=f"delete_entry_{entry.id}").click().run()
    assert JournalEntry.get_by_id(entry.id) is not None
    assert journal.button(key=f"confirm_delete_entry_{entry.id}")

    journal.button(key=f"confirm_delete_entry_{entry.id}").click().run()
    assert JournalEntry.get_by_id(entry.id) is None


def test_correction_draft_shows_original_and_retains_visible_chain(
    client_id, accounts, monkeypatch
):
    _select_client(monkeypatch, client_id)
    original = post_entry(
        client_id,
        date.today(),
        [(accounts["cash"], 75, 0), (accounts["revenue"], 0, 75)],
    )
    cash = Account.get_by_id(accounts["cash"], client_id=client_id)
    revenue = Account.get_by_id(accounts["revenue"], client_id=client_id)
    result = mcp_tools.propose_correction(
        client_id,
        original.id,
        date.today().isoformat(),
        "Correct duplicate posting",
        [
            {"account_number": cash.account_number, "credit": 75},
            {"account_number": revenue.account_number, "debit": 75},
        ],
        rationale="Duplicate identified during review.",
    )

    journal = AppTest.from_file(
        page_path("pages/2_Journal_Entries.py"), default_timeout=30
    )
    journal.session_state["journal_active_tab"] = "Drafts"
    journal.run()
    assert not journal.exception
    body = " ".join(str(item.value) for item in journal.markdown)
    notices = " ".join(str(item.value) for item in journal.info)
    assert f"Original · JE #{original.id}" in body
    assert f"Proposed correction · Draft #{result['draft_id']}" in body
    assert "Review both sides before deciding" in notices

    reversal_page = AppTest.from_file(
        page_path("pages/2_Journal_Entries.py"), default_timeout=30
    )
    reversal_page.session_state["journal_active_tab"] = "Reverse Entry"
    reversal_page.run()
    reversal_page.number_input(key="reversal_entry_id").set_value(original.id).run()
    assert reversal_page.button(key="post_reversal").disabled
    warnings = " ".join(str(item.value) for item in reversal_page.warning)
    assert "Resolve pending correction draft" in warnings

    journal.button(key=f"draft_approve_{result['draft_id']}").click().run()
    stored = DraftEntry.get_by_id(result["draft_id"], client_id)
    assert stored.status == "approved" and stored.posted_entry_id

    journal.session_state["journal_active_tab"] = "View Entries"
    journal.run()
    captions = " ".join(str(item.value) for item in journal.caption)
    assert (
        f"JE #{original.id} → draft #{stored.id} → "
        f"journal entry #{stored.posted_entry_id}"
    ) in captions


def test_imported_journal_uses_guided_category_correction(
    client_id, accounts, monkeypatch
):
    _select_client(monkeypatch, client_id)
    travel = Account(
        client_id=client_id,
        account_number="6100",
        name="Travel Expense",
        type="Expense",
    )
    travel.save()
    original, _ = post_transaction(
        client_id=client_id,
        transaction={
            "date": date(2026, 1, 10),
            "description": "Imported merchant",
            "amount": -80,
            "source_id": "guided-correction",
            "source_filename": "bank.csv",
            "source_row_number": 2,
        },
        target_account_id=accounts["expense"],
        bank_account_id=accounts["cash"],
        batch_id="guided-correction",
        learn=False,
    )
    journal = AppTest.from_file(page_path("pages/2_Journal_Entries.py"), default_timeout=30
    )
    journal.session_state["journal_active_tab"] = "View Entries"
    journal.run()

    assert journal.button(key=f"correct_import_{original.id}")
    assert not any(
        button.key == f"edit_entry_{original.id}" for button in journal.button
    )
    journal.button(key=f"correct_import_{original.id}").click().run()
    assert any("Correct category" in heading.value for heading in journal.subheader)

    journal.selectbox(key=f"correction_target_{original.id}").set_value(travel.id).run()
    journal.text_input(key=f"correction_reason_{original.id}").set_value(
        "Client travel"
    ).run()
    journal.button(key=f"post_correction_{original.id}").click().run()

    assert not journal.exception
    assert JournalEntry.count(client_id) == 2
    link = ImportedTransaction.get_links_for_journal_entries(client_id, [original.id])[
        original.id
    ]
    assert link["suggested_account_id"] == travel.id


def test_import_history_reverses_batch_into_review_queue(
    client_id, accounts, monkeypatch
):
    _select_client(monkeypatch, client_id)
    _, imported = post_transaction(
        client_id=client_id,
        transaction={
            "date": date(2026, 1, 10),
            "description": "Wrong-account import",
            "amount": -55,
            "source_id": "reverse-page-source",
            "source_filename": "bank.csv",
            "source_row_number": 2,
        },
        target_account_id=accounts["expense"],
        bank_account_id=accounts["cash"],
        batch_id="reverse-page",
        learn=False,
    )
    page = AppTest.from_file(
        page_path("pages/4_Import_Transactions.py"), default_timeout=30
    )
    page.session_state["import_active_tab"] = "Import History"
    page.run()
    assert not page.exception

    button = page.button(key="reverse_import_batch_reverse-page")
    assert button.disabled
    page.text_area(key="batch_reversal_reason_reverse-page").set_value(
        "Imported against the wrong account"
    ).run()
    page.checkbox(key="batch_reversal_confirmation_reverse-page").check().run()
    assert not page.button(key="reverse_import_batch_reverse-page").disabled
    page.button(key="reverse_import_batch_reverse-page").click().run()

    assert not page.exception
    assert page.session_state["import_active_tab"] == "Review & Categorize"
    original = ImportedTransaction.get_by_batch(client_id, "reverse-page")[0]
    assert original.id == imported.id
    assert original.status == "Reversed"
    pending = ImportedTransaction.get_by_status(client_id, "Pending")
    assert len(pending) == 1
    assert pending[0].replaces_transaction_id == imported.id
    assert len(page.session_state["transactions_to_review"]) == 1
    assert page.session_state["transactions_to_review"][0]["staged_id"] == pending[0].id

    transactions = AppTest.from_file(
        page_path("pages/6_Transactions.py"), default_timeout=30
    ).run()
    assert not transactions.exception
    transaction_captions = " ".join(str(item.value) for item in transactions.caption)
    assert f"Replacement for transaction #{imported.id}" in transaction_captions
    assert "Reversal JE #" not in transaction_captions

    next(box for box in transactions.selectbox if box.label == "Status").set_value(
        "Reversed"
    ).run()
    reversed_captions = " ".join(
        str(item.value) for item in transactions.caption
    )
    assert "Reversal JE #" in reversed_captions

    reports = AppTest.from_file(
        page_path("pages/5_Reports.py"), default_timeout=30
    )
    reports.session_state["active_report"] = "General Ledger"
    reports.run()
    assert not reports.exception
    assert reports.checkbox(key="gl_hide_reversed_imports").value is True
    assert any(
        "fully reversed imports" in str(item.value) for item in reports.info
    )
    reports.checkbox(key="gl_hide_reversed_imports").uncheck().run()
    assert not reports.exception
