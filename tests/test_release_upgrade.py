"""Upgrade populated historical schemas, including unverifiable old drafts."""

from datetime import date
import shutil

import pytest

from database import connection as dbc, schema
from models.audit_log import AuditLog
from models.client import Client
from models.account import Account
from models.draft_entry import DraftEntry, DraftLine
from models.recurring_entry import JournalEntryTemplate, RecurringSchedule, TemplateLine
from models.journal_entry import JournalEntry
from services.backups import create_backup, restore_backup
from services.recurring_entries import recurring_draft_context, regenerate_occurrence
from tests.conftest import post_entry


def _restore_audit(conn):
    AuditLog.write(conn.cursor(), None, "database_restore", 0, "RESTORE",
                   new_values={"restored_from": "synthetic prior-release book"})


@pytest.mark.parametrize("last_migration", [23, 24])
def test_populated_prior_schema_upgrades_and_restores_without_rewriting_history(
    db, tmp_path, monkeypatch, last_migration,
):
    historical = tmp_path / "historical-migrations"
    historical.mkdir()
    for path in schema.MIGRATIONS_DIR.glob("*.sql"):
        if int(path.name[:3]) <= last_migration:
            shutil.copy2(path, historical / path.name)
    monkeypatch.setattr(dbc, "DATABASE_PATH", tmp_path / "prior-release.db")
    with monkeypatch.context() as old:
        old.setattr(schema, "MIGRATIONS_DIR", historical)
        dbc.init_database()

    client_id = Client(name="Cedar Upgrade Demo").save(seed_accounts=False)
    cash = Account(client_id=client_id, account_number="1000", name="Cash", type="Asset")
    equity = Account(client_id=client_id, account_number="3000", name="Capital", type="Equity")
    cash.save()
    equity.save()
    post_entry(client_id, date(2026, 1, 1), [(cash.id, 100, 0), (equity.id, 0, 100)])
    legacy_draft = None
    if last_migration == 24:
        # These tables/rows are the actual v1.7.0 schema: no snapshot columns.
        template = JournalEntryTemplate(
            client_id=client_id, name="Legacy template", description="Legacy draft",
            lines=[TemplateLine(cash.id, debit_cents=100),
                   TemplateLine(equity.id, credit_cents=100)],
        )
        template.save()
        schedule = RecurringSchedule(template_id=template.id, starts_on=date(2026, 1, 1))
        schedule.save()
        legacy_draft = DraftEntry(
            client_id=client_id, entry_date="2026-01-31", description="Legacy draft",
            proposed_by="Recurring schedule: Legacy template",
            lines=[DraftLine("1000", debit_cents=100), DraftLine("3000", credit_cents=100)],
        )
        legacy_draft.save()
        with dbc.get_cursor(commit=True) as cur:
            cur.execute("""INSERT INTO recurring_occurrences
                (schedule_id, period_name, period_type, period_start, period_end,
                 scheduled_entry_date, disposition, generated_at, generated_by)
                VALUES (?, 'January', 'Month', '2026-01-01', '2026-01-31',
                        '2026-01-31', 'Generated', '2026-01-01', 'Demo user')""", (schedule.id,))
            occurrence_id = cur.lastrowid
            cur.execute("""INSERT INTO recurring_occurrence_drafts
                (occurrence_id, draft_entry_id, role, generation_number)
                VALUES (?, ?, 'Primary', 1)""", (occurrence_id, legacy_draft.id))

    with dbc.get_cursor() as cur:
        tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                  if r[0] not in ("schema_migrations", "sqlite_sequence")]
        before = {}
        for table in tables:
            cols = [r[1] for r in cur.execute(f'PRAGMA table_info("{table}")')]
            rows = [tuple(r) for r in cur.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
            before[table] = (cols, rows)
    backup = create_backup(tmp_path / "backups")
    dbc.init_database()
    dbc.init_database()

    def assert_history():
        with dbc.get_cursor() as cur:
            for table, (cols, expected) in before.items():
                projection = ", ".join(f'"{col}"' for col in cols)
                actual = [tuple(r) for r in cur.execute(
                    f'SELECT {projection} FROM "{table}" ORDER BY rowid')]
                if table == "audit_log":
                    assert actual[:len(expected)] == expected
                else:
                    assert actual == expected, table
            assert cur.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 25

    assert_history()
    restore_backup(backup.database_path, tmp_path / "backups", audit=_restore_audit)
    assert_history()
    with dbc.get_cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM audit_log WHERE action='RESTORE'").fetchone()[0] == 1

    if legacy_draft:
        with pytest.raises(ValueError, match="Reject it.*regenerate"):
            legacy_draft.approve()
        assert JournalEntry.count(client_id) == 1
        assert DraftEntry.get_by_id(legacy_draft.id, client_id).status == "pending"
        with dbc.get_cursor() as cur:
            context = recurring_draft_context(cur.connection, legacy_draft.id, client_id)
        assert not context["snapshot_available"]
        legacy_draft.reject()
        replacement = regenerate_occurrence(client_id, occurrence_id)
        assert replacement["generation_number"] == 2
        DraftEntry.get_by_id(replacement["draft_id"], client_id).approve()
        assert JournalEntry.count(client_id) == 2
