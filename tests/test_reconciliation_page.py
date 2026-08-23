from datetime import date
from pathlib import Path

from streamlit.testing.v1 import AppTest

from tests.conftest import page_path

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

    at = AppTest.from_file(page_path("pages/10_Bank_Reconciliation.py"), default_timeout=30).run()

    assert not at.exception
    assert any("Bank Reconciliation" in title.value for title in at.title)
    assert any("Statement activity" in heading.value for heading in at.subheader)


def test_reconciliation_editor_state_is_owned_by_book_and_client(
    client_id, accounts, monkeypatch, tmp_path
):
    """Draft ids restart in every book, so the in-progress cleared-checkbox
    grid must rotate its widget key on any book or client switch."""
    from database import connection as dbconn
    from database.connection import init_database
    from models.account import Account
    from models.client import Client
    from models.reconciliation import BankReconciliation

    post_entry(
        client_id, date(2026, 1, 5),
        [(accounts["cash"], 100, 0), (accounts["revenue"], 0, 100)],
    )
    first_draft = BankReconciliation.create(
        client_id, accounts["cash"], date(2026, 1, 1), date(2026, 1, 31), 100
    )

    import utils.client_selector as selector
    monkeypatch.setattr(selector, "render_client_selector", lambda: client_id)

    at = AppTest.from_file(
        page_path("pages/10_Bank_Reconciliation.py"), default_timeout=30
    ).run()
    assert not at.exception
    assert at.session_state["_client_context_generation_bank_reconciliation"] == 0
    assert at.selectbox(key="account__bank_reconciliation_g0").value == accounts["cash"]

    second_book = tmp_path / "second-book.db"
    monkeypatch.setattr(dbconn, "DATABASE_PATH", second_book)
    init_database()
    second_client_id = Client(
        name="Same Id, Different Book"
    ).save(seed_accounts=False)
    assert second_client_id == client_id
    cash = Account(
        client_id=second_client_id, account_number="1000",
        name="Second Book Cash", type="Asset",
    )
    cash.save()
    revenue = Account(
        client_id=second_client_id, account_number="4000",
        name="Second Book Revenue", type="Revenue",
    )
    revenue.save()
    post_entry(
        second_client_id, date(2026, 2, 5),
        [(cash.id, 250, 0), (revenue.id, 0, 250)],
    )
    second_draft = BankReconciliation.create(
        second_client_id, cash.id, date(2026, 2, 1), date(2026, 2, 28), 250
    )
    assert second_draft.id == first_draft.id
    at.run()

    assert not at.exception
    # The ownership generation rotated, so every keyed widget — including the
    # cleared-items editor keyed by the colliding draft id — gets a fresh key.
    assert at.session_state["_client_context_generation_bank_reconciliation"] == 1
    assert at.selectbox(key="account__bank_reconciliation_g1").value == cash.id
    assert any(
        "February 28, 2026" in heading.value for heading in at.subheader
    )


def test_reconciliation_editor_key_derives_from_the_client_scope():
    """The editor grid must never be keyed by the bare draft id."""
    source = Path(page_path("pages/10_Bank_Reconciliation.py")).read_text()
    assert 'recon_key(f"editor_{draft.id}")' in source
    assert 'key=f"reconciliation_editor_' not in source
