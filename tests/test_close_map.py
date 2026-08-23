from datetime import date

import pytest
from streamlit.testing.v1 import AppTest

from database.connection import get_connection
from models import close_map
from models.account import Account
from models.fiscal_period import FiscalPeriod
from models.journal_entry import JournalEntry, JournalEntryLine
from tests.conftest import page_path


def _period(client_id, year=2026):
    period = FiscalPeriod(
        client_id=client_id, period_name=f"FY {year}", period_type="Year",
        start_date=date(year, 1, 1), end_date=date(year, 12, 31),
    )
    period.save()
    return period


def _post(client_id, when, debit_account, credit_account, amount=100):
    return JournalEntry(
        client_id=client_id, entry_date=when, description="close map test",
        lines=[
            JournalEntryLine(account_id=debit_account, debit=amount),
            JournalEntryLine(account_id=credit_account, credit=amount),
        ],
    ).save()


def _row(summary, account_id):
    return next(row for row in summary["rows"] if row.account_id == account_id)


def test_review_lifecycle_and_account_scoped_invalidation(client_id, accounts):
    period = _period(client_id)
    _post(client_id, date(2026, 1, 2), accounts["cash"], accounts["revenue"])

    summary = close_map.readiness(client_id, period.id)
    assert summary["ready"] is False
    assert _row(summary, accounts["cash"]).status == close_map.NOT_STARTED

    close_map.save_explanation(
        client_id, period.id, accounts["cash"], "Agrees to the year-end statement."
    )
    close_map.add_evidence(
        client_id, period.id, accounts["cash"], "workpaper", "A-1",
        "Year-end bank reconciliation",
    )
    close_map.signoff(client_id, period.id, accounts["cash"], "preparer")
    assert _row(close_map.readiness(client_id, period.id), accounts["cash"]).status == close_map.PREPARED

    close_map.signoff(client_id, period.id, accounts["cash"], "reviewer")
    reviewed = _row(close_map.readiness(client_id, period.id), accounts["cash"])
    assert reviewed.status == close_map.REVIEWED
    assert reviewed.reviewed_by

    # Changing a different account does not invalidate Cash.
    _post(client_id, date(2026, 2, 2), accounts["expense"], accounts["credit_card"])
    assert _row(close_map.readiness(client_id, period.id), accounts["cash"]).status == close_map.REVIEWED

    # Any later ledger line affecting Cash makes its prior signoff visibly stale.
    _post(client_id, date(2026, 3, 2), accounts["cash"], accounts["revenue"], 25)
    changed = _row(close_map.readiness(client_id, period.id), accounts["cash"])
    assert changed.status == close_map.CHANGED
    assert changed.current_balance == 125.0


def test_notes_and_evidence_are_part_of_signed_contents(client_id, accounts):
    period = _period(client_id)
    _post(client_id, date(2026, 1, 2), accounts["cash"], accounts["revenue"])
    close_map.add_evidence(
        client_id, period.id, accounts["cash"], "workpaper", "A-1",
        "Year-end bank reconciliation",
    )
    close_map.save_explanation(
        client_id, period.id, accounts["cash"], "Agrees to bank support."
    )
    close_map.signoff(client_id, period.id, accounts["cash"], "preparer")
    close_map.add_note(client_id, period.id, accounts["cash"], "Explain outstanding check.")

    row = _row(close_map.readiness(client_id, period.id), accounts["cash"])
    assert row.status == close_map.EXCEPTION
    assert row.evidence_count == 1
    assert row.open_note_count == 1
    with pytest.raises(ValueError, match="Resolve the open review notes"):
        close_map.signoff(client_id, period.id, accounts["cash"], "reviewer")

    detail = close_map.account_detail(client_id, period.id, accounts["cash"])
    close_map.resolve_note(client_id, detail["notes"][0]["id"], "Cleared after year-end.")
    assert _row(close_map.readiness(client_id, period.id), accounts["cash"]).status == close_map.CHANGED


def test_reviewer_requires_current_preparer_and_assistant_cannot_sign(
    client_id, accounts, monkeypatch
):
    period = _period(client_id)
    _post(client_id, date(2026, 1, 2), accounts["cash"], accounts["revenue"])
    close_map.save_explanation(
        client_id, period.id, accounts["cash"], "Agrees to year-end support."
    )
    close_map.add_evidence(
        client_id, period.id, accounts["cash"], "workpaper", "A-1",
        "Year-end support",
    )
    with pytest.raises(ValueError, match="preparer signoff"):
        close_map.signoff(client_id, period.id, accounts["cash"], "reviewer")

    from utils import actor
    monkeypatch.setattr(actor, "_ASSISTANT", True)
    with pytest.raises(PermissionError, match="cannot sign off"):
        close_map.signoff(client_id, period.id, accounts["cash"], "preparer")


