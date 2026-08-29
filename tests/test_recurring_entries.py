from datetime import date
from concurrent.futures import ThreadPoolExecutor

import pytest

from models.account import Account
from models.audit_log import AuditLog
from models.recurring_entry import (
    JournalEntryTemplate,
    RecurringSchedule,
    TemplateLine,
)
from models.draft_entry import DraftEntry
from models.fiscal_period import FiscalPeriod
from services.recurring_entries import (
    generate_selected,
    generate_occurrence,
    preview_due,
    rejected_recoveries,
    regenerate_occurrence,
    skip_occurrence,
    undo_skip,
)


def _template(client_id, accounts, name="Monthly amortization"):
    return JournalEntryTemplate(
        client_id=client_id,
        name=name,
        description="Record monthly amortization",
        entry_type="Adjusting",
        source_reference="Prepaid schedule",
        lines=[
            TemplateLine(accounts["expense"], debit_cents=12_345),
            TemplateLine(accounts["cash"], credit_cents=12_345),
        ],
    )


def test_template_round_trip_update_and_audit(client_id, accounts):
    template = _template(client_id, accounts)
    template.save()

    stored = JournalEntryTemplate.get_by_id(template.id, client_id)
    assert stored.name == "Monthly amortization"
    assert stored.entry_type == "Adjusting"
    assert [line.sort_order for line in stored.lines] == [0, 1]
    assert stored.lines[0].debit_cents == 12_345

    stored.description = "Updated description"
    stored.lines[0].debit_cents = 20_000
    stored.lines[1].credit_cents = 20_000
    stored.save()

    updated = JournalEntryTemplate.get_by_id(template.id, client_id)
    assert updated.description == "Updated description"
    assert updated.lines[0].debit_cents == 20_000
    logs = [
        log for log in AuditLog.get_all(client_id)
        if log.table_name == "journal_entry_templates" and log.record_id == template.id
    ]
    assert [log.action for log in logs] == ["UPDATE", "INSERT"]
    assert logs[0].old_values["lines"][0]["debit_cents"] == 12_345
    assert logs[0].new_values["lines"][0]["debit_cents"] == 20_000


def test_template_validation_and_client_isolation(client_id, accounts):
    with pytest.raises(ValueError, match="balance"):
        bad = _template(client_id, accounts)
        bad.lines[1].credit_cents = 1
        bad.save()

    with pytest.raises(ValueError, match="Regular or Adjusting"):
        bad = _template(client_id, accounts)
        bad.entry_type = "Closing"
        bad.save()

    other = JournalEntryTemplate(
        client_id=client_id + 999,
        name="Cross-client",
        description="No",
        lines=[
            TemplateLine(accounts["cash"], debit_cents=100),
            TemplateLine(accounts["revenue"], credit_cents=100),
        ],
    )
    with pytest.raises(ValueError, match="selected client"):
        other.save()


def test_active_template_names_are_case_insensitive_unique(client_id, accounts):
    _template(client_id, accounts, "Rent").save()
    with pytest.raises(ValueError, match="already exists"):
        _template(client_id, accounts, " rent ").save()


def test_schedule_round_trip_and_validation(client_id, accounts):
    template = _template(client_id, accounts)
    template.save()
    schedule = RecurringSchedule(
        template_id=template.id,
        frequency="Monthly",
        date_rule="DayOfMonth",
        day_of_month=31,
        starts_on=date(2026, 1, 1),
        ends_on=date(2026, 12, 31),
    )
    schedule.save()

    stored = RecurringSchedule.get_for_template(template.id, client_id)
    assert stored.day_of_month == 31
    assert stored.starts_on == date(2026, 1, 1)
    stored.set_active(False)
    assert RecurringSchedule.get_by_id(stored.id, client_id).is_active is False

    duplicate = RecurringSchedule(
        template_id=template.id, starts_on=date(2027, 1, 1)
    )
    with pytest.raises(ValueError, match="already has"):
        duplicate.save()

    with pytest.raises(ValueError, match="period-end"):
        RecurringSchedule(
            template_id=template.id,
            date_rule="PeriodStart",
            reversal_rule="NextDay",
            starts_on=date(2026, 1, 1),
        ).save()


