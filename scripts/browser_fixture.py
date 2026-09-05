"""Serve a disposable Cedar book through the real app for browser acceptance.

Never use this launcher for real books. It replaces credential storage in this
process only and refuses a nonempty data directory. No production test bypass
is added to the application.
"""

import argparse
from datetime import date
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8617)
    args = parser.parse_args()
    root = args.data_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        parser.error("The browser fixture requires an empty disposable directory.")
    os.environ["ANTHROPIC_API_KEY"] = "test-key-never-used"
    os.environ["LEDGERTB_DB_PATH"] = str(root / "cedar.ledgertb")
    os.environ["LEDGERTB_BACKUP_DIR"] = str(root / "backups")
    os.environ["LEDGERTB_UI_TOKEN"] = "cedar-browser-test"

    from utils import secure_store
    vault = {}
    secure_store.get_secret = lambda name: vault.get(name)
    secure_store.set_secret = lambda name, value: vault.__setitem__(name, value)
    secure_store.delete_secret = lambda name: vault.pop(name, None)
    import config
    config.USER_DATA_DIR = root
    config.API_KEY_FILE = root / "unused-api-key"

    from database import connection as dbc
    from database.crypto import derive_key
    from models.journal_entry import JournalEntry, JournalEntryLine
    from services.csv_import import CSVImporter
    from services.import_identity import hash_source
    from services.posting import post_transaction
    from services.recurring_entries import generate_occurrence
    from tests.helpers.cedar import BANK_CSV, JANUARY, create_cedar

    dbc.set_active_key(derive_key("cedar-browser-passphrase"))
    dbc.init_database()
    client_id, other_id, accounts, schedule = create_cedar()
    schedule.ends_on = JANUARY[1]
    schedule.save()
    JournalEntry(client_id=client_id, entry_date=date(2026, 1, 1),
                 description="Cedar opening capital", entry_type="Beginning Balance",
                 lines=[JournalEntryLine(account_id=accounts["cash"], debit=10000),
                        JournalEntryLine(account_id=accounts["capital"], credit=10000)]).save()
    rows = CSVImporter.parse_csv(BANK_CSV, date_column="Date", description_column="Description",
                                 amount_column="Amount", source_id=hash_source(BANK_CSV.encode()),
                                 source_filename="cedar-january.csv")
    for row, target in zip(rows, [accounts["revenue"], accounts["office"]]):
        post_transaction(client_id, row, target, accounts["cash"], batch_id="cedar-january")
    generated = generate_occurrence(client_id, schedule.id, *JANUARY)
    (root / "fixture.json").write_text(json.dumps({
        "client_id": client_id, "other_client_id": other_id, "schedule_id": schedule.id,
        "draft_id": generated["draft_id"], "book": str(dbc.DATABASE_PATH),
    }))
    (root / "cedar-january.csv").write_text(BANK_CSV)
    dbc.clear_active_key()
    from streamlit.web import bootstrap
    options = {
        "server.address": "127.0.0.1", "server.port": args.port,
        "server.headless": True, "browser.gatherUsageStats": False,
    }
    bootstrap.load_config_options(options)
    bootstrap.run(str(Path(__file__).resolve().parents[1] / "app.py"), False, [], options)


if __name__ == "__main__":
    main()
