"""The dashboard activity feed: what was done, not what is in the books."""
from datetime import date, datetime, timedelta

from models.audit_log import AuditLog
from models.journal_entry import JournalEntry, JournalEntryLine
from services.activity_feed import describe_when, get_recent_activity
from services.posting import post_transaction


def _import(client_id, accounts, batch, rows, filename="statement.csv", bank="cash"):
    for row_number, (description, amount) in enumerate(rows, start=2):
        post_transaction(
            client_id,
            {"date": date(2026, 1, 5), "description": description, "amount": amount,
             "batch_id": batch, "source_filename": filename,
             "source_row_number": row_number},
            target_account_id=accounts["expense"],
            bank_account_id=accounts[bank],
            batch_id=batch,
            learn=False,
        )


def _manual_entry(client_id, accounts, description, entry_type="Regular",
                  aje_reference=None, amount=100.00):
    entry = JournalEntry(
        client_id=client_id,
        entry_date=date(2026, 1, 31),
        description=description,
        entry_type=entry_type,
        aje_reference=aje_reference,
        lines=[
            JournalEntryLine(account_id=accounts["expense"], debit=amount, credit=0),
            JournalEntryLine(account_id=accounts["cash"], debit=0, credit=amount),
        ],
    )
    entry.save()
    return entry


def test_no_activity_for_untouched_client(client_id, accounts):
    assert get_recent_activity(client_id) == []


def test_an_import_is_one_event_not_one_per_row(client_id, accounts):
    """The whole point: 3 imported rows must read as a single action."""
    _import(client_id, accounts, "JAN", [
        ("CANVA", -15.00), ("OBSIDIAN", -5.00), ("ANTHROPIC", -11.04),
    ])

    events = get_recent_activity(client_id)

    assert len(events) == 1
    assert events[0].kind == "import"
    assert "3 transactions" in events[0].summary
    assert "1000 - Cash" in events[0].summary
    assert "statement.csv" in events[0].detail


def test_import_summary_is_singular_for_one_row(client_id, accounts):
    _import(client_id, accounts, "ONE", [("CANVA", -15.00)])
    assert "1 transaction " in get_recent_activity(client_id)[0].summary


def test_import_reports_rows_still_awaiting_review(client_id, accounts):
    from models.transaction import ImportedTransaction

    _import(client_id, accounts, "JAN", [("CANVA", -15.00)])
    ImportedTransaction(
        client_id=client_id, import_batch="JAN",
        transaction_date=date(2026, 1, 9), description="NOT YET",
        amount=-2.00, bank_account_id=accounts["cash"], status="Pending",
        source_filename="statement.csv", source_row_number=3,
    ).save()

    assert "1 still to review" in get_recent_activity(client_id)[0].detail


def test_separate_imports_appear_as_separate_events(client_id, accounts):
    _import(client_id, accounts, "RELAY", [("GO DADDY", -26.18)],
            filename="relay.csv", bank="cash")
    _import(client_id, accounts, "AMEX", [("CANVA", -15.00)],
            filename="amex.csv", bank="credit_card")

    events = get_recent_activity(client_id)
    imports = [e for e in events if e.kind == "import"]
    assert len(imports) == 2
    assert {"relay.csv", "amex.csv"} == {
        e.detail.replace("from ", "") for e in imports
    }


def test_import_generated_entries_are_not_listed_individually(client_id, accounts):
    """An import posts journal entries; they belong to the import event."""
    _import(client_id, accounts, "JAN", [("CANVA", -15.00), ("OBSIDIAN", -5.00)])

    events = get_recent_activity(client_id)
    assert [e.kind for e in events] == ["import"]


def test_hand_keyed_entry_is_its_own_event(client_id, accounts):
    _manual_entry(client_id, accounts, "Opening balances")

    events = get_recent_activity(client_id)
    assert len(events) == 1
    assert events[0].kind == "journal"
    assert events[0].summary.startswith("Entered journal entry #")
    assert "Opening balances" in events[0].detail


def test_adjusting_entry_is_named_by_its_aje_reference(client_id, accounts):
    _manual_entry(client_id, accounts, "Depreciation",
                  entry_type="Adjusting", aje_reference="AJE-001")

    summary = get_recent_activity(client_id)[0].summary
    assert summary == "Posted adjusting entry AJE-001"


def test_adjusting_entry_without_a_reference_falls_back_to_its_id(client_id, accounts):
    entry = _manual_entry(client_id, accounts, "Accrual", entry_type="Adjusting")

    assert get_recent_activity(client_id)[0].summary == (
        f"Posted adjusting entry #{entry.id}"
    )