def test_archiving_template_pauses_schedule(client_id, accounts):
    template = _template(client_id, accounts)
    template.save()
    schedule = RecurringSchedule(
        template_id=template.id, starts_on=date(2026, 1, 1)
    )
    schedule.save()

    template.archive()
    assert JournalEntryTemplate.get_by_id(template.id, client_id).archived_at
    assert RecurringSchedule.get_by_id(schedule.id, client_id).is_active is False
    assert JournalEntryTemplate.get_all(client_id) == []
    assert len(JournalEntryTemplate.get_all(client_id, include_archived=True)) == 1
    template.restore()
    assert JournalEntryTemplate.get_by_id(template.id, client_id).archived_at == ""
    # Restoring the template is not the same decision as resuming its schedule.
    assert RecurringSchedule.get_by_id(schedule.id, client_id).is_active is False


def test_template_reference_blocks_hard_delete(client_id, accounts):
    template = _template(client_id, accounts)
    template.save()
    blockers = Account.deletion_blockers(accounts["expense"])
    assert blockers["journal-entry templates"] == 1
    with pytest.raises(ValueError, match="journal-entry templates"):
        Account.delete(accounts["expense"], client_id=client_id)


def _periods(client_id, year=2026, fye=12):
    return FiscalPeriod.ensure_periods_exist(client_id, year, fye)


def test_preview_and_generation_are_period_idempotent(client_id, accounts):
    _periods(client_id)
    template = _template(client_id, accounts)
    template.save()
    RecurringSchedule(
        template_id=template.id,
        frequency="Monthly",
        date_rule="PeriodEnd",
        starts_on=date(2026, 1, 1),
    ).save()

    due = preview_due(client_id, through_date=date(2026, 2, 28))
    assert [(item.period_name, item.state) for item in due] == [
        ("FY 2026 - Jan", "Due"), ("FY 2026 - Feb", "Due")
    ]
    january = due[0]
    first = generate_occurrence(
        client_id, january.schedule_id, january.period_start, january.period_end
    )
    second = generate_occurrence(
        client_id, january.schedule_id, january.period_start, january.period_end
    )
    assert first["result"] == "generated"
    assert second == {
        "result": "already_generated",
        "occurrence_id": first["occurrence_id"],
        "draft_id": first["draft_id"],
    }
    draft = DraftEntry.get_by_id(first["draft_id"], client_id)
    assert draft.status == "pending"
    assert draft.entry_date == "2026-01-31"
    assert draft.proposed_by == "Recurring schedule: Monthly amortization"
    assert DraftEntry.pending_count(client_id) == 1
    audit_tables = [
        log.table_name for log in AuditLog.get_all(client_id)
        if log.table_name in {
            "recurring_occurrences", "recurring_occurrence_drafts",
        }
    ]
    assert audit_tables[:2] == [
        "recurring_occurrence_drafts", "recurring_occurrences"
    ]

    results = generate_selected(client_id, [
        (january.schedule_id, january.period_start, january.period_end),
        (due[1].schedule_id, due[1].period_start, due[1].period_end),
    ])
    assert results["requested_count"] == results["accounted_count"] == 2
    assert len(results["already_generated"]) == 1
    assert len(results["generated"]) == 1


