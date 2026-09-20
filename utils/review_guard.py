"""Explicit protection for unsaved review decisions before a UI transition.

Only the current encrypted book/client can be saved. Approval is consumed by the
caller's action in the same run; it is never a reusable session permission.
"""
from copy import deepcopy

import streamlit as st
from database import connection as dbconn
from services import import_review_drafts as drafts
from utils.client_context import client_context_identity, book_scoped_key
from utils.import_review import row_key
from utils.recovery import save_error_message


def owns_review(state, client_id):
    owner = state.get('_import_state_client_id')
    return bool(owner and client_id is not None and owner[0] is not None and owner[1] is not None
                and client_context_identity(owner[0], owner[1])
                == client_context_identity(dbconn.DATABASE_PATH, client_id))


def review_snapshot(state):
    """Capture edits whose widgets arrived before the page's row reconciliation."""
    rows = deepcopy(state.get('transactions_to_review', []))
    for row in rows:
        previous = row.get('selected_account_id', row.get('suggested_account_id')) or 0
        was_transfer = bool(row.get('is_transfer', False))
        if row.get('uid'):
            row['include'] = bool(state.get(row_key('include', row), row.get('include', True)))
            row['is_transfer'] = bool(state.get(row_key('xfer', row), was_transfer))
            row['selected_account_id'] = state.get(row_key('cat', row), previous) or 0
        else:
            row.setdefault('include', True)
            row.setdefault('is_transfer', False)
            row['selected_account_id'] = previous
        if row['selected_account_id'] != previous or row['is_transfer'] != was_transfer:
            row.pop('jev_accepted', None)
            row.pop('ai_review_accepted', None)
    return rows


def mark_saved(state, client_id, rows, revision):
    state['review_saved_revision'] = revision
    state['_review_checkpoint'] = {
        'owner': client_context_identity(dbconn.DATABASE_PATH, client_id),
        'revision': revision, 'fingerprint': drafts.content_fingerprint(rows),
    }


def review_is_dirty(state, client_id):
    if not owns_review(state, client_id) or not state.get('transactions_to_review'):
        return False
    checkpoint = state.get('_review_checkpoint', {})
    if checkpoint.get('owner') != client_context_identity(dbconn.DATABASE_PATH, client_id):
        return True
    current = drafts.summary(client_id)
    if not current or current['revision'] != checkpoint.get('revision'):
        return True
    try:
        return drafts.content_fingerprint(review_snapshot(state)) != checkpoint['fingerprint']
    except (ValueError, TypeError):
        return True  # Invalid edits still belong to the user; never discard them.


def save_current_review(client_id):
    if not owns_review(st.session_state, client_id):
        raise ValueError('This review belongs to another book or client. Return to it before saving.')
    rows = review_snapshot(st.session_state)
    revision = drafts.save(client_id, rows,
                           expected_revision=st.session_state.get('review_saved_revision'),
                           expected_generation=st.session_state.get('_review_generation'))
    st.session_state.transactions_to_review = rows
    mark_saved(st.session_state, client_id, rows, revision)
    from utils.review_lifecycle import checkpoint_active_review
    checkpoint_active_review()
    return revision


def confirm_transition(client_id, action, label):
    """Return continue/cancel/pending. Call only for a requested action.

    The caller keeps its pending target and old context until continue. Save
    errors and revision conflicts return pending, so no destructive action runs.
    """
    if not review_is_dirty(st.session_state, client_id):
        return 'continue'
    # A pending guard stops the normal page before its widgets render. Keep
    # their decisions in rows before Streamlit cleans up unrendered widgets.
    st.session_state.transactions_to_review = review_snapshot(st.session_state)
    st.warning(f'Your transaction review has unsaved changes. Save them before {label}?')
    st.caption('Discard changes leaves any previously saved copy in the book.')
    if dbconn.READ_ONLY:
        st.caption('This book is read-only. Saving is unavailable.')
    key = book_scoped_key(f'review_guard_{client_id}_{action}', dbconn.DATABASE_PATH)
    with st.container(horizontal=True):
        save = st.button('Save and continue', key=key + '_save', type='primary', disabled=dbconn.READ_ONLY)
        discard = st.button('Discard changes', key=key + '_discard')
        cancel = st.button('Cancel', key=key + '_cancel')
    if cancel:
        return 'cancel'
    if discard:
        try:
            from utils.review_lifecycle import discard_window_recovery
            discard_window_recovery(client_id)
            return 'continue'
        except Exception as exc:
            st.error(save_error_message(exc))
            return 'pending'
    if save:
        try:
            save_current_review(client_id)
            return 'continue'
        except Exception as exc:
            st.error(str(exc) if isinstance(exc, (drafts.ReviewConflict, ValueError))
                     else save_error_message(exc))
    return 'pending'


def queue_replacement(client_id, rows, duplicates=0):
    """Keep a fully parsed candidate separate until the old review is resolved."""
    from utils.import_review import ensure_row_ids
    st.session_state['_review_replacement'] = {
        'owner': client_context_identity(dbconn.DATABASE_PATH, client_id),
        'rows': ensure_row_ids(rows), 'duplicates': duplicates,
    }
    st.rerun()


def render_pending_replacement(client_id):
    pending = st.session_state.get('_review_replacement')
    if not pending:
        return
    if pending['owner'] != client_context_identity(dbconn.DATABASE_PATH, client_id):
        st.session_state.pop('_review_replacement', None)
        return
    decision = confirm_transition(client_id, 'replace_import',
                                  f"replacing it with {len(pending['rows'])} imported transactions")
    if decision == 'cancel':
        st.session_state.pop('_review_replacement', None)
        st.rerun()
    if decision == 'continue':
        rows = pending['rows']
        st.session_state.transactions_to_review = rows
        st.session_state.bulk_rows = []
        st.session_state.review_page = 1
        for row in rows:
            if row.get('suggested_account_id'):
                st.session_state[row_key('cat', row)] = row['suggested_account_id']
        st.session_state.import_complete = False
        st.session_state.import_complete_msg = None
        st.session_state.import_active_tab = 'Review & Categorize'
        st.session_state.pop('_review_replacement', None)
        if pending['duplicates']:
            st.session_state.post_result = {
                'level': 'warning',
                'text': f"{pending['duplicates']} potential duplicate(s) were auto-deselected.",
            }
        st.rerun()
    st.stop()
