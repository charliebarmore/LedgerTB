"""Abruptly exit during an accounting transaction or after its commit."""

from datetime import date
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from database import connection as dbc
from models.audit_log import AuditLog
from models.draft_entry import DraftEntry
from services.posting import post_transaction
from services.recurring_entries import generate_occurrence
from utils import secure_store


if __name__ == "__main__":
    config = json.loads(Path(sys.argv[1]).read_text())
    dbc.set_active_key(config["key"])
    secure_store.get_secret = lambda *a: None
    original_write = AuditLog.write

    def crash_at_audit(cursor, client_id, table, record, action, **kwargs):
        original_write(cursor, client_id, table, record, action, **kwargs)
        boundary = "imported_transactions" if config["operation"] == "post" else "recurring_occurrence_drafts"
        if table == boundary:
            os._exit(73)  # No Python finally/rollback/connection cleanup.

    if config["boundary"] == "before_commit":
        AuditLog.write = crash_at_audit
    if config["operation"] == "generate":
        generate_occurrence(config["client_id"], config["schedule_id"], date(2026, 1, 1), date(2026, 1, 31))
    elif config["operation"] == "approve":
        DraftEntry.get_by_id(config["draft_id"], config["client_id"]).approve()
    else:
        post_transaction(config["client_id"],
                         {"date": date(2026, 1, 5), "amount": 2500, "description": "Crash demo receipt",
                          "source_id": "cedar-crash-demo", "source_row_number": 2},
                         config["revenue"], config["cash"], batch_id="crash-demo")
    os._exit(73)  # Commit succeeded but no response reaches the caller.
