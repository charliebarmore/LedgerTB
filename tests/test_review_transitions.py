"""Unfinished review protection uses only the autouse fake vault/encrypted book."""
from copy import deepcopy

from services.csv_import import CSVImporter
from tests.test_jev_review import page


def button(at, label):
    return next(b for b in at.button if b.label == label)


def csv_page(monkeypatch, client_id, accounts, fake_credential_vault):
    at, row = page(monkeypatch, client_id, accounts, fake_credential_vault)
    at.session_state['import_active_tab'] = 'Upload CSV'
    at.session_state['csv_content'] = 'Date,Description,Amount\n2026-01-02,Fictional replacement,-10.00\n'
    at.session_state['csv_filename'] = 'replacement.csv'
    at.run()
    assert not at.exception
    at.checkbox(key='csv_confirm').check().run()
    return at, row


def test_invalid_replacement_never_clears_existing_review(monkeypatch, client_id, accounts, fake_credential_vault):
    at, row = csv_page(monkeypatch, client_id, accounts, fake_credential_vault)
    original = deepcopy(at.session_state['transactions_to_review'])
    def invalid(*args, **kwargs):
        raise ValueError('Fictional invalid transaction')
    monkeypatch.setattr(CSVImporter, 'parse_csv', invalid)
    button(at, 'Continue to review').click().run()
    assert not at.exception
    assert at.session_state['transactions_to_review'] == original
    assert any('Fictional invalid transaction' in e.value for e in at.error)


import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest
from database import connection as dbconn
from models.client import Client
from models.journal_entry import JournalEntry
from services import import_review_drafts as drafts
from tests.conftest import page_path
from utils.import_review import ensure_row_ids, row_key
from utils.review_guard import mark_saved, review_is_dirty, review_snapshot


def test_replacement_cancel_preserves_old_review_and_import(monkeypatch, client_id, accounts, fake_credential_vault):
    at, _ = csv_page(monkeypatch, client_id, accounts, fake_credential_vault)
    original = review_snapshot(at.session_state.filtered_state)
    button(at, 'Continue to review').click().run()
    assert not at.exception
    assert at.session_state['transactions_to_review'] == original
    assert at.session_state['_review_replacement']['rows'][0]['amount'] == -10
    button(at, 'Cancel').click().run()
    assert not at.exception and at.session_state['transactions_to_review'] == original
    assert at.session_state['csv_content'].startswith('Date,Description,Amount')
    assert drafts.summary(client_id) is None and JournalEntry.count(client_id) == 0


def test_replacement_saves_old_copy_before_installing_candidate(monkeypatch, client_id, accounts, fake_credential_vault):
    at, _ = csv_page(monkeypatch, client_id, accounts, fake_credential_vault)
    button(at, 'Continue to review').click().run()
    button(at, 'Save and continue').click().run()
    assert not at.exception
    assert drafts.load(client_id)[1][0]['amount'] == -33.33
    assert at.session_state['transactions_to_review'][0]['amount'] == -10
    assert any('Unsaved changes' in c.value for c in at.caption)
    assert JournalEntry.count(client_id) == 0


@pytest.mark.parametrize('failure', ['conflict', 'storage'])
def test_save_failure_blocks_replacement(monkeypatch, client_id, accounts, fake_credential_vault, failure):
    at, _ = csv_page(monkeypatch, client_id, accounts, fake_credential_vault)
    old = review_snapshot(at.session_state.filtered_state)
    if failure == 'conflict':
        revision = drafts.save(client_id, [dict(old[0], description='Other window copy')])
    else:
        def fail(*args, **kwargs):
            raise OSError('disk full')
        monkeypatch.setattr(drafts, 'save', fail)
    button(at, 'Continue to review').click().run()
    button(at, 'Save and continue').click().run()
    assert not at.exception and at.error
    assert at.session_state['transactions_to_review'] == old
    assert at.session_state['_review_replacement']['rows']
    assert JournalEntry.count(client_id) == 0
    if failure == 'conflict':
        assert drafts.load(client_id)[0] == revision
    button(at, 'Cancel').click().run()
    assert at.session_state['transactions_to_review'] == old


def test_dirty_readonly_review_can_cancel_but_cannot_save(monkeypatch, client_id, accounts, fake_credential_vault):
    at, _ = csv_page(monkeypatch, client_id, accounts, fake_credential_vault)
    monkeypatch.setattr(dbconn, 'READ_ONLY', True)
    button(at, 'Continue to review').click().run()
    assert not at.exception and button(at, 'Save and continue').disabled
    assert not button(at, 'Cancel').disabled
    button(at, 'Cancel').click().run()
    assert len(at.session_state['transactions_to_review']) == 1
    assert drafts.summary(client_id) is None


