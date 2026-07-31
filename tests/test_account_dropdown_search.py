"""Account dropdowns must be type-to-search friendly.

The old empty state was a real option labeled "-- Select Account --". A
selectbox's search input starts from the selected option's label, so typing an
account number produced "-- Select Account --300" and matched nothing. The
empty state is now Streamlit's native index=None, which leaves the search box
blank; nothing in the option list may be a placeholder pretending to be an
account.
"""
from streamlit.testing.v1 import AppTest
import streamlit as st

from utils.import_review import ensure_row_ids

SENTINEL = "-- Select Account --"


def _import_page(monkeypatch, client_id):
    import utils.client_selector as selector

    monkeypatch.setattr(selector, "render_client_selector", lambda: client_id)
    monkeypatch.setattr(selector, "apply_sidebar_style", lambda *a, **k: None)
    monkeypatch.setattr(st, "page_link", lambda *args, **kwargs: None)
    return AppTest.from_file("pages/4_Import_Transactions.py", default_timeout=60)


def _staged_rows(accounts):
    rows = [{
        "date": "2026-01-12",
        "description": "CWR DIGITAL LLC",
        "amount": -79.00,
        "bank_account_id": accounts["cash"],
    }]
    ensure_row_ids(rows)
    return rows


def test_review_dropdowns_have_no_placeholder_option(client_id, accounts, monkeypatch):
    page = _import_page(monkeypatch, client_id)
    page.session_state["import_active_tab"] = "Review & Categorize"
    page.session_state["transactions_to_review"] = _staged_rows(accounts)
    page.run()

    assert not page.exception
    for box in page.selectbox:
        labels = [str(option) for option in box.options]
        assert not any(SENTINEL in label for label in labels), (
            f"selectbox {box.key} still offers the placeholder pseudo-option"
        )


def test_uncategorized_row_starts_empty_and_still_blocks_posting(
    client_id, accounts, monkeypatch
):
    page = _import_page(monkeypatch, client_id)
    page.session_state["import_active_tab"] = "Review & Categorize"
    page.session_state["transactions_to_review"] = _staged_rows(accounts)
    page.run()

    assert not page.exception
    assert page.selectbox(key="bulk_account_select").value is None
    # The row dict keeps 0 for "unset" so posting validation is unchanged.
    assert page.session_state["transactions_to_review"][0]["selected_account_id"] == 0


def test_journal_entry_lines_have_no_placeholder_option(
    client_id, accounts, monkeypatch
):
    import utils.client_selector as selector

    monkeypatch.setattr(selector, "render_client_selector", lambda: client_id)
    monkeypatch.setattr(selector, "apply_sidebar_style", lambda *a, **k: None)
    monkeypatch.setattr(st, "page_link", lambda *args, **kwargs: None)
    journal = AppTest.from_file("pages/2_Journal_Entries.py", default_timeout=30).run()

    assert not journal.exception
    line_box = journal.selectbox(key="account_0")
    assert line_box.value is None
    labels = [str(option) for option in line_box.options]
    assert not any(SENTINEL in label for label in labels)
