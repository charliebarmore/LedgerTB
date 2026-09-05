"""Generation, edits, approval, and recovery must preserve draft instructions."""

from datetime import date

import pytest

from models.draft_entry import DraftEntry
from models.fiscal_period import FiscalPeriod
from models.journal_entry import JournalEntry
from models.recurring_entry import JournalEntryTemplate, RecurringSchedule, TemplateLine
from services.recurring_entries import generate_occurrence
from services.recurring_entries import regenerate_occurrence, recurring_draft_context
from database.connection import get_cursor
from models.audit_log import AuditLog


def _generated(client_id, accounts, reversal_rule):
    FiscalPeriod.ensure_periods_exist(client_id, 2026, 12)
    template = JournalEntryTemplate(
        client_id=client_id, name="Original accrual", description="January accrual",
        entry_type="Adjusting", source_reference="Original workpaper",
        lines=[TemplateLine(accounts["expense"], debit_cents=12_345),
               TemplateLine(accounts["credit_card"], credit_cents=12_345)],
    )
    template.save()
    schedule = RecurringSchedule(
        template_id=template.id, starts_on=date(2026, 1, 1),
        reversal_rule=reversal_rule,
    )
    schedule.save()
    generated = generate_occurrence(
        client_id, schedule.id, date(2026, 1, 1), date(2026, 1, 31),
    )
    return template, schedule, DraftEntry.get_by_id(generated["draft_id"], client_id)


@pytest.mark.parametrize("initial,updated,expected_reversals", [
    ("NextDay", "None", 1),
    ("None", "NextDay", 0),
    ("NextDay", "NextDay", 1),
    ("None", "None", 0),
])
def test_pending_primary_preserves_generation_time_reversal_rule(
    client_id, accounts, initial, updated, expected_reversals,
):
    _, schedule, draft = _generated(client_id, accounts, initial)
    schedule.reversal_rule = updated
    schedule.save()
    draft.approve()
    assert len(DraftEntry.get_pending(client_id)) == expected_reversals


def test_pending_primary_preserves_generation_time_source_reference(client_id, accounts):
    template, _, draft = _generated(client_id, accounts, "None")
    template.name = "Future accrual"
    template.source_reference = "Future workpaper"
    template.save()
    posted = JournalEntry.get_by_id(draft.approve(), client_id)
    assert "Original workpaper" in posted.source_reference
    assert "Future workpaper" not in posted.source_reference


def test_new_generation_adopts_edits_but_reversal_keeps_primary_snapshot(client_id, accounts):
    template, schedule, first = _generated(client_id, accounts, "None")
    first.reject()
    template.name = "Revised accrual"
    template.source_reference = "Revised workpaper"
    template.lines[0].debit_cents = template.lines[1].credit_cents = 25_000
    template.save()
    schedule.reversal_rule = "NextDay"
    schedule.save()
    with get_cursor() as cursor:
        context = recurring_draft_context(cursor.connection, first.id, client_id)
    result = regenerate_occurrence(client_id, context["occurrence_id"])
    replacement = DraftEntry.get_by_id(result["draft_id"], client_id)
    assert replacement.lines[0].debit_cents == 25_000
    assert result["generation_number"] == 2
    posted_id = replacement.approve()
    assert "Revised workpaper" in JournalEntry.get_by_id(posted_id).source_reference
    reversal = DraftEntry.get_pending(client_id)[0]
    reversal.reject()

    template.name = "Unrelated future name"
    template.source_reference = "Unrelated future workpaper"
    template.save()
    template.archive()
    regenerated = regenerate_occurrence(client_id, context["occurrence_id"], "Reversal")
    new_reversal = DraftEntry.get_by_id(regenerated["draft_id"], client_id)
    reversed_id = new_reversal.approve()
    posted_reversal = JournalEntry.get_by_id(reversed_id)
    assert "Revised accrual" in posted_reversal.source_reference
    assert "Unrelated" not in posted_reversal.source_reference
    assert posted_reversal.total_debits() == 250
    assert f"Scheduled reversal of JE #{posted_id}" in posted_reversal.source_reference
    assert DraftEntry.get_by_id(first.id, client_id).status == "rejected"
    assert DraftEntry.get_by_id(reversal.id, client_id).status == "rejected"
    with get_cursor() as cursor:
        originals = recurring_draft_context(cursor.connection, first.id, client_id)
    assert originals["template_name"] == "Original accrual"
    assert originals["reversal_rule"] == "None"
    logs = [log for log in AuditLog.get_all(client_id)
            if log.table_name == "recurring_occurrence_drafts"]
    assert len(logs) == 4
    assert all(log.new_values["snapshot"]["template_name"] != "Unrelated future name"
               for log in logs)


