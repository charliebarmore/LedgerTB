from datetime import date

from streamlit.testing.v1 import AppTest

from conftest import post_entry


def test_reconciliation_page_renders_draft_and_activity(client_id, accounts, monkeypatch):
    post_entry(
        client_id, date(2026, 1, 5),
        [(accounts["cash"], 100, 0), (accounts["revenue"], 0, 100)],
    )

    import utils.client_selector as selector
    monkeypatch.setattr(selector, "render_client_selector", lambda: client_id)

    from models.reconciliation import BankReconciliation
    BankReconciliation.create(
        client_id, accounts["cash"], date(2026, 1, 1), date(2026, 1, 31), 100
    )

    at = AppTest.from_file("pages/10_Bank_Reconciliation.py", default_timeout=30).run()

    assert not at.exception
    assert any("Bank Reconciliation" in title.value for title in at.title)
    assert any("Statement activity" in heading.value for heading in at.subheader)
