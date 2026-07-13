from datetime import date, timedelta

from streamlit.testing.v1 import AppTest
import streamlit as st

from models.transaction import ImportedTransaction
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