def test_future_period_adopts_updated_reference_and_reversal_choice(client_id, accounts):
    template, schedule, january = _generated(client_id, accounts, "NextDay")
    template.source_reference = "February workpaper"
    template.save()
    schedule.reversal_rule = "None"
    schedule.save()
    result = generate_occurrence(client_id, schedule.id, date(2026, 2, 1), date(2026, 2, 28))
    february = DraftEntry.get_by_id(result["draft_id"], client_id)
    february_entry = JournalEntry.get_by_id(february.approve())
    assert "February workpaper" in february_entry.source_reference
    assert [draft.id for draft in DraftEntry.get_pending(client_id)] == [january.id]
    january_entry = JournalEntry.get_by_id(january.approve())
    assert "Original workpaper" in january_entry.source_reference
    assert len(DraftEntry.get_pending(client_id)) == 1


@pytest.mark.parametrize("regenerate", [False, True])
def test_legacy_reversal_can_still_be_reviewed_without_using_live_template(
    client_id, accounts, regenerate,
):
    template, _, primary = _generated(client_id, accounts, "NextDay")
    posted_id = primary.approve()
    reversal = DraftEntry.get_pending(client_id)[0]
    with get_cursor(commit=True) as cur:
        cur.execute("""UPDATE recurring_occurrence_drafts SET snapshot_reversal_rule=NULL,
            snapshot_template_name=NULL, snapshot_source_reference=NULL""")
    template.name = "Future name"
    template.save()
    if regenerate:
        reversal.reject()
        with get_cursor() as cur:
            context = recurring_draft_context(cur.connection, reversal.id, client_id)
        result = regenerate_occurrence(client_id, context["occurrence_id"], "Reversal")
        reversal = DraftEntry.get_by_id(result["draft_id"], client_id)
    entry = JournalEntry.get_by_id(reversal.approve())
    assert f"Scheduled reversal of JE #{posted_id}" in entry.source_reference
    assert "Original accrual" in entry.source_reference
    assert "Future name" not in entry.source_reference


def test_legacy_primary_page_explains_recovery_and_disables_approval(client_id, accounts, monkeypatch):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    import utils.client_selector as selector
    from tests.conftest import page_path

    _, _, draft = _generated(client_id, accounts, "NextDay")
    with get_cursor(commit=True) as cur:
        cur.execute("""UPDATE recurring_occurrence_drafts SET snapshot_reversal_rule=NULL,
            snapshot_template_name=NULL, snapshot_source_reference=NULL""")
    monkeypatch.setattr(selector, "render_client_selector", lambda: client_id)
    monkeypatch.setattr(st, "page_link", lambda *a, **k: None)
    page = AppTest.from_file(page_path("pages/2_Journal_Entries.py"))
    page.session_state["journal_active_tab"] = "Drafts"
    page.run()
    assert not page.exception
    assert page.button(key=f"draft_approve_{draft.id}").disabled
    assert any("Reject it" in warning.value for warning in page.warning)
    assert not page.button(key=f"draft_reject_{draft.id}").disabled
