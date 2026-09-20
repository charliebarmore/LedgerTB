"""Provider contracts use fake transport and disposable encrypted books only."""
import copy
import json
import urllib.error

import pytest
from services import review_categorization as ai
from services import jev_categorization as jev
from tests.test_jev_categorization import inputs


def response(provider, payload, choice=None):
    state = json.loads(payload['input'][0]['content'] if provider == 'openai' else payload['messages'][0]['content'])
    text = json.dumps({'suggestions': [dict(request_id=k, choice=choice or next(iter(state['choices'])),
                                           reason='Fictional receipt identifies office paper.') for k in state['transactions']]})
    common = dict(model=payload['model'], usage=dict(input_tokens=100, output_tokens=20))
    if provider == 'openai':
        return dict(common, status='completed', output=[dict(type='message', content=[dict(type='output_text', text=text)])])
    return dict(common, stop_reason='end_turn', content=[dict(type='text', text=text)])


@pytest.mark.parametrize('provider', ['anthropic', 'openai'])
@pytest.mark.parametrize('outcome', ['account', *jev.OUTCOMES])
def test_proposals_are_allowlisted_and_never_write(provider, outcome, accounts, client_id):
    from database.connection import get_connection
    data = inputs(accounts, client_id)
    choice = f"account_{accounts['expense']}" if outcome == 'account' else outcome
    cache, calls = {}, []
    def send(p, payload, key):
        calls.append(payload)
        assert 'PRIVATE' not in json.dumps(payload)
        if p == 'openai':
            assert payload['store'] is False and payload['text']['format']['strict'] is True
        else:
            assert payload['output_config']['format']['type'] == 'json_schema'
        return response(p, payload, choice)
    ai.suggest({'row': data}, cache, provider=provider, model=ai.MODELS[provider][0], api_key='fake', consent=True, transport=send)
    assert len(calls) == 1
    assert cache['row']['outcome'] == outcome and cache['row']['requires_review']
    assert 'confidence' not in cache['row']
    assert cache['row']['batch_usage'] == dict(input_tokens=100, output_tokens=20)
    with get_connection() as conn:
        assert conn.execute('SELECT count(*) FROM journal_entries').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM imported_transactions').fetchone()[0] == 0


@pytest.mark.parametrize('provider', ['anthropic', 'openai'])
@pytest.mark.parametrize('bad', ['account', 'missing', 'duplicate', 'unknown_row', 'long_reason', 'incomplete', 'refusal'])
def test_invalid_response_rejects_whole_batch(provider, bad, accounts, client_id):
    batch = {'row': inputs(accounts, client_id)}
    payload = ai.request_payload(batch, provider, ai.MODELS[provider][0])
    result = response(provider, payload)
    block = result['output'][0]['content'][0] if provider == 'openai' else result['content'][0]
    parsed = json.loads(block['text'])
    if bad == 'account': parsed['suggestions'][0]['choice'] = 'account_999999'
    if bad == 'missing': parsed['suggestions'] = []
    if bad == 'duplicate': parsed['suggestions'] *= 2
    if bad == 'unknown_row': parsed['suggestions'][0]['request_id'] = 'invented'
    if bad == 'long_reason': parsed['suggestions'][0]['reason'] = 'x' * 801
    if bad == 'incomplete': result['status' if provider == 'openai' else 'stop_reason'] = 'incomplete'
    if bad == 'refusal': block['type'] = 'refusal'
    block['text'] = json.dumps(parsed)
    with pytest.raises(ValueError): ai.validate_response(result, batch, provider)


def test_cache_scope_inputs_model_and_instructions(accounts, client_id, monkeypatch):
    data = inputs(accounts, client_id)
    def key(d=data, scope=('book', client_id), model='gpt-4o-mini', provider='openai'):
        return ai.request_key(scope, d, provider, model)
    original = key()
    assert key(scope=('other-book', client_id)) != original
    assert key(scope=('book', client_id + 1)) != original
    assert key(model='other-model') != original
    assert key(provider='anthropic') != original
    for field in ['date', 'description', 'amount_cents', 'bank_account_id', 'is_transfer', 'receipt_text']:
        changed = copy.deepcopy(data)
        changed['transaction'][field] = 'changed'
        assert key(changed) != original
    for field in ['eligible_accounts', 'business_context']:
        changed = copy.deepcopy(data)
        changed[field] = [] if field == 'eligible_accounts' else 'changed'
        assert key(changed) != original
    monkeypatch.setattr(ai, 'REVIEW_INSTRUCTIONS', ai.REVIEW_INSTRUCTIONS + ' Changed instructions')
    assert key() != original


