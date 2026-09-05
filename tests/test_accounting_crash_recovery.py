"""Actual process death must leave accounting commits complete and retryable."""

from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from database import connection as dbc
from models.draft_entry import DraftEntry
from models.journal_entry import JournalEntry
from models.transaction import ImportedTransaction
from services.posting import post_transaction
from services.recurring_entries import generate_occurrence
from tests.helpers.cedar import JANUARY, create_cedar


@pytest.mark.parametrize("operation", ["generate", "approve", "post"])
@pytest.mark.parametrize("boundary", ["before_commit", "after_commit"])
def test_process_death_preserves_atomicity_and_retry(db, tmp_path, operation, boundary):
    client_id, _, accounts, schedule = create_cedar()
    draft_id = None
    if operation == "approve":
        draft_id = generate_occurrence(client_id, schedule.id, *JANUARY)["draft_id"]
    config = dict(key=dbc.get_active_key(), client_id=client_id, schedule_id=schedule.id,
                  draft_id=draft_id, operation=operation, boundary=boundary,
                  cash=accounts["cash"], revenue=accounts["revenue"])
    fixture = tmp_path / "crash-fixture.json"
    fixture.write_text(json.dumps(config))
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "helpers/accounting_crash_worker.py"), str(fixture)],
        env=dict(os.environ, LEDGERTB_DB_PATH=str(dbc.DATABASE_PATH), ANTHROPIC_API_KEY="test-key-never-used"),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 73, result.stderr
    committed = boundary == "after_commit"
    with dbc.get_cursor() as cur:
        assert cur.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert cur.execute("PRAGMA foreign_key_check").fetchall() == []
        assert cur.execute("SELECT COUNT(*) FROM recurring_occurrence_drafts").fetchone()[0] == (
            (1 + int(committed)) if operation == "approve" else int(committed and operation == "generate")
        )
    assert JournalEntry.count(client_id) == int(committed and operation != "generate")
    assert len(ImportedTransaction.get_by_status(client_id, "Posted")) == int(committed and operation == "post")

    if operation == "generate":
        retry = generate_occurrence(client_id, schedule.id, *JANUARY)
        assert retry["result"] == ("already_generated" if committed else "generated")
        assert DraftEntry.pending_count(client_id) == 1
        assert JournalEntry.count(client_id) == 0
    elif operation == "approve":
        draft = DraftEntry.get_by_id(draft_id, client_id)
        if committed:
            with pytest.raises(ValueError, match="pending"):
                draft.approve()
        else:
            assert draft.status == "pending"
            draft.approve()
        assert JournalEntry.count(client_id) == 1
        assert DraftEntry.pending_count(client_id) == 1  # Exactly one linked reversal.
    else:
        for _ in range(2):
            post_transaction(client_id,
                             {"date": date(2026, 1, 5), "amount": 2500, "description": "Crash demo receipt",
                              "source_id": "cedar-crash-demo", "source_row_number": 2},
                             accounts["revenue"], accounts["cash"], batch_id="retry")
        assert JournalEntry.count(client_id) == 1
        assert len(ImportedTransaction.get_by_status(client_id, "Posted")) == 1
    with dbc.get_cursor() as cur:
        assert cur.execute("SELECT COALESCE(SUM(debit-credit), 0) FROM journal_entry_lines").fetchone()[0] == 0
        for table in ("journal_entries", "draft_entries", "recurring_occurrence_drafts", "imported_transactions"):
            assert cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == cur.execute(
                "SELECT COUNT(*) FROM audit_log WHERE table_name=? AND action='INSERT'", (table,)
            ).fetchone()[0]
