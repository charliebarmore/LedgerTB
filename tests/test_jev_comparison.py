import json
from pathlib import Path

import pytest

from models.account import Account
from scripts import compare_jev as comparison

FIXTURE = Path(__file__).parent / 'fixtures/jev_evaluation_v2.json'


def test_frozen_fixture_splits_and_labels():
    fixture, development = comparison.load_fixture(FIXTURE, 'development')
    _, holdout = comparison.load_fixture(FIXTURE, 'holdout')
    assert len(development) == 80 and len(holdout) == 40
    assert not {c['id'] for c in development} & {c['id'] for c in holdout}
    assert not {c['description'] for c in development} & {c['description'] for c in holdout}
    assert {c['kind'] for c in development} == {c['kind'] for c in holdout}
    assert fixture['synthetic'] and fixture['label_policy']['frozen_before_inference']
    assert all(c['rationale'] for c in development + holdout)


def test_metrics_separate_wrong_accounts_from_review_route_mismatches():
    rows = [
        comparison.labeled_row({'id': 'a', 'kind': 'x', 'expected': 'split_required'}, '6100', .99, .1),
        comparison.labeled_row({'id': 'b', 'kind': 'x', 'expected': 'transfer_review'}, 'insufficient_information', .9, .1),
        comparison.labeled_row({'id': 'c', 'kind': 'x', 'expected': '6100'}, 'insufficient_information', .2, .1),
        comparison.labeled_row({'id': 'd', 'kind': 'x', 'expected': '6200'}, None, 0, .1, 'timeout'),
        comparison.labeled_row({'id': 'e', 'kind': 'x', 'expected': '6100'}, '6100', .8, .1),
    ]
    result = comparison.summarize(rows)
    assert result['cases'] == 5 and result['completed'] == 4 and result['correct'] == 1
    assert result['wrong_account_suggestions'] == 1
    assert result['review_route_mismatches'] == 1
    assert result['missed_account_suggestions'] == 1
    assert result['account_suggestion_precision'] == .5
    assert result['confident_errors'] == 2
    assert result['errors'] == 1
    assert comparison.summarize([])['correctness_all_cases'] is None


def _data(n):
    cases = [dict(id=str(i), kind='synthetic', expected='6100', description=f'Synthetic supplies {i}',
                  amount=-1.01, receipt_text='Business printing paper', is_transfer=False,
                  rationale='LABEL_ONLY_NEVER_SEND') for i in range(n)]
    return cases, [comparison.transaction_for(c, 10) for c in cases]


def _response(payload):
    return {'model': 'fake-jev', 'usage': {'input_tokens': 100, 'output_tokens': 10},
            'answers': {k: {'type': 'choice', 'choice': next(iter(q['criteria'])), 'confidence': 1,
                           'probabilities': {c: float(c == next(iter(q['criteria']))) for c in q['criteria']}}
                        for k, q in payload['questions'].items()}}


def test_batching_preserves_evidence_excludes_labels_and_counts_usage_once(accounts, client_id):
    cases, transactions = _data(53)
    chart = Account.get_all(client_id)
    calls = []
    def send(payload, key):
        calls.append(payload)
        assert 'LABEL_ONLY_NEVER_SEND' not in json.dumps(payload)
        assert all(t['receipt_text'] == 'Business printing paper' and t['amount_cents'] == -101
                   for t in payload['state']['transactions'].values())
        return _response(payload)
    result = comparison.evaluate_jev(cases, transactions, chart, client_id, {'business_context': 'Synthetic'},
                                     api_key='fake', transport=send)
    assert [len(c['questions']) for c in calls] == [25, 25, 3]
    assert result['network_requests'] == 3
    assert result['usage'] == {'input_tokens': 300, 'output_tokens': 30}
    assert result['summary']['completed'] == 53


def test_request_limit_prevents_any_cloud_calls(accounts, client_id):
    cases, transactions = _data(26)
    def never(*args):
        pytest.fail('Request limit must be checked before any paid call')
    with pytest.raises(ValueError, match='limit'):
        comparison.evaluate_jev(cases, transactions, Account.get_all(client_id), client_id,
                                {'business_context': 'Synthetic'}, api_key='fake', max_requests=1, transport=never)


def test_contexts_are_never_mixed_in_one_request(accounts, client_id):
    cases, transactions = _data(4)
    for i, case in enumerate(cases):
        case['context'] = 'a' if i % 2 else 'b'
    contexts = []
    def send(payload, key):
        contexts.append(payload['state']['business_context'])
        return _response(payload)
    result = comparison.evaluate_jev(cases, transactions, Account.get_all(client_id), client_id,
                                     {'contexts': {'a': 'Studio A', 'b': 'Studio B'}}, api_key='fake', transport=send)
    assert set(contexts) == {'Studio A', 'Studio B'}
    assert result['network_requests'] == 2


def test_reused_cases_do_not_duplicate_usage_or_suppress_new_batch_error(accounts, client_id):
    cases, transactions = _data(4)
    # First row of second batch was already cached, second is new and fails.
    transactions[2] = transactions[0].copy()
    calls = []
    def send(payload, key):
        calls.append(payload)
        if len(calls) == 2:
            raise TimeoutError('secret-not-logged')
        return _response(payload)
    result = comparison.evaluate_jev(cases, transactions, Account.get_all(client_id), client_id,
                                     {'business_context': 'Synthetic'}, api_key='fake', batch_size=2, transport=send)
    assert result['usage'] == {'input_tokens': 100, 'output_tokens': 10}
    assert result['summary']['errors'] == 1
    assert result['batches'][1]['error']
    assert 'secret-not-logged' not in json.dumps(result)