@pytest.mark.parametrize('provider', ['anthropic', 'openai'])
def test_explicit_retry_only_and_no_secret_in_errors(provider, accounts, client_id):
    batch, cache, calls = {'row': inputs(accounts, client_id)}, {}, []
    kwargs = dict(provider=provider, model=ai.MODELS[provider][0], api_key='fake-secret', consent=True)
    def fail(*args):
        calls.append(1)
        raise urllib.error.HTTPError('https://example.invalid', 429, 'fake-secret', {}, None)
    ai.suggest(batch, cache, **{**kwargs, 'consent': False}, transport=fail)
    assert not calls and not cache
    ai.suggest(batch, cache, **{**kwargs, 'api_key': ''}, transport=fail)
    assert not calls
    ai.suggest(batch, cache, **kwargs, retry=True, transport=fail)
    ai.suggest(batch, cache, **kwargs, transport=fail)
    assert len(calls) == 1 and 'rate limited' in cache['row']['error']
    assert 'fake-secret' not in str(cache)
    ai.suggest(batch, cache, **kwargs, retry=True, transport=lambda p,b,k: response(p,b))
    assert 'error' not in cache['row']
    ai.suggest(batch, cache, **kwargs, retry=True, transport=fail)
    assert len(calls) == 1


def test_oversize_evidence_and_interruption_not_automatically_repeated(accounts, client_id):
    data = inputs(accounts, client_id)
    data['transaction']['receipt_text'] = 'x' * 70000
    cache, calls = {}, []
    kwargs = dict(provider='openai', model='gpt-4o-mini', api_key='fake', consent=True)
    ai.suggest({'row': data}, cache, **kwargs, transport=lambda *a: calls.append(a))
    assert not calls and cache['row']['retryable'] is False
    def interrupted(*args): raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        ai.suggest({'second': inputs(accounts, client_id)}, cache, **kwargs, transport=interrupted)
    ai.suggest({'second': inputs(accounts, client_id)}, cache, **kwargs, transport=lambda *a: calls.append(a))
    assert not calls and 'interrupted' in cache['second']['error']


@pytest.mark.parametrize('provider', ['anthropic', 'openai'])
def test_transport_fixed_host_tls_headers_and_response_limit(monkeypatch, provider):
    seen = []
    class Reply:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit):
            assert limit == 2_000_001
            return b'{"ok":true}'
    class Opener:
        def open(self, request, timeout):
            seen.append(request)
            assert timeout == 30
            return Reply()
    def build(*handlers):
        assert handlers[0] is jev._NoRedirect
        assert isinstance(handlers[1], ai.urllib.request.HTTPSHandler)
        return Opener()
    monkeypatch.setattr(ai.urllib.request, 'build_opener', build)
    assert ai.send_request(provider, {'model': 'fictional'}, 'fake') == {'ok': True}
    assert seen[0].full_url == ai.ENDPOINTS[provider]
    assert seen[0].get_method() == 'POST'
    headers = dict((k.lower(), v) for k,v in seen[0].header_items())
    assert headers.get('authorization') == ('Bearer fake' if provider == 'openai' else None)
    assert headers.get('x-api-key') == ('fake' if provider == 'anthropic' else None)
    monkeypatch.setattr(Reply, 'read', lambda self,n: b'x' * n)
    with pytest.raises(ValueError): ai.send_request(provider, {}, 'fake')


def test_chunk_failure_stops_unsent_requests(monkeypatch, accounts, client_id):
    data = inputs(accounts, client_id)
    monkeypatch.setattr(jev, 'MAX_REQUEST_BYTES', len(json.dumps(ai.request_payload({'a': data}, 'openai', 'gpt-4o-mini')).encode()) + 20)
    calls, cache = [], {}
    def fail(*args): calls.append(1); raise TimeoutError()
    batch = {'a': data, 'b': copy.deepcopy(data)}
    assert len(ai.plan_requests(batch, 'openai', 'gpt-4o-mini')) == 2
    ai.suggest(batch, cache, provider='openai', model='gpt-4o-mini', api_key='fake', consent=True, transport=fail)
    assert len(calls) == 1 and 'Not sent' in cache['b']['error']