def test_frequency_change_blocks_periods_overlapping_existing_occurrences(
    client_id, accounts
):
    _periods(client_id)
    template = _template(client_id, accounts)
    template.save()
    schedule = RecurringSchedule(
        template_id=template.id,
        frequency="Monthly",
        starts_on=date(2026, 1, 1),
    )
    schedule.save()

    monthly = preview_due(client_id, through_date=date(2026, 3, 31))
    for item in monthly:
        generated = generate_occurrence(
            client_id, schedule.id, item.period_start, item.period_end
        )
        DraftEntry.get_by_id(generated["draft_id"], client_id).approve()

    schedule.frequency = "Quarterly"
    schedule.save()
    quarterly = preview_due(client_id, through_date=date(2026, 3, 31))
    assert len(quarterly) == 1
    assert quarterly[0].period_name == "FY 2026 - Q1"
    assert quarterly[0].state == "Blocked"
    assert "overlaps" in quarterly[0].reason
    with pytest.raises(ValueError, match="overlaps"):
        generate_occurrence(
            client_id, schedule.id,
            quarterly[0].period_start, quarterly[0].period_end,
        )


def test_frequency_change_blocks_smaller_periods_inside_existing_occurrence(
    client_id, accounts
):
    _periods(client_id)
    template = _template(client_id, accounts)
    template.save()
    schedule = RecurringSchedule(
        template_id=template.id,
        frequency="Quarterly",
        starts_on=date(2026, 1, 1),
    )
    schedule.save()
    quarter = preview_due(client_id, through_date=date(2026, 3, 31))[0]
    generated = generate_occurrence(
        client_id, schedule.id, quarter.period_start, quarter.period_end
    )
    DraftEntry.get_by_id(generated["draft_id"], client_id).approve()

    schedule.frequency = "Monthly"
    schedule.save()
    monthly = preview_due(client_id, through_date=date(2026, 3, 31))
    assert [item.state for item in monthly] == ["Blocked", "Blocked", "Blocked"]
    assert all("FY 2026 - Q1" in item.reason for item in monthly)


def test_day_31_clamps_and_noncalendar_fiscal_year_works(client_id, accounts):
    from models.client import Client

    client = Client.get_by_id(client_id)
    client.fiscal_year_end_month = 6
    client.save(seed_accounts=False)
    _periods(client_id, year=2026, fye=6)
    template = _template(client_id, accounts)
    template.save()
    RecurringSchedule(
        template_id=template.id,
        frequency="Monthly",
        date_rule="DayOfMonth",
        day_of_month=31,
        starts_on=date(2025, 7, 1),
    ).save()
    items = preview_due(client_id, through_date=date(2026, 2, 28))
    february = next(item for item in items if item.period_name == "FY 2026 - Feb")
    assert february.entry_date == date(2026, 2, 28)


def test_quarterly_schedule_uses_noncalendar_fiscal_quarter(client_id, accounts):
    from models.client import Client

    client = Client.get_by_id(client_id)
    client.fiscal_year_end_month = 6
    client.save(seed_accounts=False)
    _periods(client_id, year=2026, fye=6)
    template = _template(client_id, accounts)
    template.save()
    RecurringSchedule(
        template_id=template.id, frequency="Quarterly",
        starts_on=date(2025, 7, 1),
    ).save()
    items = preview_due(client_id, through_date=date(2025, 9, 30))
    assert len(items) == 1
    assert items[0].period_name == "FY 2026 - Q1"
    assert items[0].entry_date == date(2025, 9, 30)


