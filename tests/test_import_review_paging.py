"""State regressions for paging and independent human bulk selection."""
from datetime import date
import pytest
import time

from tests.test_jev_review import page
from utils.import_review import ensure_row_ids, row_key


def test_bulk_category_and_clear_selection_preserve_inclusion(monkeypatch, client_id, accounts, fake_credential_vault):
    at, row = page(monkeypatch, client_id, accounts, fake_credential_vault)
    fake_credential_vault["categorization_provider"] = "off"
    at.run()
    at.multiselect(key="bulk_rows").set_value([row["uid"]]).run()
    at.selectbox(key="bulk_account_select").set_value(accounts["expense"]).run()
    next(b for b in at.button if b.label == "Apply to Selected").click().run()
    assert not at.exception
    assert at.session_state[row_key("cat", row)] == accounts["expense"]
    assert not at.session_state[row_key("include", row)]
    assert at.multiselect(key="bulk_rows").value == []
    at.button(key="select_bulk").click().run()
    at.button(key="deselect_bulk").click().run()
    assert at.multiselect(key="bulk_rows").value == []
    assert not at.session_state[row_key("include", row)]


def test_paging_preserves_edits_and_posts_only_included_rows(monkeypatch, client_id, accounts, fake_credential_vault):
    from database.connection import get_cursor
    at, _ = page(monkeypatch, client_id, accounts, fake_credential_vault)
    fake_credential_vault["categorization_provider"] = "off"
    rows = ensure_row_ids([dict(date=date(2026, 9, 1), description=f"Synthetic row {i}",
                              amount=-33.33, bank_account_id=accounts["cash"],
                              batch_id="synthetic-pagination", include=False) for i in range(51)])
    rows[0]["include"] = rows[-1]["include"] = True
    at.session_state["transactions_to_review"] = rows
    at.run()
    assert not at.exception
    assert len([c for c in at.checkbox if c.label == "Include for posting"]) == 50
    at.selectbox(key=row_key("cat", rows[0])).set_value(accounts["expense"]).run()
    at.checkbox(key=row_key("include", rows[0])).uncheck().run()
    at.selectbox(key="review_page").set_value(2).run()
    assert not at.exception
    assert len([c for c in at.checkbox if c.label == "Include for posting"]) == 1
    at.selectbox(key=row_key("cat", rows[-1])).set_value(accounts["expense"]).run()
    at.selectbox(key="review_page").set_value(1).run()
    assert at.selectbox(key=row_key("cat", rows[0])).value == accounts["expense"]
    assert not at.checkbox(key=row_key("include", rows[0])).value
    next(b for b in at.button if b.label == "Post Transactions").click().run()
    assert not at.exception
    with get_cursor() as cursor:
        entries = cursor.execute("SELECT * FROM journal_entries WHERE client_id = ?", (client_id,)).fetchall()
        assert len(entries) == 1 and entries[0]["description"] == "Synthetic row 50"
        lines = cursor.execute("SELECT debit, credit FROM journal_entry_lines").fetchall()
        assert sum(r["debit"] for r in lines) == sum(r["credit"] for r in lines) == 3333


@pytest.mark.performance
def test_ten_thousand_row_review_renders_one_page_without_cloud_calls(monkeypatch, client_id, accounts, fake_credential_vault):
    from services import jev_categorization as jev
    at, _ = page(monkeypatch, client_id, accounts, fake_credential_vault)
    rows = ensure_row_ids([dict(date=date(2026, 9, 1), description=f"Large synthetic import {i:05}",
                              amount=-12.34, bank_account_id=accounts["cash"], include=False)
                           for i in range(10_000)])
    at.session_state["transactions_to_review"] = rows
    def unexpected(*args, **kwargs):
        raise AssertionError("Rendering must not prepare or send unselected cloud inputs")
    monkeypatch.setattr(jev, "request_input", unexpected)
    monkeypatch.setattr(jev, "send_request", unexpected)
    start = time.monotonic()
    at.run(timeout=120)
    elapsed = time.monotonic() - start
    assert not at.exception
    assert len([c for c in at.checkbox if c.label == "Include for posting"]) == 50
    assert len(at.selectbox(key="review_page").options) == 200
    assert len(at.session_state["transactions_to_review"]) == 10_000
    assert all(not row["include"] for row in at.session_state["transactions_to_review"])
    print(f"10,000-row review rendered 50 row controls in {elapsed:.3f}s")
