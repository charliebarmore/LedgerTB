"""Test-only abrupt exit around an encrypted saved-review transaction."""

import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from database import connection as dbc
from models.audit_log import AuditLog
from services import import_review_drafts as drafts
from utils import secure_store

if __name__ == "__main__":
    fixture = json.loads(Path(sys.argv[1]).read_text())
    dbc.set_active_key(fixture["key"])
    secure_store.get_secret = lambda *a: None
    original = AuditLog.write
    if fixture["boundary"] == "before_commit":

        def die(*a, **k):
            original(*a, **k)
            os._exit(73)

        AuditLog.write = die
    drafts.save(
        fixture["client_id"], [fixture["row"]], expected_revision=fixture["revision"]
    )
    os._exit(73)
