"""Provider choice and independent second opinions in staged review."""
import streamlit as st
from services import jev_categorization as jev
from services import review_categorization as ai
from utils import secure_store
from utils.import_review import row_key
from utils.jev_review import render_jev_review, render_jev_result


def current_config(state):
    default = jev.configured_provider()
    if state.get('ai_review_default') != default:
        state['ai_review_default'] = default
        state['ai_review_picker_epoch'] = state.get('ai_review_picker_epoch', 0) + 1
        state['ai_review_provider'] = state['ai_review_choice'] = default
        state['jev_consent'] = state['ai_review_consent'] = False
    provider = state.setdefault('ai_review_provider', state.get('ai_review_choice', default))
    if provider not in ai.PROVIDERS:
        provider = 'off'
    if provider == 'off':
        return provider, ''
    saved = state.get('ai_review_model_choices', {})
    model = state.setdefault(f'ai_review_model_{provider}', saved.get(provider, ai.MODELS[provider][0]))
    if model == 'Other model ID':
        model = state.setdefault(f'ai_review_custom_{provider}', saved.get(f'{provider}_custom', '')).strip()
    return provider, model


def reset_consent():
    provider = st.session_state.get('ai_review_provider', 'off')
    st.session_state['ai_review_choice'] = provider
    saved = st.session_state.setdefault('ai_review_model_choices', {})
    if provider in ai.MODELS:
        saved[provider] = st.session_state.get(f'ai_review_model_{provider}', ai.MODELS[provider][0])
        saved[f'{provider}_custom'] = st.session_state.get(f'ai_review_custom_{provider}', '')
    st.session_state['jev_consent'] = False
    st.session_state['ai_review_consent'] = False


def prepare_other_reviews(transactions, accounts, client_id, book, context, state, provider, model):
    """Revalidate each accepted opinion with its own provider/model, not the picker."""
    chosen = state.get('bulk_rows', [])
    chosen = set(chosen) if len(chosen) <= jev.MAX_BATCH else set()
    variants = state.get('ai_review_variants', {})
    by_uid = {t['uid']: t for t in transactions}
    inputs, keys = {}, {}
    for uid, row in by_uid.items():
        accepted = row.get('ai_review_accepted')
        needed = [(v['provider'], v['model']) for v in variants.values() if uid in v['rows']]
        if accepted:
            needed.append((accepted['provider'], accepted['model']))
        if uid in chosen and provider in ai.ENDPOINTS and ai.valid_model(provider, model):
            needed.append((provider, model))
        if not needed:
            continue
        evidence = {**row, 'is_transfer': state.get(row_key('xfer', row), row.get('is_transfer', False))}
        inputs[uid] = jev.request_input(evidence, accounts, client_id, context)
        for p, m in set(needed):
            keys[(uid, p, m)] = ai.request_key((str(book), client_id), inputs[uid], p, m)
        if accepted:
            cat_key = row_key('cat', row)
            current = state.get(cat_key, row.get('selected_account_id'))
            if current != accepted['account_id']:
                row.pop('ai_review_accepted', None)
            elif accepted['key'] != keys[(uid, accepted['provider'], accepted['model'])]:
                state[cat_key] = None
                row['selected_account_id'] = 0
                row.pop('ai_review_accepted', None)
    return inputs, keys


def accept_other(row, account_id, key, provider, model):
    st.session_state[row_key('cat', row)] = account_id
    row['selected_account_id'] = account_id
    row.pop('jev_accepted', None)
    row['ai_review_accepted'] = dict(key=key, account_id=account_id, provider=provider, model=model)


def change_provider(widget_key):
    st.session_state['ai_review_provider'] = st.session_state[widget_key]
    reset_consent()


def choose_another():
    current = st.session_state['ai_review_provider']
    providers = ['jev', 'anthropic', 'openai']
    st.session_state['ai_review_provider'] = providers[(providers.index(current) + 1) % len(providers)]
    st.session_state['ai_review_picker_epoch'] += 1
    reset_consent()