def test_annual_leap_year_historical_and_future_through_dates(client_id, accounts):
    _periods(client_id, year=2024)
    _periods(client_id, year=2027)
    _periods(client_id, year=2028)

    annual = _template(client_id, accounts, "Annual true-up")
    annual.save()
    RecurringSchedule(
        template_id=annual.id,
        frequency="Annually",
        starts_on=date(2024, 1, 1),
        ends_on=date(2024, 12, 31),
    ).save()
    historical = [
        item for item in preview_due(client_id, through_date=date(2024, 12, 31))
        if item.template_id == annual.id
    ]
    assert [(item.period_name, item.entry_date) for item in historical] == [
        ("FY 2024", date(2024, 12, 31))
    ]

    leap = _template(client_id, accounts, "Leap-year month end")
    leap.save()
    RecurringSchedule(
        template_id=leap.id,
        frequency="Monthly",
        starts_on=date(2028, 1, 1),
        ends_on=date(2028, 2, 29),
    ).save()
    leap_items = [
        item for item in preview_due(client_id, through_date=date(2028, 2, 29))
        if item.template_id == leap.id
    ]
    assert [item.entry_date for item in leap_items] == [
        date(2028, 1, 31), date(2028, 2, 29)
    ]

    catch_up = _template(client_id, accounts, "Future catch-up")
    catch_up.save()
    RecurringSchedule(
        template_id=catch_up.id,
        frequency="Monthly",
        starts_on=date(2027, 1, 1),
        ends_on=date(2027, 3, 31),
    ).save()
    future_items = [
        item for item in preview_due(client_id, through_date=date(2027, 3, 31))
        if item.template_id == catch_up.id
    ]
    assert [item.period_name for item in future_items] == [
        "FY 2027 - Jan", "FY 2027 - Feb", "FY 2027 - Mar"
    ]


def test_missing_calendar_and_closed_year_are_blocked(client_id, accounts):
    template = _template(client_id, accounts)
    template.save()
    RecurringSchedule(
        template_id=template.id, starts_on=date(2026, 1, 1)
    ).save()
    missing = preview_due(client_id, through_date=date(2026, 1, 31))
    assert missing[0].state == "Blocked"
    assert "fiscal calendar" in missing[0].reason

    periods = _periods(client_id)
    year = next(period for period in periods if period.period_type == "Year")
    FiscalPeriod.set_closed(
        year.id, True, client_id=client_id,
        confirmation={"explicit_confirmation": True},
    )
    closed = preview_due(client_id, through_date=date(2026, 1, 31))
    assert closed[0].state == "Blocked"
    assert "is closed" in closed[0].reason


def test_skip_undo_and_regenerate_preserve_draft_history(client_id, accounts):
    _periods(client_id)
    template = _template(client_id, accounts)
    template.save()
    schedule = RecurringSchedule(
        template_id=template.id, starts_on=date(2026, 1, 1)
    )
    schedule.save()
    january = preview_due(client_id, through_date=date(2026, 1, 31))[0]

    skipped = skip_occurrence(
        client_id, schedule.id, january.period_start, january.period_end,
        "No activity this month",
    )
    assert preview_due(client_id, through_date=date(2026, 1, 31))[0].state == "Skipped"
    undo_skip(client_id, skipped["occurrence_id"])
    assert preview_due(client_id, through_date=date(2026, 1, 31))[0].state == "Due"

    generated = generate_occurrence(
        client_id, schedule.id, january.period_start, january.period_end
    )
    old = DraftEntry.get_by_id(generated["draft_id"], client_id)
    old.reject()
    replacement = regenerate_occurrence(client_id, generated["occurrence_id"])
    assert replacement["generation_number"] == 2
    assert DraftEntry.get_by_id(old.id, client_id).status == "rejected"
    assert DraftEntry.get_by_id(replacement["draft_id"], client_id).status == "pending"


def test_inactive_template_account_blocks_generation(client_id, accounts):
    _periods(client_id)
    template = _template(client_id, accounts)
    template.save()
    RecurringSchedule(
        template_id=template.id, starts_on=date(2026, 1, 1)
    ).save()
    expense = Account.get_by_id(accounts["expense"], client_id)
    expense.deactivate()
    item = preview_due(client_id, through_date=date(2026, 1, 31))[0]
    assert item.state == "Blocked"
    assert "inactive" in item.reason


