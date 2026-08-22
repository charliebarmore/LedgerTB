"""One place to see everything the assistant has done — and sign off on it.

Per-surface review already exists (Drafts, staged imports). This page is the
oversight layer above them: what still needs a human decision, what the
assistant did since the last sign-off, and an append-only "reviewed through
here" checkpoint that is itself audit-logged.
"""
import streamlit as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import init_database
from database import connection as dbconn
from models import assistant_review
from models.draft_entry import DraftEntry
from models.transaction import ImportedTransaction
from services.branding import pending_client_branding_count
from utils.client_context import set_client_intent
from utils.client_selector import render_client_selector
from utils.unlock import require_unlock
from utils import icons

st.set_page_config(page_title="Assistant Review", page_icon=icons.ASSISTANT,
                   layout="wide")
require_unlock()
init_database()

client_id = render_client_selector()

st.title("Assistant Review")

if not client_id:
    st.warning("Please select or create a client first.")
    st.stop()

st.caption(
    "Everything the assistant does is stamped \"(AI)\" in the audit trail. "
    "This page gathers it: proposals waiting on you, and completed assistant "
    "actions since your last sign-off."
)

# ---------------------------------------------------------------- needs action
st.subheader("Waiting on you")
pending_drafts = DraftEntry.pending_count(client_id)
pending_staged = len(ImportedTransaction.get_by_status(client_id, "Pending"))
pending_branding = pending_client_branding_count(client_id)

na1, na2, na3, na4 = st.columns([1, 1, 1, 1])
with na1:
    st.metric("Draft entries", pending_drafts)
    if pending_drafts and st.button("Review drafts →", key="ar_goto_drafts"):
        set_client_intent(
            st.session_state, "journal", {"view": "Drafts"},
            client_id, dbconn.DATABASE_PATH,
        )
        st.switch_page("pages/2_Journal_Entries.py")
with na2:
    st.metric("Staged imports", pending_staged)
    if pending_staged and st.button("Review staged →", key="ar_goto_staged"):
        st.session_state.import_active_tab = "Review & Categorize"
        st.switch_page("pages/4_Import_Transactions.py")
with na3:
    st.metric("Branding proposals", pending_branding)
    if pending_branding and st.button("Review branding →", key="ar_goto_branding"):
        st.switch_page("pages/12_Firm_Settings.py")
with na4:
    if not pending_drafts and not pending_staged and not pending_branding:
        st.success("Nothing is waiting for a decision.")

st.divider()

# ---------------------------------------------------------------- activity log
st.subheader("Assistant activity since your last sign-off")
mark = assistant_review.latest_mark(client_id)
if mark:
    st.caption(f"Last reviewed by {mark['reviewed_by']} on "
               f"{mark['reviewed_at']} (through audit #{mark['through_audit_id']}).")
else:
    st.caption("Never reviewed — everything the assistant has ever done for "
               "this client is listed below.")

_LABELS = {
    ("INSERT", "journal_entries"): "Posted journal entry",
    ("INSERT", "draft_entries"): "Filed draft",
    ("INSERT", "imported_transactions"): "Staged import row",
    ("INSERT", "accounts"): "Created account",
    ("INSERT", "clients"): "Created client",
    ("INSERT", "client_branding_proposals"): "Proposed client branding",
    ("EXPORT", "audit_log"): "Exported files",
}

unreviewed_total = assistant_review.unreviewed_count(client_id)
actions = assistant_review.unreviewed_actions(client_id)
if not actions:
    st.info("No unreviewed assistant activity.")
else:
    if unreviewed_total > len(actions):
        st.info(
            f"Showing the oldest {len(actions)} of {unreviewed_total} "
            "unreviewed actions. Signing off below covers only the actions "
            "shown on this page; the rest will remain for your next review."
        )
    for a in actions:
        label = _LABELS.get((a.action, a.table_name),
                            f"{a.action.title()} · {a.table_name}")
        row1, row2 = st.columns([4, 1])
        with row1:
            st.markdown(f"**{label}** #{a.record_id} · {a.changed_at} · "
                        f"{a.actor}")
        with row2:
            if a.table_name == "journal_entries" and a.action == "INSERT":
                if st.button("Open entry", key=f"ar_open_{a.audit_id}"):
                    set_client_intent(
                        st.session_state,
                        "journal",
                        {"entry_id": a.record_id, "view": "New Entry"},
                        client_id,
                        dbconn.DATABASE_PATH,
                    )
                    st.switch_page("pages/2_Journal_Entries.py")

    st.divider()
    st.caption(
        "Signing off records an append-only checkpoint in the audit trail — "
        "it does not change any entry. Pending drafts and staged imports "
        "above still need their own decisions."
    )
    action_word = "action" if len(actions) == 1 else "actions"
    if st.button(
        f"Mark these {len(actions)} displayed {action_word} reviewed",
        type="primary",
    ):
        try:
            through = assistant_review.mark_reviewed(
                client_id, actions[-1].audit_id
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state.ar_marked_msg = (
                f"Reviewed through audit #{through}.")
            st.rerun()

_marked = st.session_state.pop("ar_marked_msg", None)
if _marked:
    st.success(_marked)
