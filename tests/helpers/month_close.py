"""Deterministic fictional January. Expected balances are specified independently.

Source labels describe human review decisions, not categorizer predictions.
Card CSV uses positive charges; staged rows use negative money-out amounts.
"""
import csv
from datetime import date
from io import StringIO

from models.account import Account
from models.client import Client
from models.fiscal_period import FiscalPeriod
from services.csv_import import CSVImporter, apply_sign_convention
from services.import_identity import hash_source
from utils.import_review import ensure_row_ids

JANUARY = (date(2026, 1, 1), date(2026, 1, 31))
BALANCES = {
    '1000': (1_506_500, 0), '2000': (0, 130_000), '2100': (0, 30_000),
    '3000': (0, 600_000), '3100': (6_000, 0), '4000': (0, 1_000_000),
    '6100': (71_000, 0), '6200': (138_000, 0), '6300': (1_000, 0),
    '6400': (7_500, 0), '6500': (30_000, 0),
}
CHART = [
    ('cash', '1000', 'Checking', 'Asset', 'Cash'),
    ('card', '2000', 'Credit Card', 'Liability', 'Credit Card'),
    ('accrual', '2100', 'Accrued Wages', 'Liability', 'Other Current Liability'),
    ('capital', '3000', 'Owner Capital', 'Equity', 'Owner Contribution'),
    ('draw', '3100', 'Owner Draw', 'Equity', 'Owner Distribution'),
    ('revenue', '4000', 'Service Revenue', 'Revenue', 'Operating Revenue'),
    ('office', '6100', 'Office Supplies', 'Expense', 'Operating Expense'),
    ('software', '6200', 'Software', 'Expense', 'Operating Expense'),
    ('fees', '6300', 'Bank Fees', 'Expense', 'Operating Expense'),
    ('repairs', '6400', 'Repairs', 'Expense', 'Operating Expense'),
    ('wages', '6500', 'Wages', 'Expense', 'Operating Expense'),
]


def create_month():
    client_id = Client(name='Juniper Month Services', entity_type='Sole Proprietor',
                       fiscal_year_end_month=12).save(seed_accounts=False)
    other_id = Client(name='Willow Empty Fictional Client').save(seed_accounts=False)
    accounts = {key: Account(client_id=client_id, account_number=number, name=name,
                             type=kind, subtype=subtype).save()
                for key, number, name, kind, subtype in CHART}
    FiscalPeriod.ensure_periods_exist(client_id, 2026, 12)
    return client_id, other_id, accounts


def labeled_sources():
    sources = {'cash': [], 'card': []}
    def series(source, count, cents, label, target, outcome='account'):
        for index in range(count):
            rows = sources[source]
            rows.append(dict(date=f'2026-01-{2 + len(rows) % 25:02}',
                             description=f'Juniper {label} {index + 1:03}',
                             cents=cents, target=target, outcome=outcome,
                             include=outcome != 'mirror', transfer=outcome == 'transfer'))
    series('cash', 100, 10000, 'customer invoice receipt', 'revenue')
    series('cash', 30, -1250, 'paper receipt office use', 'office')
    series('cash', 5, 500, 'paper return refund', 'office')
    series('cash', 5, -200, 'bank service fee', 'fees')
    series('cash', 4, -10000, 'card payment transfer', 'card', 'transfer')
    series('cash', 2, 50000, 'owner capital deposit', 'capital')
    series('cash', 1, -10000, 'mixed office and personal receipt', 'office', 'split_required')
    series('cash', 1, -7500, 'unknown marketplace missing receipt', 'repairs', 'insufficient_information')
    sources['cash'].extend(dict(r, outcome='duplicate', include=False) for r in sources['cash'][:2])
    series('card', 70, -2000, 'software subscription receipt', 'software')
    series('card', 20, -1500, 'stationery receipt office use', 'office')
    series('card', 5, 1000, 'software refund', 'software')
    series('card', 4, 10000, 'payment received checking mirror', 'cash', 'mirror')
    series('card', 1, -5000, 'mixed software and personal receipt', 'software', 'split_required')
    return sources


def csv_sources():
    result = {}
    for source, rows in labeled_sources().items():
        out = StringIO(newline='')
        writer = csv.writer(out)
        writer.writerow(['Date', 'Description', 'Amount'])
        for row in rows:
            signed = row['cents'] * (-1 if source == 'card' else 1)
            writer.writerow([row['date'], row['description'], f'{signed / 100:.2f}'])
        result[source] = out.getvalue()
    return result


def review_rows(accounts):
    """Parse actual source files; attach independently labeled human decisions."""
    rows = []
    for source, content in csv_sources().items():
        parsed = CSVImporter.parse_csv(content, date_column='Date', description_column='Description',
                                       amount_column='Amount', source_id=hash_source(content.encode()),
                                       source_filename=f'juniper-{source}-january.csv')
        for row, label in zip(parsed, labeled_sources()[source]):
            row['amount'] = apply_sign_convention(row['amount'], 'credit_card' if source == 'card' else 'bank')
            row.update(bank_account_id=accounts[source], include=label['include'],
                       is_transfer=label['transfer'],
                       selected_account_id=(None if label['outcome'] in ('split_required', 'insufficient_information')
                                            else accounts[label['target']]))
            rows.append(row)
    return ensure_row_ids(rows)
