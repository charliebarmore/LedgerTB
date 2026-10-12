"""Provider contracts use fake transport and disposable encrypted books only."""
import copy
import json
import urllib.error

import pytest
from services import review_categorization as ai
from services import jev_categorization as jev
from tests.test_jev_categorization import inputs


def response(provider, payload, choice=None):
    state = json.loads(payload['input'][0]['content'] if provider == 'openai' else payload['messages'][-1]['content'])
    text = json.dumps({'suggestions': [dict(request_id=k, choice=choice or next(iter(state['choices'])),
                                           reason='Fictional receipt identifies office paper.') for k in state['transactions']]})
    if provider == 'local':
        return dict(model=payload['model'], done=True, done_reason='stop', prompt_eval_count=100, eval_count=20,
                    message=dict(role='assistant', content=text))
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


def test_local_model_needs_no_key_and_never_writes(accounts, client_id):
    from database.connection import get_connection
    data = inputs(accounts, client_id)
    cache, calls = {}, []
    def send(p, payload, key):
        calls.append((p, payload, key))
        return response(p, payload, f"account_{accounts['expense']}")
    kwargs = dict(provider='local', model=ai.MODELS['local'][0], api_key='', transport=send)
    ai.suggest({'row': data}, cache, consent=False, **kwargs)
    assert not calls and not cache
    ai.suggest({'row': data}, cache, consent=True, **kwargs)
    (provider, payload, key), = calls
    assert provider == 'local' and key == ''
    assert 'PRIVATE' not in json.dumps(payload)
    assert payload['stream'] is False and payload['think'] is False
    assert payload['options'] == dict(temperature=0, num_ctx=ai.LOCAL_CONTEXT_TOKENS)
    assert payload['messages'][0] == dict(role='system', content=ai.REVIEW_INSTRUCTIONS)
    rows = payload['format']['properties']['suggestions']['items']['properties']
    assert rows['request_id']['enum'] == ['row'] and f"account_{accounts['expense']}" in rows['choice']['enum']
    assert cache['row']['outcome'] == 'account' and cache['row']['requires_review']
    assert cache['row']['provider'] == 'local' and cache['row']['model'] == ai.MODELS['local'][0]
    assert cache['row']['batch_usage'] == dict(input_tokens=100, output_tokens=20)
    with get_connection() as conn:
        assert conn.execute('SELECT count(*) FROM journal_entries').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM imported_transactions').fetchone()[0] == 0


@pytest.mark.parametrize('bad', ['account', 'missing', 'duplicate', 'unknown_row', 'long_reason',
                                 'cut_short', 'not_done', 'truncated_prompt', 'no_message'])
def test_local_invalid_response_rejects_whole_batch(bad, accounts, client_id):
    batch = {'row': inputs(accounts, client_id)}
    result = response('local', ai.request_payload(batch, 'local', 'fictional-model:1b'))
    parsed = json.loads(result['message']['content'])
    if bad == 'account': parsed['suggestions'][0]['choice'] = 'account_999999'
    if bad == 'missing': parsed['suggestions'] = []
    if bad == 'duplicate': parsed['suggestions'] *= 2
    if bad == 'unknown_row': parsed['suggestions'][0]['request_id'] = 'invented'
    if bad == 'long_reason': parsed['suggestions'][0]['reason'] = 'x' * 801
    result['message']['content'] = json.dumps(parsed)
    if bad == 'cut_short': result['done_reason'] = 'length'
    if bad == 'not_done': result['done'] = False
    if bad == 'truncated_prompt': result['prompt_eval_count'] = ai.LOCAL_CONTEXT_TOKENS
    if bad == 'no_message': del result['message']
    with pytest.raises(ValueError): ai.validate_response(result, batch, 'local')


@pytest.mark.parametrize('given,saved', [
    ('http://127.0.0.1:11434', 'http://127.0.0.1:11434'),
    ('  http://localhost:11434/  ', 'http://localhost:11434'),
    ('http://192.168.1.20:11434', 'http://192.168.1.20:11434'),
    ('http://10.0.0.5:8080', 'http://10.0.0.5:8080'),
    ('http://100.64.0.7:11434', 'http://100.64.0.7:11434'),
    ('http://[::1]:11434', 'http://[::1]:11434'),
    ('https://models.example.test', 'https://models.example.test'),
])
def test_local_address_accepts_this_computer_private_networks_and_https(given, saved):
    assert ai.normalize_local_url(given) == saved


