"""UI recovery checkpoints and restore detection; no cloud or posting actions."""
import uuid
import streamlit as st

from database import connection as dbconn
from services import book_generation, import_review_drafts as drafts, review_recovery_store as recovery
from utils.client_context import _book_identity
from utils.import_review import scope_import_state_to_client


def window_id(state):
    if '_review_window_id' not in state:
        state['_review_window_id'] = uuid.uuid4().hex
    return state['_review_window_id']


def protect_restored_book():
    """Run before rendering mutation controls, also on every import rerun."""
    state = st.session_state
    path = _book_identity(dbconn.DATABASE_PATH)
    generation = book_generation.current()
    seen = state.setdefault('_book_generations_seen', {})
    if path in seen and seen[path] != generation:
        st.warning('This book was restored. Reload it before continuing; the review and AI suggestions in this window are from the earlier book.')
        st.caption('Saved reviews and recovery copies in the restored book can be resumed and checked against its current history.')
        if st.button('Reload restored book', key='reload_restored_book_' + str(generation)):
            acknowledge_restored_book()
            st.rerun()
        st.stop()
    seen[path] = generation
    return generation


def acknowledge_restored_book():
    state = st.session_state
    scope_import_state_to_client(state, None, book=dbconn.DATABASE_PATH)
    # Page-scoped mutation widgets must get a fresh browser identity.
    for key in list(state):
        if key.startswith('_client_context_owner_'):
            state[key] = ('restored', -1)
        elif key.startswith('_client_navigation_intent_'):
            state.pop(key, None)
    state.setdefault('_book_generations_seen', {})[_book_identity(dbconn.DATABASE_PATH)] = book_generation.current()
    state.pop('_review_generation', None)
    state.pop('_worksheet_export_cache', None)
    publish_close_status()


def checkpoint_active_review():
    """Save changed, completed server-side review state, never explicit copies.

    Call before leaving a page and after installing/reconciling/posting rows.
    A crash during the transaction retains the last complete checkpoint.
    """
    from utils.review_guard import owns_review, review_snapshot, review_is_dirty
    state = st.session_state
    owner = state.get('_import_state_client_id')
    if not owner or not owns_review(state, owner[1]):
        publish_close_status()
        return
    generation = state.get('_review_generation')
    if generation is None:
        generation = book_generation.current()
        state['_review_generation'] = generation
    rows = review_snapshot(state)
    for row in state.get('transactions_to_review', []):
        row.setdefault('_book_generation', generation)
    publish_close_status()
    if dbconn.READ_ONLY or not dbconn.ENCRYPTION_AVAILABLE or generation is None:
        state['_review_recovery_status'] = 'Recovery is unavailable in this read-only or unencrypted book. Keep this window open to retain unsaved changes.'
        return
    try:
        if rows and review_is_dirty(state, owner[1]):
            recovery.save(owner[1], window_id(state), rows, expected_generation=generation,
                          base_revision=state.get('review_saved_revision'))
            state['_review_recovery_status'] = 'Recovery copy updated in this encrypted book. It is separate from Save review for later.'
        else:
            recovery.discard(owner[1], window_id(state), expected_generation=generation)
            state.pop('_review_recovery_status', None)
    except Exception:
        state['_review_recovery_status'] = 'Recovery copy could not be updated. Keep this window open and use Save review for later before closing.'


def discard_window_recovery(client_id):
    if not dbconn.READ_ONLY and st.session_state.get('_review_generation'):
        recovery.discard(client_id, window_id(st.session_state),
                         expected_generation=st.session_state['_review_generation'])


def publish_close_status():
    from utils.desktop_review_status import publish
    publish(window_id(st.session_state), bool(st.session_state.get('transactions_to_review')))


def render_recovery_copies(client_id, duplicate_check):
    copies = recovery.list_copies(client_id, exclude_window=window_id(st.session_state))
    if not copies:
        return
    with st.expander(f'Recovery copies · {len(copies)}'):
        st.caption('Automatic checkpoints from other or interrupted windows. These do not replace your explicitly saved review. Resume rechecks duplicates and never sends information to AI or posts entries.')
        selected = st.selectbox('Recovery copy', options=[c['revision'] for c in copies],
                                format_func=lambda rev: next(f"{c['row_count']} rows · {c['updated_at']} · copy {rev[:6]}" for c in copies if c['revision'] == rev))
        copy = next(c for c in copies if c['revision'] == selected)
        replace = not bool(st.session_state.get('transactions_to_review'))
        if not replace:
            replace = st.checkbox('Replace this window’s review with the recovery copy', key='recovery_replace_' + selected)
        if st.button('Resume recovery copy', disabled=not replace, key='recovery_resume_' + selected):
            try:
                rows, base_revision = recovery.load(client_id, copy['window_id'], selected)
                duplicate_check(rows)
                st.session_state.transactions_to_review = rows
                st.session_state.review_saved_revision = base_revision
                st.session_state.pop('_review_checkpoint', None)
                st.session_state.bulk_rows = []
                st.session_state.review_page = 1
                st.session_state.import_complete = False
                st.session_state.import_complete_msg = None
                checkpoint_active_review()
                st.session_state.review_saved_message = 'Recovery copy resumed. Check the included rows and categories before posting.'
                st.rerun()
            except Exception as exc:
                st.error(str(exc) if isinstance(exc, ValueError) else 'The recovery copy could not be opened. Your current review is unchanged.')
        discard = st.checkbox('Discard this recovery copy', key='recovery_discard_confirm_' + selected)
        if st.button('Discard recovery copy', disabled=not discard or dbconn.READ_ONLY, key='recovery_discard_' + selected):
            try:
                recovery.discard(client_id, copy['window_id'], expected_generation=book_generation.current(), revision=selected)
                st.rerun()
            except Exception as exc:
                st.error(str(exc) if isinstance(exc, ValueError) else 'The recovery copy could not be discarded.')


def conflicting_saved_review(client_id):
    """Require an explicit choice if another window changed the saved baseline."""
    info = drafts.summary(client_id)
    current = info['revision'] if info else None
    baseline = st.session_state.get('review_saved_revision')
    if baseline == current or not st.session_state.get('transactions_to_review'):
        return False
    st.warning('A different saved review is available. Resume that copy, or keep this window’s review before posting or replacing the saved copy.')
    if st.button('Keep this window’s review', key='review_keep_' + str(current)):
        st.session_state.review_saved_revision = current
        st.session_state.pop('_review_checkpoint', None)
        checkpoint_active_review()
        st.rerun()
    return True