def test_saved_status_tracks_evidence_and_widgets_not_sorting(client_id, accounts):
    rows = ensure_row_ids([
        dict(date='2026-01-01', description='Fictional paper', amount=-33.33, bank_account_id=accounts['cash'], selected_account_id=accounts['expense']),
        dict(date='2026-01-02', description='Fictional sale', amount=100, bank_account_id=accounts['cash'], selected_account_id=accounts['revenue']),
    ])
    state = {'_import_state_client_id': (str(dbconn.DATABASE_PATH), client_id), 'transactions_to_review': rows}
    revision = drafts.save(client_id, rows)
    mark_saved(state, client_id, rows, revision)
    assert not review_is_dirty(state, client_id)
    rows.reverse()
    assert not review_is_dirty(state, client_id)
    state[row_key('include', rows[0])] = False
    assert review_is_dirty(state, client_id)
    assert not review_snapshot(state)[0]['include']
    state.pop(row_key('include', rows[0]))
    rows[0]['receipt_text'] = 'New receipt evidence'
    assert review_is_dirty(state, client_id)
    rows[0].pop('receipt_text')
    drafts.save(client_id, rows, expected_revision=revision)
    assert review_is_dirty(state, client_id)  # Another window changed the durable copy.


def selector_page(monkeypatch, client_id, accounts):
    import utils.client_selector as selector
    monkeypatch.setattr(st, 'page_link', lambda *a, **kw: None)
    monkeypatch.setattr(selector, 'render_safety_status', lambda: None)
    monkeypatch.setattr(st.sidebar, 'page_link', lambda *a, **kw: None)
    at = AppTest.from_string('''
import streamlit as st
from utils.client_selector import render_client_selector
selected = render_client_selector()
st.text(f"Effective client: {selected}")
''', default_timeout=30)
    rows = ensure_row_ids([dict(date='2026-01-01', description='Unfinished fictional review', amount=-20, bank_account_id=accounts['cash'], include=False)])
    at.session_state['selected_client_id'] = client_id
    at.session_state['_import_state_client_id'] = (str(dbconn.DATABASE_PATH), client_id)
    at.session_state['transactions_to_review'] = rows
    at.run()
    assert not at.exception
    return at


def test_client_cancel_resets_selector_and_save_stays_with_old_client(monkeypatch, client_id, accounts):
    second = Client(name='Other fictional studio').save(seed_accounts=False)
    at = selector_page(monkeypatch, client_id, accounts)
    at.selectbox(key='client_selector').select(second).run()
    assert not at.exception and at.session_state['selected_client_id'] == client_id
    button(at, 'Cancel').click().run()
    assert not at.exception
    assert next(s for s in at.selectbox if s.label == 'Select Client').value == client_id
    next(s for s in at.selectbox if s.label == 'Select Client').select(second).run()
    button(at, 'Save and continue').click().run()
    assert not at.exception and at.session_state['selected_client_id'] == second
    assert drafts.load(client_id)[1][0]['description'] == 'Unfinished fictional review'
    assert drafts.summary(second) is None
    assert not at.session_state.filtered_state.get('transactions_to_review')
    assert JournalEntry.count(client_id) == JournalEntry.count(second) == 0


def test_book_cancel_and_failed_save_keep_key_and_review(monkeypatch, client_id, accounts):
    import utils.client_selector as selector
    import utils.book_lock as locks
    monkeypatch.setattr(selector, 'render_client_selector', lambda: client_id)
    monkeypatch.setattr(st, 'page_link', lambda *a, **kw: None)
    released = []
    monkeypatch.setattr(locks, 'release', lambda path: released.append(path))
    at = AppTest.from_file(page_path('pages/9_Data_Safety.py'), default_timeout=30)
    at.session_state['_import_state_client_id'] = (str(dbconn.DATABASE_PATH), client_id)
    at.session_state['transactions_to_review'] = ensure_row_ids([dict(date='2026-01-01', description='Unfinished book review', amount=-20, bank_account_id=accounts['cash'])])
    at.run()
    button(at, 'Switch book…').click().run()
    assert not at.exception and dbconn.has_active_key() and not released
    def fail(*a, **kw):
        raise OSError('disk full')
    monkeypatch.setattr(drafts, 'save', fail)
    button(at, 'Save and continue').click().run()
    assert not at.exception and at.error and dbconn.has_active_key() and not released
    button(at, 'Cancel').click().run()
    assert not at.exception and dbconn.has_active_key() and not released
    assert len(at.session_state['transactions_to_review']) == 1


def test_clear_review_cancel_and_discard_leave_saved_copy(monkeypatch, client_id, accounts, fake_credential_vault):
    at, _ = page(monkeypatch, client_id, accounts, fake_credential_vault)
    at.run()
    button(at, 'Save review for later').click().run()
    revision = drafts.summary(client_id)['revision']
    row = at.session_state['transactions_to_review'][0]
    at.checkbox(key=row_key('include', row)).check().run()
    button(at, 'Clear review list').click().run()
    assert not at.exception
    assert not any(b.label == 'Post Transactions' for b in at.button)
    button(at, 'Cancel').click().run()
    assert len(at.session_state['transactions_to_review']) == 1
    button(at, 'Clear review list').click().run()
    button(at, 'Discard changes').click().run()
    assert not at.exception and not at.session_state['transactions_to_review']
    assert drafts.summary(client_id)['revision'] == revision
    assert not drafts.load(client_id)[1][0]['include']