def render_controls(transactions, accounts, client_id, jev_prepared, other_prepared):
    provider, model = current_config(st.session_state)
    picker_key = f"ai_review_provider_{st.session_state['ai_review_picker_epoch']}"
    provider_column, model_column = st.columns(2)
    with provider_column:
        st.selectbox('AI provider', list(ai.PROVIDERS), index=list(ai.PROVIDERS).index(provider), format_func=ai.PROVIDERS.get,
                     key=picker_key, on_change=change_provider, args=(picker_key,))
    if provider == 'off':
        st.caption('AI categorization is off. Choose a provider for this request; local review remains available.')
        return
    options = ai.MODELS[provider] + ([] if provider == 'jev' else ['Other model ID'])
    with model_column:
        st.selectbox('Model', options, index=options.index(st.session_state[f'ai_review_model_{provider}']),
                     key=f'ai_review_model_{provider}', on_change=reset_consent)
    if st.session_state[f'ai_review_model_{provider}'] == 'Other model ID':
        st.text_input('Model ID', key=f'ai_review_custom_{provider}', on_change=reset_consent,
                      help='An API model available to your provider account that supports structured JSON output.')
    chosen = st.session_state.get('bulk_rows', [])
    if not chosen:
        st.info('Choose rows in Select rows for actions first. Inclusion checkboxes control posting only.')
    if provider == 'jev':
        render_jev_review(transactions, accounts, client_id, jev_prepared, chosen=chosen, show_results=False)
    else:
        name = ai.PROVIDERS[provider]
        with st.expander(f'What is sent to {name}'):
            st.caption(f'{name} receives the selected dates, descriptions, amounts, source account IDs, transfer flags, '
                       'receipt text if present, eligible account details and AI business context. General client Notes '
                       'and other providers’ answers are not sent. Every suggestion requires your approval.')
        st.caption('Suggestions only. Every account choice needs your approval.')
        valid = ai.valid_model(provider, model)
        if not valid:
            st.info('Enter a valid API model ID before requesting suggestions.')
        too_many = len(chosen) > jev.MAX_BATCH
        if too_many:
            st.info(f'Select at most {jev.MAX_BATCH} rows per AI request. Bulk category changes can use more.')
        consent = st.checkbox(f'Send the selected transaction information to {name}', key='ai_review_consent')
        api_key = secure_store.get_secret(ai.KEY_NAMES[provider])
        if not api_key:
            st.info(f'Add your {name} API key in Firm Settings. Local review remains available.')
        inputs, keys = other_prepared
        requested = {keys[(uid, provider, model)]: inputs[uid] for uid in chosen} if valid and not too_many else {}
        cache = st.session_state.setdefault('ai_review_results', {})
        new = ai.plan_requests({k: v for k, v in requested.items() if k not in cache}, provider, model) if valid else []
        if new:
            st.caption(f'This selection needs {sum(fits for _, fits in new)} new {name} request(s). Existing results are reused.')
        enabled = bool(requested and consent and api_key)
        run = st.button(f'Ask {name} for suggestions', key='ai_review_run', disabled=not enabled)
        has_error = any(cache.get(k, {}).get('error') and cache[k].get('retryable', True) for k in requested)
        retry = False
        if has_error:
            st.caption('Retry may incur another charge, including after a timeout.')
            retry = st.button(f'Retry failed {name} requests', key='ai_review_retry', disabled=not enabled)
        if run or retry:
            variant_id = f'{provider}:{model}'
            variants = st.session_state.setdefault('ai_review_variants', {})
            prior_rows = variants.get(variant_id, {}).get('rows', [])
            variants[variant_id] = dict(provider=provider, model=model, rows=sorted(set(prior_rows) | set(chosen)))
            with st.spinner(f'{name} is reviewing the selected information…'):
                ai.suggest(requested, cache, provider=provider, model=model, api_key=api_key, consent=consent, retry=retry)
            st.rerun()
    if st.session_state.get('jev_results') or st.session_state.get('ai_review_results'):
        st.button('Choose another AI', on_click=choose_another)


def render_results(row, accounts, client_id, jev_prepared, other_prepared):
    uid = row['uid']
    cache = st.session_state.get('ai_review_results', {})
    _, keys = other_prepared
    opinions = []
    jev_result = st.session_state.get('jev_results', {}).get(jev_prepared[1].get(uid))
    if jev_result and not jev_result.get('error'):
        opinions.append((jev_result['outcome'], jev_result['account_id']))
    variants = st.session_state.get('ai_review_variants', {})
    current_results = []
    for v in variants.values():
        key = keys.get((uid, v['provider'], v['model']))
        result = cache.get(key)
        if result:
            current_results.append((v, key, result))
            if not result.get('error'):
                opinions.append((result['outcome'], result['account_id']))
    count = len(current_results) + bool(jev_result)
    if not count:
        return
    disagreement = len(set(opinions)) > 1
    failed = bool(jev_result and jev_result.get('error')) or any(result.get('error') for _, _, result in current_results)
    label = f'AI opinions ({count})' + (' · Disagree' if disagreement else '') + (' · Request failed' if failed else '')
    with st.expander(label):
        if len(set(opinions)) > 1:
            st.warning('AI opinions disagree. Review the evidence before choosing a category.')
        render_jev_result(row, accounts, client_id, jev_prepared)
        names = {a.id: a.display_name() for a in jev.eligible_accounts(accounts, client_id)}
        for variant, key, result in current_results:
            name = ai.PROVIDERS[variant['provider']]
            st.text(f"{name} · {result.get('model', variant['model'])}")
            if result.get('error'):
                st.warning(result['error'] + ' Staged work is unchanged.')
                continue
            if result['outcome'] == 'account':
                st.caption(f"Suggested account: {names[result['account_id']]}")
                st.button(f'Accept {name} suggestion', key=f'ai_review_accept_{uid}_{key}', on_click=accept_other,
                          args=(row, result['account_id'], key, variant['provider'], variant['model']))
            else:
                st.info(jev.OUTCOMES[result['outcome']])
            st.text(result['reason'])
