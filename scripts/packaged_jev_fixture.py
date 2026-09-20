"""Prepare or inspect disposable encrypted books for packaged-app acceptance.

No real vault access. Refuses nonempty directories at creation and requires its
own synthetic marker before inspection. Run in a separate process from the app.
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PASSPHRASE = "fictional-packaged-acceptance-only"
CSV = ("Date,Description,Amount\n"
       "2026-09-01,Cedar Paper receipt printer paper solely for design studio,-33.33\n"
       "2026-09-02,Unknown marketplace no receipt,-48.25\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    root = args.data_dir.resolve()
    marker = root / "synthetic-fixture.json"
    if args.verify:
        if not marker.is_file() or json.loads(marker.read_text()).get("synthetic") is not True:
            parser.error("Inspection requires this tool's synthetic fixture marker")
    else:
        root.mkdir(parents=True, exist_ok=True)
        if any(root.iterdir()):
            parser.error("Preparation requires an empty disposable directory")
    os.environ.update(LEDGERTB_DATA_DIR=str(root), ANTHROPIC_API_KEY="fake-not-used",
                      PYTHON_DOTENV_DISABLED="1")
    for prefix in ("LEDGERTB", "PROBOOKS"):
        for suffix in ("DB_PATH", "BACKUP_DIR"):
            os.environ.pop(f"{prefix}_{suffix}", None)
    from utils import secure_store
    secure_store.get_secret = lambda name: None
    secure_store.set_secret = lambda *args: None
    secure_store.delete_secret = lambda *args: None
    from database import connection as db
    from database.crypto import derive_key
    from models.client import Client
    from models.account import Account
    if not db.ENCRYPTION_AVAILABLE:
        raise RuntimeError("Disposable acceptance books require SQLCipher")
    db.set_active_key(derive_key(PASSPHRASE))
    if args.verify:
        with db.get_cursor() as cursor:
            report = {}
            for table in ("journal_entries", "journal_entry_lines", "imported_transactions", "audit_log"):
                report[table] = [dict(row) for row in cursor.execute(f"SELECT * FROM {table}").fetchall()]
        report["encrypted_header"] = (root / "accounting.db").read_bytes()[:16] != b"SQLite format 3\x00"
        report["book_counts"] = []
        for book in json.loads(marker.read_text())["books"]:
            path = Path(book["path"]).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                parser.error("Every inspected fixture book must stay inside its disposable directory")
            db.DATABASE_PATH = path
            with db.get_cursor() as cursor:
                report["book_counts"].append({
                    "name": path.name,
                    "client_id": book["client_id"],
                    "journal_entries": cursor.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0],
                    "imported_transactions": cursor.execute("SELECT COUNT(*) FROM imported_transactions").fetchone()[0],
                    "encrypted_header": path.read_bytes()[:16] != b"SQLite format 3\x00",
                })
        print(json.dumps(report, indent=2, default=str))
    else:
        books = []
        for filename, name in (("accounting.db", "Cedar Synthetic Studio"),
                               ("Books/Maple.ledgertb", "Maple Synthetic Studio")):
            db.DATABASE_PATH = root / filename
            db.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
            db.init_database()
            client = Client(name=name, entity_type="S-Corp", business_type="Graphic design",
                            business_context="Fictional remote design studio. Require purchase facts and business purpose; unknown purchases need clarification.")
            client_id = client.save(seed_accounts=False)
            accounts = {}
            for number, label, kind, subtype in (
                ("1000", "Checking", "Asset", "Cash"),
                ("2000", "Credit Card", "Liability", None),
                ("4000", "Design Revenue", "Revenue", None),
                ("6100", "Office Supplies", "Expense", None),
                ("6200", "Software", "Expense", None),
            ):
                account = Account(client_id=client_id, account_number=number, name=label, type=kind, subtype=subtype)
                accounts[number] = account.save()
            books.append({"path": str(db.DATABASE_PATH), "client_id": client_id, "accounts": accounts})
        from utils.books import set_active_book
        set_active_book(root / "Books/Maple.ledgertb")
        set_active_book(root / "accounting.db")
        marker.write_text(json.dumps({"synthetic": True, "books": books}, indent=2))
        (root / "synthetic-import.csv").write_text(CSV)
        print("Created two disposable encrypted books and fictional CSV")
    db.clear_active_key()


if __name__ == "__main__":
    main()
