"""Small, fictional book shared by workflow and browser acceptance tests."""

from datetime import date

from models.account import Account
from models.client import Client
from models.fiscal_period import FiscalPeriod
from models.recurring_entry import JournalEntryTemplate, RecurringSchedule, TemplateLine


JANUARY = (date(2026, 1, 1), date(2026, 1, 31))
BANK_CSV = (
    "Date,Description,Amount\n"
    "2026-01-05,Cedar demo customer receipt,2500.00\n"
    "2026-01-08,Cedar demo office supplies,-300.00\n"
)
# Independently specified expected ending balances in integer cents.
JANUARY_BALANCES = {
    "1000": (1_220_000, 0), "2100": (0, 120_000),
    "3000": (0, 1_000_000), "4000": (0, 250_000),
    "6000": (30_000, 0), "6100": (120_000, 0),
}


def create_cedar():
    client_id = Client(name="Cedar Demo Services", fiscal_year_end_month=12).save(seed_accounts=False)
    other_id = Client(name="Maple Empty Demo", fiscal_year_end_month=6).save(seed_accounts=False)
    accounts = {}
    for key, number, name, kind, subtype in [
        ("cash", "1000", "Cash", "Asset", "Cash"),
        ("accrual", "2100", "Accrued Expenses", "Liability", "Current Liability"),
        ("capital", "3000", "Owner Capital", "Equity", "Owner's Equity"),
        ("revenue", "4000", "Service Revenue", "Revenue", "Operating Revenue"),
        ("office", "6000", "Office Expense", "Expense", "Operating Expense"),
        ("rent", "6100", "Rent Expense", "Expense", "Operating Expense"),
    ]:
        account = Account(client_id=client_id, account_number=number, name=name,
                          type=kind, subtype=subtype)
        account.save()
        accounts[key] = account.id
    FiscalPeriod.ensure_periods_exist(client_id, 2026, 12)
    template = JournalEntryTemplate(
        client_id=client_id, name="Monthly rent accrual", description="January rent accrual",
        entry_type="Adjusting", source_reference="Cedar rent workpaper",
        lines=[TemplateLine(accounts["rent"], debit_cents=120_000),
               TemplateLine(accounts["accrual"], credit_cents=120_000)],
    )
    template.save()
    schedule = RecurringSchedule(template_id=template.id, starts_on=JANUARY[0],
                                 reversal_rule="NextDay")
    schedule.save()
    return client_id, other_id, accounts, schedule