@pytest.mark.parametrize('given', [
    '', 'not an address', 'ftp://127.0.0.1:11434', 'http://models.example.test:11434',
    'http://203.0.113.9:11434', 'http://fictional:secret@127.0.0.1:11434',
    'http://127.0.0.1:11434/api/chat', 'http://127.0.0.1:11434?x=1', 'http://127.0.0.1:notaport',
])
def test_local_address_rejects_unencrypted_public_and_malformed(given):
    with pytest.raises(ValueError): ai.normalize_local_url(given)


def test_local_address_setting_scopes_the_cache(accounts, client_id, fake_credential_vault):
    data = inputs(accounts, client_id)
    def key(**extra): return ai.request_key(('book', client_id), data, 'local', 'fictional-model:1b', **extra)
    assert ai.local_url() == ai.DEFAULT_LOCAL_URL and ai.endpoint('local') == 'http://127.0.0.1:11434/api/chat'
    original = key()
    fake_credential_vault[ai.LOCAL_URL_NAME] = 'http://192.168.1.20:11434'
    assert ai.endpoint('local') == 'http://192.168.1.20:11434/api/chat'
    assert key() != original and key(local_endpoint='http://127.0.0.1:11434/api/chat') == original
    fake_credential_vault[ai.LOCAL_URL_NAME] = 'http://203.0.113.9:11434'  # a tampered or stale setting
    assert ai.local_url() == ai.DEFAULT_LOCAL_URL


def test_local_transport_goes_straight_to_saved_address(monkeypatch, fake_credential_vault):
    fake_credential_vault[ai.LOCAL_URL_NAME] = 'http://192.168.1.20:11434'
    seen = []
    class Reply:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit): return b'{"ok":true}'
    class Opener:
        def open(self, request, timeout):
            seen.append((request, timeout))
            return Reply()
    def build(*handlers):
        assert handlers[0] is jev._NoRedirect
        proxies = [h for h in handlers if isinstance(h, ai.urllib.request.ProxyHandler)]
        assert len(proxies) == 1 and proxies[0].proxies == {}
        return Opener()
    monkeypatch.setattr(ai.urllib.request, 'build_opener', build)
    assert ai.send_request('local', {'model': 'fictional-model:1b'}, '') == {'ok': True}
    (request, timeout), = seen
    assert request.full_url == 'http://192.168.1.20:11434/api/chat' and timeout == ai.LOCAL_TIMEOUT
    headers = dict((k.lower(), v) for k, v in request.header_items())
    assert 'authorization' not in headers and 'x-api-key' not in headers


def test_local_errors_name_the_server_and_never_a_charge(accounts, client_id):
    batch = {'row': inputs(accounts, client_id)}
    kwargs = dict(provider='local', model='fictional-model:1b', api_key='', consent=True)
    def run(exc):
        cache = {}
        def fail(*args): raise exc
        ai.suggest(batch, cache, **kwargs, transport=fail)
        return cache['row']['error']
    missing = urllib.error.HTTPError('http://127.0.0.1:11434/api/chat', 404, 'not found', {}, None)
    assert 'does not have that model' in run(missing)
    assert 'could not be reached' in run(urllib.error.URLError('refused'))
    assert 'invalid or incomplete' in run(ValueError('bad'))
    cache = {}
    def interrupted(*args): raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        ai.suggest(batch, cache, **kwargs, transport=interrupted)
    assert 'interrupted' in cache['row']['error'] and 'charge' not in cache['row']['error']


def test_local_requests_stay_inside_the_model_context(accounts, client_id):
    data = inputs(accounts, client_id)
    data['transaction']['receipt_text'] = 'x' * 30000
    assert [fits for _, fits in ai.plan_requests({'row': data}, 'openai', 'gpt-4o-mini')] == [True]
    assert [fits for _, fits in ai.plan_requests({'row': data}, 'local', 'fictional-model:1b')] == [False]
    cache, calls = {}, []
    ai.suggest({'row': data}, cache, provider='local', model='fictional-model:1b', api_key='', consent=True,
               transport=lambda *a: calls.append(a))
    assert not calls and cache['row']['retryable'] is False