def test_generated_draft_is_snapshot_and_future_generation_uses_template_edit(
    client_id, accounts
):
    _periods(client_id)
    template = _template(client_id, accounts)
    template.save()
    schedule = RecurringSchedule(
        template_id=template.id, starts_on=date(2026, 1, 1)
    )
    schedule.save()
    due = preview_due(client_id, through_date=date(2026, 2, 28))
    january = generate_occurrence(
        client_id, schedule.id, due[0].period_start, due[0].period_end
    )
    original = DraftEntry.get_by_id(january["draft_id"], client_id)

    template.lines[0].debit_cents = 50_000
    template.lines[1].credit_cents = 50_000
    template.save()
    still_original = DraftEntry.get_by_id(january["draft_id"], client_id)
    assert still_original.lines[0].debit_cents == original.lines[0].debit_cents == 12_345

    february = generate_occurrence(
        client_id, schedule.id, due[1].period_start, due[1].period_end
    )
    future = DraftEntry.get_by_id(february["draft_id"], client_id)
    assert future.lines[0].debit_cents == 50_000


def test_generation_audit_failure_rolls_back_occurrence_and_draft(
    client_id, accounts, monkeypatch
):
    _periods(client_id)
    template = _template(client_id, accounts)
    template.save()
    schedule = RecurringSchedule(
        template_id=template.id, starts_on=date(2026, 1, 1)
    )
    schedule.save()
    january = preview_due(client_id, through_date=date(2026, 1, 31))[0]
    original_write = AuditLog.write

    def fail_link(cursor, client, table, record, action, **kwargs):
        if table == "recurring_occurrence_drafts":
            raise RuntimeError("link audit failed")
        return original_write(cursor, client, table, record, action, **kwargs)

    monkeypatch.setattr(AuditLog, "write", fail_link)
    with pytest.raises(RuntimeError, match="link audit failed"):
        generate_occurrence(
            client_id, schedule.id, january.period_start, january.period_end
        )

    assert DraftEntry.pending_count(client_id) == 0
    item = preview_due(client_id, through_date=date(2026, 1, 31))[0]
    assert item.state == "Due"


def test_adjusting_approval_posts_with_aje_and_creates_one_reversal(
    client_id, accounts
):
    _periods(client_id)
    template = _template(client_id, accounts)
    template.save()
    schedule = RecurringSchedule(
        template_id=template.id,
        starts_on=date(2026, 1, 1),
        reversal_rule="NextDay",
    )
    schedule.save()
    january = preview_due(client_id, through_date=date(2026, 1, 31))[0]
    generated = generate_occurrence(
        client_id, schedule.id, january.period_start, january.period_end
    )
    primary = DraftEntry.get_by_id(generated["draft_id"], client_id)
    entry_id = primary.approve()

    from models.journal_entry import JournalEntry

    entry = JournalEntry.get_by_id(entry_id, client_id)
    assert entry.entry_type == "Adjusting"
    assert entry.aje_reference == "AJE-001"
    assert entry.source_reference.startswith(
        "Recurring · Monthly amortization · FY 2026 - Jan"
    )

    pending = DraftEntry.get_pending(client_id)
    assert len(pending) == 1
    reversal = pending[0]
    assert reversal.id != primary.id
    assert reversal.entry_date == "2026-02-01"
    assert reversal.entry_type == "Adjusting"
    assert reversal.lines[0].credit_cents == primary.lines[0].debit_cents
    assert reversal.lines[1].debit_cents == primary.lines[1].credit_cents

    with pytest.raises(ValueError, match="pending"):
        primary.approve()
    assert DraftEntry.pending_count(client_id) == 1

    reversal_entry_id = reversal.approve()
    reversal_entry = JournalEntry.get_by_id(reversal_entry_id, client_id)
    assert reversal_entry.aje_reference == "AJE-002"
    assert reversal_entry.source_reference.startswith(
        f"Scheduled reversal of JE #{entry_id}"
    )


