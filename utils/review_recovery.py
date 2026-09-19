"""Explicit saved-review controls. No cloud requests or posting side effects."""

import streamlit as st
from database import connection as dbconn
from services import import_review_drafts as drafts
from utils.recovery import save_error_message
from utils.client_context import book_scoped_key
from utils.review_guard import mark_saved, review_is_dirty, save_current_review


def _control_key(client_id, action, revision=""):
    # A confirmation belongs to the displayed copy, not a later saved revision.
    return book_scoped_key(
        f"review_recovery_{client_id}_{action}_{revision}", dbconn.DATABASE_PATH
    )


def render_saved_review(client_id, duplicate_check):
    info = drafts.summary(client_id)
    message = st.session_state.pop("review_saved_message", None)
    if message:
        st.success(message)
    if not info:
        return
    with st.expander(
        f"Saved review · {info['row_count']} row{'s' if info['row_count'] != 1 else ''} · {info['saved_at'].replace('T', ' ')}"
    ):
        st.caption(
            "This is the last copy you saved in this encrypted book. Later edits and posting do not update it. Resuming checks current import history again; it never sends information to AI."
        )
        has_rows = bool(st.session_state.get("transactions_to_review"))
        replace = (
            st.checkbox(
                "Replace the review currently in this window",
                key=_control_key(client_id, "replace", info["revision"]),
            )
            if has_rows
            else True
        )
        if st.button(
            "Resume saved review", disabled=not replace,
            key=_control_key(client_id, "resume", info["revision"]),
        ):
            try:
                loaded = drafts.load(client_id)
                if not loaded:
                    raise drafts.ReviewConflict(
                        "The saved copy was removed in another window."
                    )
                revision, rows = loaded
                mark_saved(st.session_state, client_id, rows, revision)
                duplicate_check(rows)
                st.session_state.transactions_to_review = rows
                st.session_state.review_saved_revision = revision
                st.session_state.import_complete = False
                st.session_state.import_complete_msg = None
                st.session_state.bulk_rows = []
                st.session_state.review_saved_message = (
                    "Saved review resumed. Check the included rows before posting."
                )
                st.rerun()
            except Exception as exc:
                st.error(
                    str(exc)
                    if isinstance(exc, drafts.ReviewConflict)
                    else save_error_message(exc)
                )
        confirm = st.checkbox(
            "Discard the saved copy",
            key=_control_key(client_id, "discard_confirm", info["revision"]),
        )
        if st.button(
            "Discard saved review",
            disabled=dbconn.READ_ONLY or not confirm,
            key=_control_key(client_id, "discard", info["revision"]),
        ):
            try:
                drafts.discard(client_id, info["revision"])
                st.session_state.pop("review_saved_revision", None)
                st.session_state.pop("_review_checkpoint", None)
                st.session_state.review_saved_message = (
                    "Saved copy discarded. The current review is unchanged."
                )
                st.rerun()
            except Exception as exc:
                st.error(
                    str(exc)
                    if isinstance(exc, drafts.ReviewConflict)
                    else save_error_message(exc)
                )


def render_save_review(client_id, rows):
    if review_is_dirty(st.session_state, client_id):
        st.caption("Unsaved changes · Save this review before closing the app.")
    else:
        st.caption("Saved in this encrypted book. Duplicate overrides are checked again on resume.")
    if st.button(
        "Save review for later", disabled=dbconn.READ_ONLY,
        key=_control_key(client_id, "save"),
    ):
        try:
            save_current_review(client_id)
            st.session_state.review_saved_message = (
                "Review saved in this encrypted book. No transactions were posted."
            )
            st.rerun()
        except Exception as exc:
            st.error(
                str(exc)
                if isinstance(exc, drafts.ReviewConflict)
                else save_error_message(exc)
            )
