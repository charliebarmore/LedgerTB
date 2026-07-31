from datetime import date, timedelta

from streamlit.testing.v1 import AppTest
import streamlit as st

from models.account import Account
from models.journal_entry import JournalEntry
from models.transaction import ImportedTransaction
from services.posting import post_transaction
from tests.conftest import post_entry


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

    transactions = AppTest.from_file("pages/6_Transactions.py", default_timeout=30).run()
    assert not transactions.exception
    assert any(metric.label == "Filtered Transactions" for metric in transactions.metric)
    assert any(button.label == "Next" and not button.disabled for button in transactions.button)

    journals = AppTest.from_file("pages/2_Journal_Entries.py", default_timeout=30).run()
    assert not journals.exception
    assert any(metric.label == "Filtered Entries" for metric in journals.metric)
    assert any(button.label == "Next" and not button.disabled for button in journals.button)

    audit = AppTest.from_file("pages/8_Audit_Trail.py", default_timeout=30).run()
    assert not audit.exception
    assert any(metric.label == "Total Changes" for metric in audit.metric)
    assert any(button.label == "Next" and not button.disabled for button in audit.button)


def test_year_close_checklist_page_renders(client_id, accounts, monkeypatch):
    _select_client(monkeypatch, client_id)
    worksheet = AppTest.from_file(
        "pages/1_Trial_Balance_Worksheet.py", default_timeout=30
    ).run()
    assert not worksheet.exception
    assert any(
        heading.value == "Year-close checklist" for heading in worksheet.subheader
    )


def test_journal_form_clears_keyed_line_widgets_after_save(
    client_id, accounts, monkeypatch
):
    _select_client(monkeypatch, client_id)
    journal = AppTest.from_file(
        "pages/2_Journal_Entries.py", default_timeout=30
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
    journal = AppTest.from_file(
        "pages/2_Journal_Entries.py", default_timeout=30
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
    journal = AppTest.from_file(
        "pages/2_Journal_Entries.py", default_timeout=30
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
    journal = AppTest.from_file(
        "pages/2_Journal_Entries.py", default_timeout=30
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
    dashboard = AppTest.from_file("pages/7_Dashboard.py", default_timeout=30).run()
    assert not dashboard.exception

    markdown = "\n".join(str(m.value) for m in dashboard.markdown)
    for label in ["Total assets", "Total liabilities", "Total equity",
                  "Total revenue", "Total expenses", "Net income (fiscal YTD)",
                  "**Equity**"]:
        assert label in markdown, f"missing {label!r}"
    assert "$1,080.00" in markdown  # total assets: 900 + 300 - 120

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
    journal = AppTest.from_file(
        "pages/2_Journal_Entries.py", default_timeout=30
    ).run()

    journal.button(key=f"delete_entry_{entry.id}").click().run()
    assert JournalEntry.get_by_id(entry.id) is not None
    assert journal.button(key=f"confirm_delete_entry_{entry.id}")

    journal.button(key=f"confirm_delete_entry_{entry.id}").click().run()
    assert JournalEntry.get_by_id(entry.id) is None


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
    journal = AppTest.from_file(
        "pages/2_Journal_Entries.py", default_timeout=30
    ).run()

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