def test_not_required_needs_reason_and_is_removed_from_readiness(client_id, accounts):
    period = _period(client_id)
    _post(client_id, date(2026, 1, 2), accounts["cash"], accounts["revenue"])
    with pytest.raises(ValueError, match="Explain why"):
        close_map.save_mapping(client_id, accounts["cash"], None, False)

    close_map.save_mapping(
        client_id, accounts["cash"], None, False, "Reviewed in another system."
    )
    row = _row(close_map.readiness(client_id, period.id), accounts["cash"])
    assert row.status == close_map.NOT_REQUIRED
    assert row.required is False


def test_assistant_explanation_is_a_proposal_until_human_accepts(
    client_id, accounts, monkeypatch
):
    period = _period(client_id)
    _post(client_id, date(2026, 1, 2), accounts["cash"], accounts["revenue"])
    from utils import actor
    monkeypatch.setattr(actor, "_ASSISTANT", True)
    proposal_id = close_map.propose_explanation(
        client_id, period.id, accounts["cash"], "Balance agrees to bank support.",
        "Based on the completed reconciliation.",
    )
    assert _row(close_map.readiness(client_id, period.id), accounts["cash"]).explanation == ""

    monkeypatch.setattr(actor, "_ASSISTANT", False)
    close_map.resolve_proposal(client_id, proposal_id, True)
    row = _row(close_map.readiness(client_id, period.id), accounts["cash"])
    assert row.explanation == "Balance agrees to bank support."
    assert row.status == close_map.IN_PROGRESS


def test_group_assignment_is_client_scoped(client_id, accounts):
    group_id = close_map.create_group(client_id, "A", "Cash")
    close_map.save_mapping(client_id, accounts["cash"], group_id, True)
    assert close_map.list_groups(client_id)[0]["code"] == "A"

    other = Account(client_id=client_id, account_number="1010", name="Savings", type="Asset")
    other.save()
    with pytest.raises(ValueError, match="Lead-sheet group"):
        close_map.save_mapping(client_id, other.id, group_id + 999, True)

    close_map.update_group(client_id, group_id, "A1", "Cash and equivalents")
    close_map.bulk_assign_group(
        client_id, [accounts["cash"], other.id], group_id
    )
    period = _period(client_id)
    _post(client_id, date(2026, 1, 2), other.id, accounts["revenue"])
    _post(client_id, date(2026, 1, 3), accounts["cash"], accounts["revenue"], 5)
    grouped = close_map.readiness(client_id, period.id)["rows"]
    assert {row.group_code for row in grouped if row.account_id in {accounts["cash"], other.id}} == {"A1"}


def test_prior_year_context_rolls_forward_without_support_or_signoff(
    client_id, accounts
):
    prior = _period(client_id, 2025)
    current = _period(client_id, 2026)
    _post(client_id, date(2025, 1, 2), accounts["cash"], accounts["revenue"], 100)

    group_id = close_map.create_group(client_id, "A", "Cash")
    close_map.save_mapping(client_id, accounts["cash"], group_id, True)
    close_map.save_explanation(
        client_id, prior.id, accounts["cash"],
        "Agrees to the 2025 bank reconciliation; variance reflects collections.",
    )
    close_map.add_evidence(
        client_id, prior.id, accounts["cash"], "workpaper", "A-1",
        "2025 year-end bank reconciliation",
    )
    note_id = close_map.add_note(
        client_id, prior.id, accounts["cash"], "Confirm the outstanding deposit."
    )
    close_map.resolve_note(client_id, note_id, "Deposit cleared in January 2026.")
    close_map.signoff(client_id, prior.id, accounts["cash"], "preparer")
    close_map.signoff(client_id, prior.id, accounts["cash"], "reviewer")

    _post(client_id, date(2026, 1, 2), accounts["cash"], accounts["revenue"], 25)
    detail = close_map.account_detail(client_id, current.id, accounts["cash"])

    # The reusable lead-sheet mapping carries forward automatically.
    assert detail["row"].group_code == "A"

    # Period-specific work starts clean and therefore needs fresh support and signoff.
    assert detail["row"].explanation == ""
    assert detail["row"].status == close_map.NOT_STARTED
    assert detail["evidence"] == []
    assert detail["notes"] == []
    assert detail["row"].prepared_by == ""
    assert detail["row"].reviewed_by == ""

    # Last year's work remains visible as reference-only review context.
    context = detail["prior_year_context"]
    assert context["period_name"] == "FY 2025"
    assert context["explanation"].startswith("Agrees to the 2025")
    assert context["evidence"][0]["reference"] == "A-1"
    assert context["notes"][0]["status"] == "resolved"
    assert context["notes"][0]["resolution"] == "Deposit cleared in January 2026."
    assert context["prepared_by"]
    assert context["reviewed_by"]

    close_map.save_explanation(
        client_id, current.id, accounts["cash"], "Agrees to the 2026 reconciliation."
    )
    with pytest.raises(ValueError, match="current-year evidence"):
        close_map.signoff(client_id, current.id, accounts["cash"], "preparer")
    close_map.add_evidence(
        client_id, current.id, accounts["cash"], "workpaper", "A-1-2026",
        "2026 year-end bank reconciliation",
    )
    close_map.signoff(client_id, current.id, accounts["cash"], "preparer")
    assert _row(
        close_map.readiness(client_id, current.id), accounts["cash"]
    ).status == close_map.PREPARED