def test_rejected_reversal_can_be_regenerated_without_reversing_new_template_values(
    client_id, accounts
):
    _periods(client_id)
    template = _template(client_id, accounts)
    template.save()
    schedule = RecurringSchedule(
        template_id=template.id,
        starts_on=date(2026, 1, 1),
        reversal_rule="NextDay",
    )
    schedule.save()
    january = preview_due(client_id, through_date=date(2026, 1, 31))[0]
    generated = generate_occurrence(
        client_id, schedule.id, january.period_start, january.period_end
    )
    DraftEntry.get_by_id(generated["draft_id"], client_id).approve()
    rejected = DraftEntry.get_pending(client_id)[0]
    rejected.reject()

    template.lines[0].debit_cents = 99_999
    template.lines[1].credit_cents = 99_999
    template.save()
    handled = preview_due(client_id, through_date=date(2026, 1, 31))[0]
    assert handled.state == "Handled"
    assert handled.draft_status == "approved"
    assert handled.reversal_draft_id == rejected.id
    assert handled.reversal_draft_status == "rejected"

    template.archive()
    recoveries = rejected_recoveries(client_id)
    assert [(item["role"], item["draft_id"]) for item in recoveries] == [
        ("Reversal", rejected.id)
    ]

    replacement = regenerate_occurrence(
        client_id, generated["occurrence_id"], role="Reversal"
    )
    assert replacement["role"] == "Reversal"
    assert replacement["generation_number"] == 2
    restored = DraftEntry.get_by_id(replacement["draft_id"], client_id)
    assert restored.status == "pending"
    assert restored.entry_date == rejected.entry_date == "2026-02-01"
    assert [line.__dict__ for line in restored.lines] == [
        line.__dict__ for line in rejected.lines
    ]
    assert restored.lines[0].credit_cents == 12_345
    with pytest.raises(ValueError, match="rejected reversal"):
        regenerate_occurrence(
            client_id, generated["occurrence_id"], role="Reversal"
        )


def test_reversal_creation_failure_rolls_back_primary_approval(
    client_id, accounts, monkeypatch
):
    _periods(client_id)
    template = _template(client_id, accounts)
    template.save()
    schedule = RecurringSchedule(
        template_id=template.id,
        starts_on=date(2026, 1, 1),
        reversal_rule="NextDay",
    )
    schedule.save()
    january = preview_due(client_id, through_date=date(2026, 1, 31))[0]
    generated = generate_occurrence(
        client_id, schedule.id, january.period_start, january.period_end
    )
    primary = DraftEntry.get_by_id(generated["draft_id"], client_id)

    import services.recurring_entries as recurring_service

    monkeypatch.setattr(
        recurring_service,
        "create_reversal_after_primary_approval",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("reversal failed")),
    )
    with pytest.raises(RuntimeError, match="reversal failed"):
        primary.approve()

    assert DraftEntry.get_by_id(primary.id, client_id).status == "pending"
    from models.journal_entry import JournalEntry
    assert JournalEntry.count(client_id) == 0


def test_duplicate_calendar_rows_do_not_duplicate_due_occurrences(
    client_id, accounts
):
    _periods(client_id)
    FiscalPeriod(
        client_id=client_id,
        period_name="Duplicate January",
        period_type="Month",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
    ).save()
    template = _template(client_id, accounts)
    template.save()
    RecurringSchedule(
        template_id=template.id, starts_on=date(2026, 1, 1)
    ).save()
    january = preview_due(client_id, through_date=date(2026, 1, 31))
    assert len(january) == 1


def test_concurrent_generation_produces_one_occurrence_and_one_draft(
    client_id, accounts
):
    _periods(client_id)
    template = _template(client_id, accounts)
    template.save()
    schedule = RecurringSchedule(
        template_id=template.id, starts_on=date(2026, 1, 1)
    )
    schedule.save()
    january = preview_due(client_id, through_date=date(2026, 1, 31))[0]

    def generate():
        return generate_occurrence(
            client_id, schedule.id, january.period_start, january.period_end
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: generate(), range(2)))
    assert {result["result"] for result in results} == {
        "generated", "already_generated"
    }
    assert len({result["occurrence_id"] for result in results}) == 1
    assert len({result["draft_id"] for result in results}) == 1
    assert DraftEntry.pending_count(client_id) == 1
