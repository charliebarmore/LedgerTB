"""A finished import must not hide the next one.

Review & Categorize shows a "What's next?" screen when ``import_complete`` is
set, and ends the script with st.stop(). The flag was only ever cleared by the
two buttons on that screen, so parsing a second file — which navigates straight
to Review & Categorize — displayed the previous batch's success message and
stopped before rendering the newly staged rows. They looked like they had
vanished.
"""
from streamlit.testing.v1 import AppTest

from tests.conftest import page_path
import streamlit as st

from utils.import_review import ensure_row_ids


def _page(monkeypatch, client_id):
    import utils.client_selector as selector

    monkeypatch.setattr(selector, "render_client_selector", lambda: client_id)
    monkeypatch.setattr(selector, "apply_sidebar_style", lambda *a, **k: None)
    monkeypatch.setattr(st, "page_link", lambda *args, **kwargs: None)
    return AppTest.from_file(page_path("pages/4_Import_Transactions.py"), default_timeout=60)


def _staged_rows(accounts):
    rows = [{
        "date": "2026-01-12",
        "description": "ACME HOSTING LLC",
        "amount": -79.00,
        "bank_account_id": accounts["cash"],
    }]
    ensure_row_ids(rows)
    return rows


def test_completion_screen_offers_a_way_out_that_clears_the_flag(client_id, accounts, monkeypatch):
    """The state the user got stuck in, and the escape from it.

    AppTest cannot drive a file_uploader, so the parse-clears-the-flag path
    itself is covered by the browser check rather than here. What is asserted
    here is that a set flag is always escapable and that escaping really clears
    it — otherwise the stuck state has no exit at all.
    """
    page = _page(monkeypatch, client_id)
    page.session_state["import_active_tab"] = "Review & Categorize"
    page.session_state["transactions_to_review"] = _staged_rows(accounts)
    # The state left behind by the previous batch.
    page.session_state["import_complete"] = True
    page.session_state["import_complete_msg"] = "Created 43 journal entries! (2 excluded)"
    page.run()

    assert not page.exception
    assert any("Import More Transactions" in b.label for b in page.button), (
        "the What's next? screen should still offer a way out"
    )

    next_button = next(b for b in page.button if "Import More Transactions" in b.label)
    next_button.click().run()
    assert page.session_state["import_complete"] is False
    assert page.session_state["import_active_tab"] == "Upload CSV"


def test_import_more_button_keeps_staged_rows(client_id, accounts, monkeypatch):
    """The recovery path must not also discard rows already parsed."""
    page = _page(monkeypatch, client_id)
    page.session_state["import_active_tab"] = "Review & Categorize"
    page.session_state["transactions_to_review"] = _staged_rows(accounts)
    page.session_state["import_complete"] = True
    page.session_state["import_complete_msg"] = "Created 43 journal entries!"
    page.run()

    next_button = next(b for b in page.button if "Import More Transactions" in b.label)
    next_button.click().run()

    assert not page.exception
    assert len(page.session_state["transactions_to_review"]) == 1


def test_review_tab_renders_staged_rows_once_the_flag_is_clear(client_id, accounts, monkeypatch):
    page = _page(monkeypatch, client_id)
    page.session_state["import_active_tab"] = "Review & Categorize"
    page.session_state["transactions_to_review"] = _staged_rows(accounts)
    page.session_state["import_complete"] = False
    page.run()

    assert not page.exception
    headings = [str(element.value) for element in page.subheader]
    assert any("Review & Categorize" in h for h in headings)
    # The completion screen must be gone.
    assert not any("What's next?" in h for h in headings)