def test_prior_year_context_requires_an_adjacent_fiscal_year(client_id, accounts):
    old = _period(client_id, 2024)
    current = _period(client_id, 2026)
    _post(client_id, date(2024, 1, 2), accounts["cash"], accounts["revenue"])
    close_map.save_explanation(client_id, old.id, accounts["cash"], "Old context.")
    _post(client_id, date(2026, 1, 2), accounts["cash"], accounts["revenue"])

    detail = close_map.account_detail(client_id, current.id, accounts["cash"])
    assert detail["prior_year_context"] is None


def test_preparer_signoff_requires_an_explanation(client_id, accounts):
    period = _period(client_id)
    _post(client_id, date(2026, 1, 2), accounts["cash"], accounts["revenue"])
    with pytest.raises(ValueError, match="Explain the balance"):
        close_map.signoff(client_id, period.id, accounts["cash"], "preparer")


def test_close_map_tables_are_migrated(db):
    conn = get_connection()
    names = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()}
    conn.close()
    assert {"lead_sheet_groups", "account_close_reviews",
            "account_close_signoffs", "close_review_proposals"} <= names


def test_close_map_page_renders_account_readiness(client_id, accounts, monkeypatch):
    prior = _period(client_id, 2025)
    _period(client_id, 2026)
    _post(client_id, date(2025, 1, 2), accounts["cash"], accounts["revenue"])
    close_map.save_explanation(
        client_id, prior.id, accounts["cash"], "Prior-year cash explanation."
    )
    _post(client_id, date(2026, 1, 2), accounts["cash"], accounts["revenue"])
    import utils.client_selector as selector
    monkeypatch.setattr(selector, "render_client_selector", lambda: client_id)

    at = AppTest.from_file(
        page_path("pages/14_Close_Map.py"), default_timeout=30
    ).run()

    assert not at.exception
    assert any("Close Map" in title.value for title in at.title)
    assert any("1000" in option for option in at.selectbox(key="account__close_map_g0").options)
    assert any(
        "Prior-year review context — FY 2025" in expander.label
        for expander in at.expander
    )
    assert any("Reference only" in caption.value for caption in at.caption)


def test_close_map_resolution_input_is_owned_by_book_and_client(
    client_id, accounts, monkeypatch, tmp_path
):
    """A typed-but-unsaved resolution must never surface under another
    client's same-numbered note — note ids restart in every book."""
    from database import connection as dbconn
    from database.connection import init_database
    from models.client import Client

    period = _period(client_id)
    _post(client_id, date(2026, 1, 2), accounts["cash"], accounts["revenue"])
    first_note_id = close_map.add_note(
        client_id, period.id, accounts["cash"], "First book open question"
    )

    import utils.client_selector as selector
    monkeypatch.setattr(selector, "render_client_selector", lambda: client_id)

    at = AppTest.from_file(
        page_path("pages/14_Close_Map.py"), default_timeout=30
    ).run()
    assert not at.exception
    at.text_input(
        key=f"resolution_{first_note_id}__close_map_g0"
    ).set_value("FIRST BOOK DRAFT RESOLUTION").run()

    second_book = tmp_path / "second-book.db"
    monkeypatch.setattr(dbconn, "DATABASE_PATH", second_book)
    init_database()
    second_client_id = Client(
        name="Same Id, Different Book"
    ).save(seed_accounts=False)
    assert second_client_id == client_id
    from models.account import Account
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
    second_period = _period(second_client_id)
    _post(second_client_id, date(2026, 1, 2), cash.id, revenue.id)
    second_note_id = close_map.add_note(
        second_client_id, second_period.id, cash.id, "Second book open question"
    )
    assert second_note_id == first_note_id
    at.run()

    assert not at.exception
    resolution = at.text_input(key=f"resolution_{second_note_id}__close_map_g1")
    assert resolution.value in ("", None)
    rendered = " ".join(str(item.value) for item in at.text_input)
    assert "FIRST BOOK DRAFT RESOLUTION" not in rendered
