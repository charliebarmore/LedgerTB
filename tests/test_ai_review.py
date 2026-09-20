"""Streamlit reruns and human acceptance for independent provider opinions."""
import pytest
from streamlit.testing.v1 import AppTest
import streamlit as st
from tests.conftest import page_path
from tests.test_jev_review import page
from tests.test_jev_categorization import response as jev_response
from tests.test_review_categorization import response
from services import review_categorization as ai, jev_categorization as jev
from utils.import_review import row_key, scope_import_state_to_client


def fixture(monkeypatch, client_id, accounts, fake_credential_vault, provider='anthropic'):
    calls = []
    def send(p, payload, key):
        calls.append((p, payload))
        return response(p, payload, f"account_{accounts['revenue']}")
    monkeypatch.setattr(ai, 'send_request', send)
    monkeypatch.setattr(jev, 'send_request', lambda p,k: jev_response(p, f"account_{accounts['expense']}"))
    at, row = page(monkeypatch, client_id, accounts, fake_credential_vault)
    fake_credential_vault.update(anthropic_api_key='fake', openai_api_key='fake')
    at.run()
    at.multiselect(key='bulk_rows').set_value([row['uid']]).run()
    next(w for w in at.selectbox if w.label == 'AI provider').set_value(provider).run()
    return at, row, calls


def ask(at):
    at.checkbox(key='ai_review_consent').check().run()
    at.button(key='ai_review_run').click().run()
    assert not at.exception


def test_second_opinions_keep_first_and_inclusion(monkeypatch, client_id, accounts, fake_credential_vault):
    at, row, calls = fixture(monkeypatch, client_id, accounts, fake_credential_vault, 'jev')
    at.checkbox(key='jev_consent').check().run()
    at.button(key='jev_run').click().run()
    next(b for b in at.button if b.label == 'Choose another AI').click().run()
    assert next(w for w in at.selectbox if w.label == 'AI provider').value == 'anthropic'
    assert not at.checkbox(key='ai_review_consent').value and not calls
    ask(at)
    assert len(calls) == 1
    assert any('opinions disagree' in x.value for x in at.warning)
    assert any(b.label == 'Accept account suggestion' for b in at.button)
    assert at.session_state[row_key('cat', row)] is None
    assert not at.session_state[row_key('include', row)]
    at.run()
    at.button(key='ai_review_run').click().run()
    assert len(calls) == 1
    next(b for b in at.button if b.label == 'Accept Anthropic suggestion').click().run()
    assert at.session_state[row_key('cat', row)] == accounts['revenue']
    assert not at.session_state[row_key('include', row)]
    next(w for w in at.selectbox if w.label == 'AI provider').set_value('openai').run()
    assert not at.checkbox(key='ai_review_consent').value
    assert at.session_state[row_key('cat', row)] == accounts['revenue']
    ask(at)
    assert len(calls) == 2
    assert any(b.label == 'Accept OpenAI suggestion' for b in at.button)
    assert 'Fictional receipt identifies' not in str(calls[1][1])  # no prior answer sent
    at.selectbox(key='ai_review_model_openai').set_value('gpt-4o-mini').run()
    assert not at.checkbox(key='ai_review_consent').value and len(calls) == 2
    ask(at)
    assert len(calls) == 3
    assert sum(b.label == 'Accept OpenAI suggestion' for b in at.button) == 2
    assert not at.exception


