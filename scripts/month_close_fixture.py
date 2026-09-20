"""Create only an empty, disposable fictional month directory; never open user books."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PASSPHRASE = 'fictional-packaged-acceptance-only'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    args = parser.parse_args()
    root = args.data_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        parser.error('Use an empty disposable directory')
    for prefix in ('LEDGERTB', 'PROBOOKS'):
        for suffix in ('DB_PATH', 'BACKUP_DIR'):
            os.environ.pop(f'{prefix}_{suffix}', None)
    os.environ.update(LEDGERTB_DATA_DIR=str(root), PYTHON_DOTENV_DISABLED='1',
                      PYTHON_KEYRING_BACKEND='keyring.backends.fail.Keyring',
                      ANTHROPIC_API_KEY='fake-not-used', OPENAI_API_KEY='fake-not-used', TYPESAFE_API_KEY='fake-not-used')
    from utils import secure_store
    secure_store.get_secret = lambda name: None
    secure_store.set_secret = lambda *a: None
    secure_store.delete_secret = lambda *a: None
    from database import connection as db
    from database.crypto import derive_key
    from models.journal_entry import JournalEntry, JournalEntryLine
    from services import import_review_drafts as drafts
    from services.import_identity import classify_import_duplicates
    from tests.helpers.month_close import BALANCES, JANUARY, create_month, csv_sources, labeled_sources, review_rows
    from utils.books import set_active_book
    if not db.ENCRYPTION_AVAILABLE:
        raise RuntimeError('The fixture requires SQLCipher')
    db.set_active_key(derive_key(PASSPHRASE))
    books = []
    for filename in ('accounting.db', 'Books/Second.ledgertb'):
        db.DATABASE_PATH = root / filename
        db.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        db.init_database()
        client_id, other_id, accounts = create_month()
        JournalEntry(client_id=client_id, entry_date=JANUARY[0], description='Fictional opening capital',
                     entry_type='Beginning Balance', lines=[
                         JournalEntryLine(account_id=accounts['cash'], debit=5000),
                         JournalEntryLine(account_id=accounts['capital'], credit=5000),
                     ]).save()
        rows = review_rows(accounts)
        classify_import_duplicates(rows, client_id)
        for row in rows:
            row['batch_id'] = 'juniper-january'
        drafts.save(client_id, rows)
        books.append(dict(path=str(db.DATABASE_PATH), client_id=client_id, other_id=other_id, accounts=accounts))
        set_active_book(db.DATABASE_PATH)
    set_active_book(root / 'accounting.db')
    for source, content in csv_sources().items():
        (root / f'juniper-{source}-january.csv').write_text(content)
    (root / 'synthetic-fixture.json').write_text(json.dumps(dict(synthetic=True, books=books), indent=2))
    (root / 'expected.json').write_text(json.dumps(dict(labels=labeled_sources(), ending_balances_cents=BALANCES,
        source_rows=250, posted_imports=244, total_entries=248, trial_balance_cents=1760000,
        net_income_cents=752500), indent=2))
    db.clear_active_key()
    print('Created two encrypted fictional month books, two CSVs and independent expected results.')


if __name__ == '__main__':
    main()
