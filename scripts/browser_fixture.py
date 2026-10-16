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
import signal
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8617)
    parser.add_argument("--review-regressions", action="store_true")
    parser.add_argument("--app", type=Path, help="Disposable frozen app for review checks")
    args = parser.parse_args()
    root = args.data_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        parser.error("The browser fixture requires an empty disposable directory.")
    os.environ["ANTHROPIC_API_KEY"] = "test-key-never-used"  # pragma: allowlist secret -- synthetic fixture, never sent
    os.environ["LEDGERTB_DB_PATH"] = str(root / "cedar.ledgertb")
    os.environ["LEDGERTB_BACKUP_DIR"] = str(root / "backups")
    os.environ["LEDGERTB_UI_TOKEN"] = "cedar-browser-test"
    os.environ["LEDGERTB_DATA_DIR"] = str(root)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"

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
    review_fixture = {}
    if args.review_regressions:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        from models import close_map
        from models.draft_entry import DraftEntry, DraftLine
        from models.fiscal_period import FiscalPeriod

        year = FiscalPeriod.get_all(client_id, period_type="Year")[0]
        for name in ("capital", "revenue"):
            close_map.save_explanation(client_id, year.id, accounts[name],
                                       "Current-year amount agrees to synthetic support.")
            close_map.add_evidence(client_id, year.id, accounts[name],
                                   "workpaper", "DEMO-1", "Synthetic supporting schedule")
        close_map.propose_explanation(
            client_id, year.id, accounts["capital"],
            "Capital moved from $0.00 to $10,000.00.",
            "The $10,000.00 deposit agrees to $10,000.00 of opening capital.",
        )
        DraftEntry(client_id=client_id, entry_date="2026-01-31",
                   description="Synthetic currency explanation",
                   rationale="Accrue $1,200.00 of rent against $1,200.00 payable.\nReceipt total: $1,200.00.",
                   lines=[DraftLine("6100", debit_cents=120000),
                          DraftLine("2100", credit_cents=120000)]).save()

        requests = []

        class LocalModel(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(payload)
                (root / "local-requests.json").write_text(json.dumps(requests))
                if payload["model"] == "missing-demo-model":
                    self.send_response(404)
                    self.end_headers()
                    return
                inputs = json.loads(payload["messages"][-1]["content"])
                answers = [{"request_id": key, "choice": f"account_{accounts['office']}",
                            "reason": "Synthetic receipt identifies office supplies."}
                           for key in inputs["transactions"]]
                body = json.dumps({"model": payload["model"], "done": True,
                                   "done_reason": "stop", "prompt_eval_count": 100,
                                   "message": {"content": json.dumps({"suggestions": answers})}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        local_model = ThreadingHTTPServer(("127.0.0.1", 0), LocalModel)
        Thread(target=local_model.serve_forever, daemon=True).start()
        review_fixture = {"local_url": f"http://127.0.0.1:{local_model.server_port}",
                          "period_id": year.id, "accounts": accounts}
        (root / "local-requests.json").write_text("[]")
        (root / "review.csv").write_text(
            "Date,Description,Amount\n2026-02-01,Cedar printer paper receipt,-33.33\n")
    (root / "fixture.json").write_text(json.dumps({
        "client_id": client_id, "other_client_id": other_id, "schedule_id": schedule.id,
        "draft_id": generated["draft_id"], "book": str(dbc.DATABASE_PATH),
        **review_fixture,
    }))
    (root / "cedar-january.csv").write_text(BANK_CSV)
    dbc.clear_active_key()
    if args.app:
        env = os.environ.copy()
        env.update(LEDGERTB_MODE="server", LEDGERTB_PORT=str(args.port),
                   LEDGERTB_PARENT_PID=str(os.getpid()),
                   PYTHON_KEYRING_BACKEND="review_fake_vault.Keyring",
                   LEDGERTB_FIXTURE_DIR=str(root))
        child = subprocess.Popen([str(args.app.resolve() / "Contents/MacOS/LedgerTB")], env=env)

        def stop_child(*_):
            child.terminate()
            # Unwind the interrupted wait before waiting again: subprocess's
            # wait lock is not reentrant inside a signal handler.
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, stop_child)
        try:
            raise SystemExit(child.wait())
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
    from streamlit.web import bootstrap
    options = {
        "server.address": "127.0.0.1", "server.port": args.port,
        "server.headless": True, "browser.gatherUsageStats": False,
    }
    bootstrap.load_config_options(options)
    bootstrap.run(str(Path(__file__).resolve().parents[1] / "app.py"), False, [], options)


if __name__ == "__main__":
    main()