@pytest.mark.parametrize('mutation', ['description', 'context', 'accounts', 'transfer'])
def test_stale_accepted_opinion_revalidated_even_off(monkeypatch, client_id, accounts, fake_credential_vault, mutation):
    at, row, calls = fixture(monkeypatch, client_id, accounts, fake_credential_vault)
    ask(at)
    next(b for b in at.button if b.label == 'Accept Anthropic suggestion').click().run()
    at.radio[0].set_value('Upload CSV').run()
    if mutation == 'description': at.session_state['transactions_to_review'][0]['description'] += ' changed'
    if mutation == 'transfer': at.session_state['transactions_to_review'][0]['is_transfer'] = True
    if mutation == 'context':
        from models.client import Client
        client = Client.get_by_id(client_id); client.business_context = 'Changed context'; client.save()
    if mutation == 'accounts':
        from models.account import Account
        a = Account.get_by_id(accounts['expense']); a.name = 'Changed eligible account'; a.save()
    fake_credential_vault['categorization_provider'] = 'off'
    at.radio[0].set_value('Review & Categorize').run()
    assert not at.exception and len(calls) == 1
    assert at.session_state[row_key('cat', row)] is None
    assert not at.session_state[row_key('include', row)]


def test_stale_accept_callback_and_manual_override(monkeypatch, client_id, accounts, fake_credential_vault):
    at, row, calls = fixture(monkeypatch, client_id, accounts, fake_credential_vault)
    ask(at)
    at.session_state['transactions_to_review'][0]['description'] += ' changed'
    next(b for b in at.button if b.label == 'Accept Anthropic suggestion').click().run()
    assert not at.exception and at.session_state[row_key('cat', row)] is None
    at.multiselect(key='bulk_rows').set_value([row['uid']]).run()
    ask(at)
    next(b for b in at.button if b.label == 'Accept Anthropic suggestion').click().run()
    at.selectbox(key=row_key('cat', row)).set_value(accounts['expense']).run()
    at.session_state['transactions_to_review'][0]['description'] += ' changed again'
    at.run()
    assert not at.exception and at.session_state[row_key('cat', row)] == accounts['expense']


def test_failure_and_custom_model_require_explicit_retry(monkeypatch, client_id, accounts, fake_credential_vault):
    at, row, calls = fixture(monkeypatch, client_id, accounts, fake_credential_vault, 'openai')
    def fail(*args): calls.append(args); raise TimeoutError('secret must not surface')
    monkeypatch.setattr(ai, 'send_request', fail)
    at.selectbox(key='ai_review_model_openai').set_value('Other model ID').run()
    assert at.button(key='ai_review_run').disabled
    at.text_input(key='ai_review_custom_openai').set_value('custom-json-model').run()
    assert not any(b.key == 'ai_review_retry' for b in at.button)
    ask(at)
    assert len(calls) == 1
    at.run()
    at.button(key='ai_review_run').click().run()
    assert len(calls) == 1 and not at.exception
    assert any('timed out' in x.value for x in at.warning)
    assert all('secret must not surface' not in x.value for x in at.warning)
    at.button(key='ai_review_retry').click().run()
    assert len(calls) == 2 and not at.exception
    assert not at.session_state[row_key('include', row)]


def test_provider_state_is_scoped_to_book_and_client():
    state = dict(ai_review_results={'private': 1}, ai_review_provider='openai', ai_review_consent=True,
                 ai_review_variants={'private': 2})
    scope_import_state_to_client(state, 1, 'book-a')
    scope_import_state_to_client(state, 1, 'book-b')
    assert not any(k.startswith('ai_review_') for k in state)


def test_openai_key_is_saved_removed_in_fake_vault(monkeypatch, client_id, fake_credential_vault):
    import utils.client_selector as selector
    monkeypatch.setattr(selector, 'render_client_selector', lambda: client_id)
    monkeypatch.setattr(st, 'page_link', lambda *a, **k: None)
    at = AppTest.from_file(page_path('pages/12_Firm_Settings.py'), default_timeout=60).run()
    at.text_input(key='firm_openai_key').set_value('fake-user-key').run()
    next(b for b in at.button if b.label == 'Save OpenAI key').click().run()
    assert not at.exception and fake_credential_vault['openai_api_key'] == 'fake-user-key'
    at.run()
    next(b for b in at.button if b.label == 'Remove OpenAI key').click().run()
    assert 'openai_api_key' not in fake_credential_vault
