from datetime import date

import pytest

from database.connection import get_cursor
from models.account import Account
from models.fiscal_period import FiscalPeriod
from models.journal_entry import JournalEntry, JournalEntryLine
from models.reconciliation import BankReconciliation
from models.reports import ReportGenerator
from services.import_corrections import correct_imported_category
from services.import_batch_reversal import reverse_import_batch
from services.posting import post_transaction


def cash_entry(client_id, accounts, amount=100):
    entry = JournalEntry(client_id=client_id, entry_date=date(2026, 1, 10),
        description='Synthetic cash receipt', lines=[
            JournalEntryLine(account_id=accounts['cash'], debit=amount),
            JournalEntryLine(account_id=accounts['revenue'], credit=amount)])
    entry.save()
    return entry


def imported_expense(client_id, accounts):
    return post_transaction(client_id,
        {'date': date(2026, 1, 10), 'description': 'Synthetic office purchase',
         'amount': -100, 'source_id': 'synthetic-statement', 'source_row_number': 1},
        accounts['expense'], accounts['cash'], batch_id='REVIEW', learn=False)


def new_expense(client_id, number, name):
    account = Account(client_id=client_id, account_number=number,
                      name=name, type='Expense')
    account.save()
    return account.id


def test_close_blocks_post_that_was_validated_before_close(client_id, accounts, monkeypatch):
    import models.journal_entry as module
    period = FiscalPeriod(client_id=client_id, period_name='2026', period_type='Year',
                          start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
    period.save()
    real_connection = module.get_connection

    def close_before_opening_post_connection():
        FiscalPeriod.set_closed(period.id, True, client_id=client_id,
            confirmation={'explicit_confirmation': True, 'warnings_acknowledged': True,
                          'close_map_exception_reason': 'Synthetic concurrency check'})
        return real_connection()

    # Reproduce the interleaving after the old preflight check, before posting.
    monkeypatch.setattr(module, 'get_connection', close_before_opening_post_connection)
    with pytest.raises(ValueError, match='closed'):
        cash_entry(client_id, accounts)
    assert JournalEntry.count(client_id) == 0


@pytest.mark.parametrize("amount", [-100, 100])
def test_reversing_category_correction_restores_effective_category(client_id, accounts, amount):
    second = new_expense(client_id, '6100', 'Synthetic travel')
    final = new_expense(client_id, '6200', 'Synthetic supplies')
    original, imported = post_transaction(client_id,
        {"date": date(2026, 1, 10), "description": "Synthetic import", "amount": amount},
        accounts["expense"], accounts["cash"], learn=False)
    correction = correct_imported_category(client_id=client_id,
        journal_entry_id=original.id, target_account_id=second,
        correction_date=date(2026, 1, 11), reason='First correction')
    JournalEntry.reverse(correction.id, client_id, date(2026, 1, 12))
    correct_imported_category(client_id=client_id,
        journal_entry_id=original.id, target_account_id=final,
        correction_date=date(2026, 1, 13), reason='Correct after undo')
    statement = ReportGenerator.income_statement(client_id, date(2026, 1, 1), date(2026, 1, 31))
    actual = {row['account_number']: row['balance'] for row in statement['expenses']}
    assert actual == {'6200': -amount}, actual


def test_superseded_import_cannot_be_recategorized(client_id, accounts):
    second = new_expense(client_id, '6100', 'Synthetic travel')
    original, imported = imported_expense(client_id, accounts)
    reverse_import_batch(client_id=client_id, batch_id='REVIEW',
        reversal_date=date(2026, 1, 11), reason='Undo synthetic import')
    try:
        correct_imported_category(client_id=client_id, journal_entry_id=original.id,
            target_account_id=second, correction_date=date(2026, 1, 12),
            reason='Attempt correction of superseded source')
    except ValueError:
        return
    statement = ReportGenerator.income_statement(client_id, date(2026, 1, 1), date(2026, 1, 31))
    actual = {row['account_number']: row['balance'] for row in statement['expenses']}
    assert actual == {}, actual


def test_later_reconciliation_does_not_clear_an_earlier_statement(client_id, accounts):
    entry = cash_entry(client_id, accounts)
    feb = BankReconciliation.create(client_id, accounts['cash'], date(2026, 2, 1),
                                    date(2026, 2, 28), 100)
    feb.save_selected_lines([entry.lines[0].id])
    feb.complete()
    with pytest.raises(ValueError, match='newer reconciliations'):
        BankReconciliation.create(client_id, accounts['cash'], date(2026, 1, 1),
                                  date(2026, 1, 31), 100)


def test_import_batch_reversal_cannot_be_edited_in_place(client_id, accounts):
    original, imported = imported_expense(client_id, accounts)
    reverse_import_batch(client_id=client_id, batch_id='REVIEW',
        reversal_date=date(2026, 1, 11), reason='Undo synthetic import')
    with get_cursor() as cur:
        reversal_id = cur.execute('SELECT reversal_journal_entry_id FROM imported_transactions WHERE id=?', (imported.id,)).fetchone()[0]
    reversal = JournalEntry.get_by_id(reversal_id, client_id=client_id)
    for line in reversal.lines:
        line.debit *= 2
        line.credit *= 2
    with pytest.raises(ValueError, match='cannot be edited'):
        reversal.save()



def test_legacy_nonzero_reversal_pair_stays_visible(client_id, accounts):
    original, imported = imported_expense(client_id, accounts)
    reverse_import_batch(client_id=client_id, batch_id='REVIEW',
        reversal_date=date(2026, 1, 11), reason='Undo synthetic import')
    # Simulate a book edited by an older release, before history was protected.
    with get_cursor(commit=True) as cur:
        reversal_id = cur.execute('SELECT reversal_journal_entry_id FROM imported_transactions WHERE id=?', (imported.id,)).fetchone()[0]
        cur.execute('UPDATE journal_entry_lines SET debit=debit*2, credit=credit*2 WHERE journal_entry_id=?', (reversal_id,))
    cash_entry(client_id, accounts, amount=50)
    raw = ReportGenerator.general_ledger(accounts['cash'], client_id=client_id)
    visible, hidden = ReportGenerator.compact_reversed_import_entries(raw, 'Asset')
    assert hidden == 0
    assert visible[-1].balance == raw[-1].balance == 150


def test_correction_history_is_immutable_and_reverse_updates_source(client_id, accounts):
    from models.transaction import ImportedTransaction
    second = new_expense(client_id, '6100', 'Synthetic travel')
    original, imported = imported_expense(client_id, accounts)
    correction = correct_imported_category(client_id=client_id,
        journal_entry_id=original.id, target_account_id=second,
        correction_date=date(2026, 1, 11), reason='First correction')
    correction.description = 'Attempt to edit history'
    with pytest.raises(ValueError, match='history'):
        correction.save()
    with pytest.raises(ValueError, match='history'):
        JournalEntry.delete(correction.id, client_id=client_id)
    reversal = JournalEntry.reverse(correction.id, client_id, date(2026, 1, 12))
    link = ImportedTransaction.get_links_for_journal_entries(client_id, [original.id])[original.id]
    assert link['suggested_account_id'] == accounts['expense']
    with pytest.raises(ValueError, match='history'):
        JournalEntry.delete(reversal.id, client_id=client_id)


def test_out_of_order_correction_reversal_rolls_back(client_id, accounts):
    second = new_expense(client_id, '6100', 'Synthetic travel')
    final = new_expense(client_id, '6200', 'Synthetic supplies')
    original, _ = imported_expense(client_id, accounts)
    correction = correct_imported_category(client_id=client_id,
        journal_entry_id=original.id, target_account_id=second,
        correction_date=date(2026, 1, 11), reason='First correction')
    correct_imported_category(client_id=client_id,
        journal_entry_id=original.id, target_account_id=final,
        correction_date=date(2026, 1, 12), reason='Second correction')
    with pytest.raises(ValueError, match='history needs review'):
        JournalEntry.reverse(correction.id, client_id, date(2026, 1, 13))
    assert JournalEntry.count(client_id) == 3


def test_existing_earlier_draft_does_not_inherit_future_clearing(client_id, accounts):
    entry = cash_entry(client_id, accounts)
    feb = BankReconciliation.create(client_id, accounts['cash'], date(2026, 2, 1),
                                    date(2026, 2, 28), 100)
    feb.save_selected_lines([entry.lines[0].id])
    feb.complete()
    # Simulate an earlier draft that an older release allowed after February.
    with get_cursor(commit=True) as cur:
        cur.execute("INSERT INTO bank_reconciliations (client_id, account_id, statement_start_date, statement_end_date, statement_ending_balance) VALUES (?, ?, '2026-01-01', '2026-01-31', 10000)",
                    (client_id, accounts['cash']))
        jan_id = cur.lastrowid
    jan = BankReconciliation.get_by_id(jan_id, client_id)
    assert jan.cleared_balance() == 0
    assert jan.difference() == 100
    with pytest.raises(ValueError, match='out of balance'):
        jan.complete()


def test_close_holds_write_lock_through_checklist_and_commit(client_id, accounts, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event
    period = FiscalPeriod(client_id=client_id, period_name='2026', period_type='Year',
                          start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
    period.save()
    checked, resume, posting_started = Event(), Event(), Event()
    real_check = FiscalPeriod._close_checklist

    def paused_check(*args):
        result = real_check(*args)
        checked.set()
        assert resume.wait(5)
        return result

    def post():
        posting_started.set()
        return cash_entry(client_id, accounts)

    monkeypatch.setattr(FiscalPeriod, '_close_checklist', staticmethod(paused_check))
    with ThreadPoolExecutor(max_workers=2) as pool:
        closing = pool.submit(FiscalPeriod.set_closed, period.id, True, client_id,
            {'explicit_confirmation': True, 'warnings_acknowledged': True,
             'close_map_exception_reason': 'Synthetic race test'})
        try:
            assert checked.wait(5)
            posting = pool.submit(post)
            assert posting_started.wait(5)
            with pytest.raises(TimeoutError):
                posting.result(timeout=0.1)
        finally:
            resume.set()
        closing.result(timeout=5)
        with pytest.raises(ValueError, match='closed'):
            posting.result(timeout=5)
    assert JournalEntry.count(client_id) == 0