def test_beginning_balance_entry_is_described_as_such(client_id, accounts):
    _manual_entry(client_id, accounts, "Opening", entry_type="Beginning Balance")

    assert get_recent_activity(client_id)[0].summary == "Entered beginning balances"


def test_notable_audit_events_appear(client_id, accounts):
    AuditLog.log_event(client_id, "EXPORT", "trial_balance_worksheet_export",
                       {"format": "xlsx"})

    events = get_recent_activity(client_id)
    assert len(events) == 1
    assert events[0].kind == "audit"
    assert events[0].summary == "Exported trial balance worksheet"


def test_row_level_audit_noise_is_excluded(client_id, accounts):
    """Saving an entry writes an INSERT audit row; it must not double-report."""
    _manual_entry(client_id, accounts, "Something")

    events = get_recent_activity(client_id)
    assert [e.kind for e in events] == ["journal"]


def test_a_period_close_is_reported(client_id, accounts):
    AuditLog.log_event(client_id, "CLOSE", "fiscal_period", {"period": "FY2026"})
    assert get_recent_activity(client_id)[0].summary == "Closed a period"


def test_notable_events_survive_a_large_import(client_id, accounts):
    """An export must not be crowded out by per-row INSERT audit rows."""
    AuditLog.log_event(client_id, "EXPORT", "general_ledger_export", {"format": "csv"})
    _import(client_id, accounts, "BIG",
            [(f"CHARGE {n}", -float(n)) for n in range(1, 31)])

    kinds = {e.kind for e in get_recent_activity(client_id, limit=10)}
    assert "audit" in kinds
    assert "import" in kinds


def test_limit_is_respected(client_id, accounts):
    for n in range(5):
        _manual_entry(client_id, accounts, f"Entry {n}")

    assert len(get_recent_activity(client_id, limit=3)) == 3


def test_activity_is_isolated_per_client(client_id, accounts):
    from models.client import Client

    _manual_entry(client_id, accounts, "Mine")
    other = Client(name="Other Co", entity_type="S-Corp",
                   fiscal_year_end_month=12).save(seed_accounts=False)

    assert get_recent_activity(other) == []
    assert len(get_recent_activity(client_id)) == 1


def test_mixed_activity_is_ordered_newest_first(client_id, accounts):
    """Timestamps come from three tables; they must interleave correctly."""
    _import(client_id, accounts, "JAN", [("CANVA", -15.00)])
    _manual_entry(client_id, accounts, "Later entry")

    events = get_recent_activity(client_id)
    stamps = [e.when for e in events if e.when]
    assert stamps == sorted(stamps, reverse=True)
    assert {e.kind for e in events} == {"import", "journal"}


def test_when_phrasing_reads_naturally():
    now = datetime(2026, 7, 26, 12, 0, 0)

    def said(**delta):
        return describe_when(now - timedelta(**delta), now=now)

    assert said(seconds=10) == "just now"
    assert said(minutes=20) == "20 min ago"
    assert said(hours=1) == "1 hour ago"
    assert said(hours=5) == "5 hours ago"
    assert said(days=1, hours=2) == "yesterday"
    assert said(days=3) == "3 days ago"
    assert "2026" in said(days=30)


def test_when_phrasing_handles_missing_and_skewed_timestamps():
    now = datetime(2026, 7, 26, 12, 0, 0)

    assert describe_when(None) == ""
    # A timestamp slightly in the future (clock skew) must not read "in -1 hours".
    assert "2026" in describe_when(now + timedelta(hours=1), now=now)


def test_every_event_offers_somewhere_to_go(client_id, accounts):
    _import(client_id, accounts, "JAN", [("CANVA", -15.00)])
    _manual_entry(client_id, accounts, "An entry")
    AuditLog.log_event(client_id, "BACKUP", "database_backup")

    for event in get_recent_activity(client_id, limit=10):
        assert event.page and event.page.startswith("pages/")
        assert event.summary


def test_every_event_carries_who_did_it(client_id, accounts):
    """New activity is attributed to the person at the keyboard."""
    from utils.actor import current_actor

    _import(client_id, accounts, "JAN", [("CANVA", -15.00)])
    _manual_entry(client_id, accounts, "An entry")
    AuditLog.log_event(client_id, "BACKUP", "database_backup")

    events = get_recent_activity(client_id, limit=10)
    assert events
    for event in events:
        assert event.actor == current_actor(), event.summary
