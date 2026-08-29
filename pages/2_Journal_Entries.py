import streamlit as st
import sys
from pathlib import Path
from datetime import date

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.account import Account
from models.client import Client
from models.draft_entry import DraftEntry
from models.journal_entry import JournalEntry, JournalEntryLine
from models.recurring_entry import JournalEntryTemplate, TemplateLine
from models.transaction import ImportedTransaction
from services.import_corrections import correct_imported_category
from services.preferences import get_date_format
from services.recurring_entries import recurring_draft_contexts
from database import init_database
from database import connection as dbconn
from utils.client_context import (
    pop_client_intent,
    scope_page_to_client,
    set_client_intent,
)
from utils.client_selector import render_client_selector
from utils.unlock import require_unlock
from utils import icons
from constants import EntryType
from utils.fiscal_dates import fiscal_year_bounds
from utils.dates import display_date
from utils.ui import view_switcher
from utils.recurring_ui import render_recurring_view
from money import to_cents, to_dollars


_FORM_WIDGET_PREFIXES = (
    "account_", "debit_", "credit_", "memo_", "je_hdr_",
    "je_new_template_name_",
)


def _empty_je_lines():
    return [
        {'account_id': 0, 'debit': 0.0, 'credit': 0.0, 'memo': ''},
        {'account_id': 0, 'debit': 0.0, 'credit': 0.0, 'memo': ''},
    ]


def start_new_form_generation():
    """Give every entry-form widget a fresh key so the browser abandons its state.

    Deleting a keyed widget's session-state entry resets only the server side:
    the browser keeps the widget's value and re-imposes it when a widget with
    the same key re-registers on the next run (which is why a saved entry's
    lines reappeared in the cleared form). Embedding a generation counter in
    every key changes the widgets' identity instead — the only reset the
    frontend honors. Stale keys are pruned on the next run, before any
    widget is instantiated.
    """
    st.session_state.je_form_gen = st.session_state.get("je_form_gen", 0) + 1
    st.session_state._prune_je_form_widgets = True


def line_key(name: str, i: int) -> str:
    return f"{name}_{i}_g{st.session_state.je_form_gen}"


def hdr_key(name: str) -> str:
    return f"je_hdr_{name}_g{st.session_state.je_form_gen}"


# Initialize database

st.set_page_config(page_title="Journal Entries", page_icon=icons.JOURNAL_ENTRIES, layout="wide")

# Client selector in sidebar
# Gate on the database passphrase before any DB access, then ensure schema.
require_unlock()
init_database()
date_format = get_date_format()

client_id = render_client_selector()

st.title("Journal Entries")

# Quick link to Trial Balance Worksheet
st.page_link("pages/1_Trial_Balance_Worksheet.py", label="Back to Trial Balance Worksheet", icon=icons.TRIAL_BALANCE)

if not client_id:
    st.warning("Please create a client first in the Clients page.")
    st.page_link("pages/0_Clients.py", label="Go to Clients →")
    st.stop()

journal_scope = scope_page_to_client(
    st.session_state, "journal_entries", client_id, dbconn.DATABASE_PATH
)
if journal_scope.changed:
    # Unsaved lines and record ids are client-owned.  Rotate the entry form's
    # widget generation as well: clearing only Python session state lets the
    # browser re-impose the previous client's values on the next render.
    start_new_form_generation()
    st.session_state.je_lines = _empty_je_lines()
    st.session_state.editing_entry_id = None
    st.session_state.journal_active_tab = "New Entry"
    st.session_state.pop("_journal_active_tab_rendered", None)
    for key in (
        "edit_entry_id",
        "correct_import_entry_id",
        "confirm_delete_entry_id",
        "je_entry_date",
        "je_entry_type",
        "je_source_reference",
        "je_description",
        "je_aje_reference",
        "je_saved_message",
        "import_correction_success",
        "search_entry_id",
        "filter_start",
        "filter_end",
        "filter_type",
        "filter_search",
        "filter_account",
        "journal_filter_signature",
        "journal_page",
        "reversal_entry_id",
        "reversal_date",
        "reversal_result",
        "draft_result",
        "je_template_loaded_message",
        "je_template_name_gen",
        "je_use_template",
        "recurring_through_date",
        "recurring_result",
        "recurring_errors",
        "recurring_editor_id",
        "recurring_editor_name",
        "recurring_editor_description",
        "recurring_editor_reference",
        "recurring_editor_type",
        "recurring_editor_rows",
        "recurring_confirm_archive",
        "journal_return_report",
    ):
        st.session_state.pop(key, None)
    for key in list(st.session_state):
        if key != "recurring_widget_gen" and key.startswith((
            "correction_target_",
            "correction_date_",
            "correction_reason_",
            "post_correction_",
            "cancel_correction_",
            "recurring_",
        )):
            del st.session_state[key]
    # Rotate after pruning so the browser sees new widget identities. Keep the
    # marker itself above; generations must continue increasing across more
    # than one client/book switch instead of cycling back to g1.
    st.session_state.recurring_widget_gen = (
        st.session_state.get("recurring_widget_gen", 0) + 1
    )
    # A new key generation is the only checkbox reset the frontend honors;
    # popping the keyed value would let the browser re-impose it.
    st.session_state["reversal_confirm_gen"] = (
        st.session_state.get("reversal_confirm_gen", 0) + 1
    )

journal_intent = pop_client_intent(
    st.session_state, "journal", client_id, dbconn.DATABASE_PATH
)
if isinstance(journal_intent, dict):
    requested_view = journal_intent.get("view")
    if requested_view in {
        "New Entry", "View Entries", "Reverse Entry", "Drafts",
        "Templates & recurring",
    }:
        st.session_state.journal_active_tab = requested_view
        st.session_state.pop("_journal_active_tab_rendered", None)
    requested_entry_id = journal_intent.get("entry_id")
    if isinstance(requested_entry_id, int) and requested_entry_id > 0:
        st.session_state.edit_entry_id = requested_entry_id
    return_report = journal_intent.get("return_report")
    if isinstance(return_report, dict):
        st.session_state.journal_return_report = return_report

# Get client info
client = Client.get_by_id(client_id)
st.caption(f"Viewing: **{client.name}**")
current_fy_start, _ = fiscal_year_bounds(date.today(), client.fiscal_year_end_month)

return_report = st.session_state.get("journal_return_report")
if isinstance(return_report, dict) and return_report.get("report") == "General Ledger":
    if st.button("← Back to General Ledger", key="journal_back_to_report"):
        set_client_intent(
            st.session_state,
            "report",
            return_report,
            client_id,
            dbconn.DATABASE_PATH,
        )
        st.session_state.pop("journal_return_report", None)
        st.switch_page("pages/5_Reports.py")

# Initialize session state
if 'je_lines' not in st.session_state:
    st.session_state.je_lines = _empty_je_lines()

if "je_form_gen" not in st.session_state:
    st.session_state.je_form_gen = 0

if "recurring_widget_gen" not in st.session_state:
    st.session_state.recurring_widget_gen = 0

if st.session_state.pop("_prune_je_form_widgets", False):
    # Only stale-generation keys exist this early in the run; the current
    # generation's widgets have not been instantiated yet.
    for key in list(st.session_state):
        if key.startswith(_FORM_WIDGET_PREFIXES):
            del st.session_state[key]

if 'editing_entry_id' not in st.session_state:
    st.session_state.editing_entry_id = None

# Check if we're coming from General Ledger drill-down
if 'edit_entry_id' in st.session_state:
    # A context change already rotated the form before the valid, tagged
    # navigation intent was applied. Same-context drill-downs still need a new
    # generation so an unsaved form cannot overwrite the loaded entry.
    if not journal_scope.changed:
        start_new_form_generation()
    entry_to_edit = JournalEntry.get_by_id(st.session_state.edit_entry_id, client_id=client_id)
    if entry_to_edit:
        import_link = ImportedTransaction.get_links_for_journal_entries(
            client_id, [entry_to_edit.id]
        ).get(entry_to_edit.id)
        if import_link:
            st.session_state.correct_import_entry_id = entry_to_edit.id
            st.info(
                "Imported postings use a category correction so their source "
                "and reconciliation history remain intact."
            )
        else:
            st.session_state.journal_active_tab = "New Entry"
            st.session_state.editing_entry_id = entry_to_edit.id
            st.session_state.je_lines = [
                {
                    'account_id': line.account_id,
                    'debit': line.debit,
                    'credit': line.credit,
                    'memo': line.memo or ''
                }
                for line in entry_to_edit.lines
            ]
            # Also store header fields
            st.session_state.je_entry_date = entry_to_edit.entry_date
            st.session_state.je_entry_type = entry_to_edit.entry_type
            st.session_state.je_source_reference = entry_to_edit.source_reference or ''
            st.session_state.je_description = entry_to_edit.description or ''
            st.session_state.je_aje_reference = entry_to_edit.aje_reference
            st.success(f"Loaded Journal Entry #{entry_to_edit.id} for editing")
    del st.session_state.edit_entry_id


def reset_entry_form():
    start_new_form_generation()
    st.session_state.je_lines = _empty_je_lines()
    st.session_state.editing_entry_id = None
    # Clear header fields
    if 'je_entry_date' in st.session_state:
        del st.session_state.je_entry_date
    if 'je_entry_type' in st.session_state:
        del st.session_state.je_entry_type
    if 'je_source_reference' in st.session_state:
        del st.session_state.je_source_reference
    if 'je_description' in st.session_state:
        del st.session_state.je_description
    if 'je_aje_reference' in st.session_state:
        del st.session_state.je_aje_reference


def load_entry_for_edit(entry: JournalEntry):
    st.session_state.editing_entry_id = entry.id
    st.session_state.je_lines = [
        {
            'account_id': line.account_id,
            'debit': line.debit,
            'credit': line.credit,
            'memo': line.memo or '',
        }
        for line in entry.lines
    ]
    st.session_state.je_entry_date = entry.entry_date
    st.session_state.je_entry_type = entry.entry_type
    st.session_state.je_source_reference = entry.source_reference or ''
    st.session_state.je_description = entry.description or ''
    st.session_state.je_aje_reference = entry.aje_reference
    start_new_form_generation()


def load_template_into_form(template: JournalEntryTemplate):
    """Copy a template into a fresh, editable journal-entry form."""
    st.session_state.editing_entry_id = None
    st.session_state.je_lines = [
        {
            "account_id": line.account_id,
            "debit": to_dollars(line.debit_cents),
            "credit": to_dollars(line.credit_cents),
            "memo": line.memo or "",
        }
        for line in template.lines
    ]
    st.session_state.je_entry_date = date.today()
    st.session_state.je_entry_type = template.entry_type
    st.session_state.je_source_reference = template.source_reference or ""
    st.session_state.je_description = template.description
    st.session_state.je_aje_reference = None
    st.session_state.je_template_loaded_message = (
        f"Loaded template {template.name}. Review the date and amounts before saving."
    )
    st.session_state.journal_active_tab = "New Entry"
    start_new_form_generation()


def render_delete_control(entry_id: int):
    """Require a second, explicit action before permanently deleting an entry."""
    confirmation_key = "confirm_delete_entry_id"
    if st.session_state.get(confirmation_key) != entry_id:
        if st.button("Delete", key=f"delete_entry_{entry_id}"):
            st.session_state[confirmation_key] = entry_id
            st.rerun()
        return

    st.warning("Permanently delete this entry?")
    confirm_col, cancel_col = st.columns(2)
    with confirm_col:
        if st.button("Confirm delete", key=f"confirm_delete_entry_{entry_id}"):
            try:
                JournalEntry.delete(entry_id, client_id=client_id)
                st.session_state.pop(confirmation_key, None)
                st.success("Entry deleted!")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    with cancel_col:
        if st.button("Cancel", key=f"cancel_delete_entry_{entry_id}"):
            st.session_state.pop(confirmation_key, None)
            st.rerun()


def render_entry_controls(entry: JournalEntry, import_link: dict | None):
    if import_link:
        st.caption("Imported posting")
        if st.button("Correct category", key=f"correct_import_{entry.id}"):
            st.session_state.correct_import_entry_id = entry.id
            st.rerun()
        return

    if st.button("Edit", key=f"edit_entry_{entry.id}"):
        load_entry_for_edit(entry)
        # Land the user on the form, or the click appears to do nothing.
        st.session_state.journal_active_tab = "New Entry"
        st.rerun()
    render_delete_control(entry.id)


def render_correction_chain(entry_id: int, drafts: list[DraftEntry]):
    """Show every proposal that names this entry as its original."""
    if not drafts:
        return
    st.markdown("**Correction chain:**")
    for draft in drafts:
        if draft.status == "approved":
            destination = (f"journal entry #{draft.posted_entry_id}"
                           if draft.posted_entry_id else "posted correction")
            st.caption(
                f"JE #{entry_id} → draft #{draft.id} → {destination} "
                f"(approved by {draft.resolved_by or 'a person'})"
            )
        elif draft.status == "rejected":
            st.caption(
                f"JE #{entry_id} → draft #{draft.id} → rejected by "
                f"{draft.resolved_by or 'a person'}"
            )
        else:
            st.caption(f"JE #{entry_id} → draft #{draft.id} → awaiting human review")


correction_success = st.session_state.pop("import_correction_success", None)
if correction_success:
    st.success(correction_success)

correction_entry_id = st.session_state.get("correct_import_entry_id")
if correction_entry_id:
    correction_entry = JournalEntry.get_by_id(correction_entry_id, client_id=client_id)
    correction_link = ImportedTransaction.get_links_for_journal_entries(
        client_id, [correction_entry_id]
    ).get(correction_entry_id)
    if not correction_entry or not correction_link:
        st.session_state.pop("correct_import_entry_id", None)
        st.error("The imported posting could not be found.")
    else:
        with st.container(border=True):
            st.subheader(f"Correct category for imported JE #{correction_entry_id}")
            st.caption(
                "This posts a separate reclassification entry. The original import, "
                "bank leg, and reconciliation history remain unchanged."
            )
            source_col, current_col, amount_col = st.columns(3)
            source_name = correction_link.get("source_filename") or "Imported transaction"
            source_col.markdown(f"**Source**  \n{source_name}")
            current_name = correction_link.get("suggested_account_name") or "Unknown"
            current_number = correction_link.get("suggested_account_number") or "—"
            current_col.markdown(
                f"**Current category**  \n{current_number} - {current_name}"
            )
            amount_col.markdown(f"**Amount**  \n${correction_link['amount']:,.2f}")

            correction_accounts = [
                account
                for account in Account.get_all(client_id, active_only=True)
                if account.id not in {
                    correction_link.get("bank_account_id"),
                    correction_link.get("suggested_account_id"),
                }
            ]
            # No placeholder pseudo-option: a real "-- Select --" option becomes
            # the search text, so type-to-search matches nothing. index=None is
            # Streamlit's native empty state and keeps the box searchable.
            correction_options = {
                account.id: account.display_name() for account in correction_accounts
            }
            target_account_id = st.selectbox(
                "Corrected category",
                options=list(correction_options),
                format_func=lambda account_id: correction_options[account_id],
                key=f"correction_target_{correction_entry_id}",
                index=None,
                placeholder="Type an account number or name",
            )
            correction_date = st.date_input(
                "Correction date",
                # Default to the transaction's own date so the correction lands
                # in the same period as the posting it fixes — defaulting to
                # today silently pushed prior-period corrections into the
                # current month.
                value=correction_link.get("transaction_date") or date.today(),
                key=f"correction_date_{correction_entry_id}",
                format=date_format,
                help="Use an open accounting period. The imported transaction date is not changed.",
            )
            reason = st.text_input(
                "Reason for correction",
                key=f"correction_reason_{correction_entry_id}",
                placeholder="e.g., Merchant was client travel, not office expense",
            )
            action_col, cancel_col, _ = st.columns([1, 1, 2])
            with action_col:
                if st.button(
                    "Post correction",
                    type="primary",
                    disabled=not target_account_id or not reason.strip(),
                    key=f"post_correction_{correction_entry_id}",
                ):
                    try:
                        correction = correct_imported_category(
                            client_id=client_id,
                            journal_entry_id=correction_entry_id,
                            target_account_id=target_account_id,
                            correction_date=correction_date,
                            reason=reason,
                        )
                        st.session_state.pop("correct_import_entry_id", None)
                        st.session_state.import_correction_success = (
                            f"Correction posted as journal entry #{correction.id}."
                        )
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
            with cancel_col:
                if st.button("Cancel", key=f"cancel_correction_{correction_entry_id}"):
                    st.session_state.pop("correct_import_entry_id", None)
                    st.rerun()
        # Keep the correction task focused instead of showing an unrelated new
        # entry form immediately beneath it.
        st.stop()


# Tabs
# A switchable view rather than st.tabs: tabs cannot be preselected, so
# "Edit" on the entry list could load the form but never show it — the page
# appeared to do nothing. Any code may set st.session_state.journal_active_tab
# and rerun to land the user on that view.
if "journal_active_tab" not in st.session_state:
    st.session_state.journal_active_tab = "New Entry"

active_view = view_switcher(
    ["New Entry", "View Entries", "Reverse Entry", "Drafts", "Templates & recurring"],
    key="journal_active_tab"
)

# Point at pending assistant proposals from anywhere on the page.
_pending_drafts = DraftEntry.pending_count(client_id)
if _pending_drafts and active_view != "Drafts":
    _bc1, _bc2 = st.columns([4, 1])
    with _bc1:
        noun = "draft entry" if _pending_drafts == 1 else "draft entries"
        st.info(f"{_pending_drafts} {noun} await review.", icon="📥")
    with _bc2:
        if st.button("Review drafts", key="goto_drafts", width="stretch"):
            st.session_state.journal_active_tab = "Drafts"
            st.rerun()

if active_view == "New Entry":
    st.subheader("Create Journal Entry" if not st.session_state.editing_entry_id else "Edit Journal Entry")

    # Shown after the post-save rerun; a plain st.success before st.rerun()
    # renders for one frame and is wiped before anyone can read it.
    saved_message = st.session_state.pop("je_saved_message", None)
    if saved_message:
        st.success(saved_message)
    template_message = st.session_state.pop("je_template_loaded_message", None)
    if template_message:
        st.info(template_message)

    reusable_templates = JournalEntryTemplate.get_all(client_id)
    if reusable_templates and not st.session_state.editing_entry_id:
        template_options = {template.id: template.name for template in reusable_templates}
        template_col, use_col = st.columns([3, 1])
        with template_col:
            selected_template_id = st.selectbox(
                "Use template",
                options=list(template_options),
                format_func=lambda template_id: template_options[template_id],
                index=None,
                placeholder="Choose a saved template",
                key=journal_scope.key("je_use_template"),
            )
        with use_col:
            st.write("")
            st.write("")
            if st.button(
                "Load template", disabled=not selected_template_id,
                key=journal_scope.key("je_load_template"),
            ):
                selected_template = JournalEntryTemplate.get_by_id(
                    selected_template_id, client_id
                )
                if selected_template:
                    load_template_into_form(selected_template)
                    st.rerun()

    # Get all active accounts for dropdown
    accounts = Account.get_all(client_id, active_only=True)
    # No "-- Select Account --" pseudo-option: its label becomes the search
    # text, so typing an account number appends to it and matches nothing.
    # An unset line is represented by selectbox index=None instead.
    account_options = {a.id: a.display_name() for a in accounts}
    # Preserve an entry's historical account selections while editing even if
    # an account has since been deactivated. Inactive accounts remain unavailable
    # for brand-new lines, but editing must not silently reset an existing line.
    if st.session_state.editing_entry_id:
        for account_id in {line['account_id'] for line in st.session_state.je_lines}:
            if account_id and account_id not in account_options:
                inactive = Account.get_by_id(account_id, client_id=client_id)
                if inactive:
                    account_options[account_id] = f"{inactive.display_name()} (inactive)"

    # Entry header - use session state values if editing
    entry_type_options = EntryType.ALL

    # Get default values from session state if editing
    default_date = st.session_state.get('je_entry_date', date.today())
    default_type = st.session_state.get('je_entry_type', 'Regular')
    default_ref = st.session_state.get('je_source_reference', '')
    default_desc = st.session_state.get('je_description', '')

    col1, col2, col3 = st.columns(3)

    # Generation-keyed (hdr_key) so saving or loading an entry actually resets
    # them — unkeyed widgets keep their browser-side state across a reset.
    with col1:
        entry_date = st.date_input(
            "Date", value=default_date, key=hdr_key("date"),
            format=date_format,
        )

    with col2:
        type_index = entry_type_options.index(default_type) if default_type in entry_type_options else 0
        entry_type = st.selectbox("Entry Type", options=entry_type_options, index=type_index, key=hdr_key("type"))

    with col3:
        source_reference = st.text_input("Source Reference", value=default_ref, placeholder="e.g., Bank stmt pg 3", key=hdr_key("ref"))

    description = st.text_input("Description", value=default_desc, placeholder="Description of the entry", key=hdr_key("desc"))

    st.divider()

    # Entry lines
    st.markdown("**Entry Lines**")

    # Display running totals. Read the widgets' committed session state, not
    # je_lines: the widgets write into je_lines AFTER this block runs, so
    # je_lines is one commit behind here and the totals would always trail the
    # values visible in the boxes by one interaction.
    def committed_amount(name: str, i: int, fallback: float) -> float:
        return float(st.session_state.get(line_key(name, i), fallback))

    total_debits = sum(
        committed_amount("debit", i, line['debit'])
        for i, line in enumerate(st.session_state.je_lines)
    )
    total_credits = sum(
        committed_amount("credit", i, line['credit'])
        for i, line in enumerate(st.session_state.je_lines)
    )
    difference = total_debits - total_credits

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Debits", f"${total_debits:,.2f}")
    with col2:
        st.metric("Total Credits", f"${total_credits:,.2f}")
    with col3:
        if abs(difference) < 0.01:
            st.metric("Difference", "$0.00", delta="Balanced")
        else:
            st.metric("Difference", f"${abs(difference):,.2f}", delta="Not balanced", delta_color="inverse")

    st.caption("Totals pick up a value once it's applied — press Enter or click "
               "out of the field after typing.")

    st.divider()

    # Line items
    for i, line in enumerate(st.session_state.je_lines):
        col1, col2, col3, col4, col5 = st.columns([3, 1.5, 1.5, 2, 0.5])

        with col1:
            account_id = st.selectbox(
                "Account",
                options=list(account_options.keys()),
                format_func=lambda x: account_options[x],
                key=line_key("account", i),
                index=list(account_options.keys()).index(line['account_id']) if line['account_id'] in account_options else None,
                placeholder="Type an account number or name",
            )
            # je_lines keeps 0 for "unset" so validation and older entries agree.
            st.session_state.je_lines[i]['account_id'] = account_id or 0

        with col2:
            debit = st.number_input(
                "Debit",
                min_value=0.0,
                value=float(line['debit']),
                step=0.01,
                key=line_key("debit", i)
            )
            st.session_state.je_lines[i]['debit'] = debit

        with col3:
            credit = st.number_input(
                "Credit",
                min_value=0.0,
                value=float(line['credit']),
                step=0.01,
                key=line_key("credit", i)
            )
            st.session_state.je_lines[i]['credit'] = credit

        with col4:
            memo = st.text_input(
                "Memo",
                value=line['memo'],
                key=line_key("memo", i),
                placeholder="Optional"
            )
            st.session_state.je_lines[i]['memo'] = memo

        with col5:
            st.write("")  # Spacing
            st.write("")  # Spacing
            if len(st.session_state.je_lines) > 2:
                if st.button("Remove", key=f"delete_line_{i}", help="Remove this line"):
                    st.session_state.je_lines.pop(i)
                    # New keys for the shifted lines, or the browser re-imposes
                    # each row's old widget values onto its new neighbor.
                    start_new_form_generation()
                    st.rerun()

    # Add line button
    if st.button("Add line"):
        st.session_state.je_lines.append({'account_id': 0, 'debit': 0.0, 'credit': 0.0, 'memo': ''})
        st.rerun()

    st.divider()

    # Save buttons
    col1, col2 = st.columns(2)

    with col1:
        if st.button("Save Entry", type="primary"):
            # Validate and save
            lines = []
            for line in st.session_state.je_lines:
                if line['account_id'] != 0 and (line['debit'] > 0 or line['credit'] > 0):
                    lines.append(JournalEntryLine(
                        account_id=line['account_id'],
                        debit=line['debit'],
                        credit=line['credit'],
                        memo=line['memo'] if line['memo'] else None
                    ))

            # Keep an edited entry's AJE reference (the update overwrites the
            # column), and give a hand-keyed adjusting entry the next AJE-00x
            # so it is findable everywhere AJEs are listed by reference.
            aje_reference = st.session_state.get('je_aje_reference')
            if entry_type == 'Adjusting':
                if not aje_reference:
                    fy_first, fy_last = fiscal_year_bounds(
                        entry_date, client.fiscal_year_end_month
                    )
                    aje_reference = JournalEntry.get_next_aje_reference(
                        client_id, fy_first, fy_last
                    )
            else:
                aje_reference = None

            entry = JournalEntry(
                id=st.session_state.editing_entry_id,
                client_id=client_id,
                entry_date=entry_date,
                description=description,
                source_reference=source_reference if source_reference else None,
                entry_type=entry_type,
                aje_reference=aje_reference,
                lines=lines
            )

            errors = entry.validate()
            if errors:
                for error in errors:
                    st.error(error)
            else:
                try:
                    entry.save()
                    st.session_state.je_saved_message = (
                        f"Journal entry #{entry.id} updated!"
                        if st.session_state.editing_entry_id
                        else f"Journal entry #{entry.id} created!"
                    )
                    reset_entry_form()
                    st.rerun()
                except Exception as e:
                    st.error(f"Error saving entry: {e}")

    with col2:
        if st.button("Clear Form"):
            reset_entry_form()
            st.rerun()

    if not st.session_state.editing_entry_id:
        with st.expander("Save this entry as a template"):
            st.caption(
                "The date and AJE reference are not saved. Scheduled use is set up "
                "after the template is created."
            )
            template_name_gen = st.session_state.get("je_template_name_gen", 0)
            template_name = st.text_input(
                "Template name",
                key=journal_scope.key(
                    f"je_new_template_name_g{template_name_gen}"
                ),
                placeholder="e.g., Monthly prepaid amortization",
            )
            if st.button(
                "Save template", key=journal_scope.key("je_save_template"),
                disabled=dbconn.READ_ONLY or not template_name.strip(),
            ):
                try:
                    template_lines = [
                        TemplateLine(
                            account_id=int(line["account_id"]),
                            debit_cents=to_cents(line["debit"]),
                            credit_cents=to_cents(line["credit"]),
                            memo=line["memo"] or "",
                        )
                        for line in st.session_state.je_lines
                        if line["account_id"]
                        and (line["debit"] > 0 or line["credit"] > 0)
                    ]
                    template = JournalEntryTemplate(
                        client_id=client_id, name=template_name,
                        description=description, entry_type=entry_type,
                        source_reference=source_reference or "",
                        lines=template_lines,
                    )
                    template.save()
                except Exception as exc:
                    st.error(str(exc))
                else:
                    st.session_state.je_saved_message = (
                        f"Template {template.name} created. Add a recurring "
                        "schedule from Templates & recurring when needed."
                    )
                    st.session_state.je_template_name_gen = template_name_gen + 1
                    st.rerun()

elif active_view == "View Entries":
    st.subheader("Journal Entry List")

    # Quick search by Entry ID
    search_col1, search_col2 = st.columns([1, 3])
    with search_col1:
        search_id = st.number_input("Find Entry #", min_value=0, value=0, step=1, key="search_entry_id")
    with search_col2:
        if search_id > 0:
            if st.button("Go to Entry", key="search_btn"):
                found_entry = JournalEntry.get_by_id(search_id, client_id=client_id)
                if found_entry:
                    import_link = ImportedTransaction.get_links_for_journal_entries(
                        client_id, [found_entry.id]
                    ).get(found_entry.id)
                    if import_link:
                        st.session_state.correct_import_entry_id = found_entry.id
                    else:
                        load_entry_for_edit(found_entry)
                        st.session_state.journal_active_tab = "New Entry"
                    st.rerun()
                else:
                    st.error(f"Entry #{search_id} not found for this client.")

    st.divider()

    # Filters
    col1, col2, col3 = st.columns(3)

    with col1:
        filter_start = st.date_input(
            "From Date", value=current_fy_start, key="filter_start",
            format=date_format,
        )

    with col2:
        filter_end = st.date_input(
            "To Date", value=date.today(), key="filter_end",
            format=date_format,
        )

    with col3:
        filter_type = st.selectbox("Entry Type", options=['All'] + EntryType.ALL, key="filter_type")

    search_col, account_col = st.columns([2, 1])
    with search_col:
        filter_search = st.text_input(
            "Search", key="filter_search",
            placeholder="Description, reference, AJE #, or amount",
        )
    with account_col:
        # Own options dict — the New Entry view builds its own and only one
        # view's code runs per render.
        filter_account_options = {
            a.id: a.display_name()
            for a in Account.get_all(client_id, active_only=True)
        }
        filter_account = st.selectbox(
            "Account",
            options=list(filter_account_options.keys()),
            format_func=lambda x: filter_account_options[x],
            key="filter_account",
            index=None,
            placeholder="All accounts",
        )

    if filter_start > filter_end:
        st.error("Journal entry filter start date cannot be after the end date.")
        st.stop()

    entry_type_param = filter_type if filter_type != 'All' else None
    search_param = filter_search.strip() or None
    filter_signature = (filter_start, filter_end, entry_type_param,
                        search_param, filter_account)
    if st.session_state.get("journal_filter_signature") != filter_signature:
        st.session_state.journal_filter_signature = filter_signature
        st.session_state.journal_page = 1

    page_size = 25
    summary = JournalEntry.get_filtered_summary(
        client_id=client_id, start_date=filter_start, end_date=filter_end,
        entry_type=entry_type_param, search_term=search_param,
        account_id=filter_account,
    )
    page_count = max(1, (summary["total_count"] + page_size - 1) // page_size)
    current_page = min(max(1, st.session_state.get("journal_page", 1)), page_count)
    st.session_state.journal_page = current_page

    metric_cols = st.columns(3)
    with metric_cols[0]:
        st.metric("Filtered Entries", summary["total_count"])
    with metric_cols[1]:
        st.metric("Total Debits", f"${summary['total_debits']:,.2f}")
    with metric_cols[2]:
        st.metric("Total Credits", f"${summary['total_credits']:,.2f}")

    nav_left, nav_status, nav_right = st.columns([1, 2, 1])
    with nav_left:
        if st.button("Previous", disabled=current_page <= 1, key="journal_previous"):
            st.session_state.journal_page = current_page - 1
            st.rerun()
    with nav_status:
        first_row = (current_page - 1) * page_size + 1 if summary["total_count"] else 0
        last_row = min(current_page * page_size, summary["total_count"])
        st.caption(
            f"Page {current_page} of {page_count} · showing {first_row}–{last_row} "
            f"of {summary['total_count']}"
        )
    with nav_right:
        if st.button("Next", disabled=current_page >= page_count, key="journal_next"):
            st.session_state.journal_page = current_page + 1
            st.rerun()

    entries = JournalEntry.get_all(
        client_id=client_id,
        start_date=filter_start,
        end_date=filter_end,
        entry_type=entry_type_param,
        search_term=search_param,
        account_id=filter_account,
        limit=page_size,
        offset=(current_page - 1) * page_size,
    )
    import_links = ImportedTransaction.get_links_for_journal_entries(
        client_id, [entry.id for entry in entries]
    )
    correction_chains = DraftEntry.get_for_originals(
        client_id, [entry.id for entry in entries]
    )

    if not entries:
        st.info("No journal entries found for the selected filters.")
    else:
        for entry in entries:
            # The collapsed row must reveal the entry type — an AJE that looks
            # identical to a regular entry can't be found by scanning the list.
            header = f"**#{entry.id}**"
            if entry.entry_type == 'Adjusting':
                header += f" ({entry.aje_reference or 'AJE'})"
            elif entry.entry_type != 'Regular':
                header += f" ({entry.entry_type})"
            header += (
                f" | {display_date(entry.entry_date, date_format)} | "
                f"{entry.description or 'No description'} | "
                f"${entry.total_debits():,.2f}"
            )

            # Use different styling for special entry types
            if entry.entry_type == 'Beginning Balance':
                with st.expander(header, expanded=False):
                    st.success("**Beginning Balance Entry**")
                    render_correction_chain(
                        entry.id, correction_chains.get(entry.id, [])
                    )
                    col1, col2 = st.columns([3, 1])

                    with col1:
                        if entry.source_reference:
                            st.caption(f"Source Reference: {entry.source_reference}")
                        st.caption(f"Type: {entry.entry_type}")

                        # Show lines
                        st.markdown("**Lines:**")
                        for line in entry.lines:
                            debit_str = f"${line.debit:,.2f}" if line.debit > 0 else ""
                            credit_str = f"${line.credit:,.2f}" if line.credit > 0 else ""
                            memo_str = f" - {line.memo}" if line.memo else ""
                            st.text(f"  {line.account_number} - {line.account_name}: Dr {debit_str} Cr {credit_str}{memo_str}")

                    with col2:
                        render_entry_controls(entry, import_links.get(entry.id))

            elif entry.entry_type == 'Adjusting':
                with st.expander(header, expanded=False):
                    st.info("**Adjusting Entry**")
                    render_correction_chain(
                        entry.id, correction_chains.get(entry.id, [])
                    )
                    col1, col2 = st.columns([3, 1])

                    with col1:
                        if entry.aje_reference:
                            st.caption(f"AJE Reference: **{entry.aje_reference}**")
                        if entry.source_reference:
                            st.caption(f"Source Reference: {entry.source_reference}")
                        st.caption(f"Type: {entry.entry_type}")

                        # Show lines
                        st.markdown("**Lines:**")
                        for line in entry.lines:
                            debit_str = f"${line.debit:,.2f}" if line.debit > 0 else ""
                            credit_str = f"${line.credit:,.2f}" if line.credit > 0 else ""
                            memo_str = f" - {line.memo}" if line.memo else ""
                            st.text(f"  {line.account_number} - {line.account_name}: Dr {debit_str} Cr {credit_str}{memo_str}")

                    with col2:
                        render_entry_controls(entry, import_links.get(entry.id))

            else:
                with st.expander(header):
                    render_correction_chain(
                        entry.id, correction_chains.get(entry.id, [])
                    )
                    col1, col2 = st.columns([3, 1])

                    with col1:
                        if entry.source_reference:
                            st.caption(f"Reference: {entry.source_reference}")
                        st.caption(f"Type: {entry.entry_type}")

                        # Show lines
                        st.markdown("**Lines:**")
                        for line in entry.lines:
                            debit_str = f"${line.debit:,.2f}" if line.debit > 0 else ""
                            credit_str = f"${line.credit:,.2f}" if line.credit > 0 else ""
                            memo_str = f" - {line.memo}" if line.memo else ""
                            st.text(f"  {line.account_number} - {line.account_name}: Dr {debit_str} Cr {credit_str}{memo_str}")

                    with col2:
                        render_entry_controls(entry, import_links.get(entry.id))

elif active_view == "Reverse Entry":
    st.subheader("Reverse a Journal Entry")
    st.caption(
        "A reversal creates a new equal-and-opposite entry. The original remains intact "
        "so the accounting history and audit trail are preserved."
    )

    _reversal_msg = st.session_state.pop("reversal_result", None)
    if _reversal_msg:
        st.success(_reversal_msg)

    reversal_entry_id = st.number_input(
        "Original journal entry #", min_value=1, value=1, step=1,
        key="reversal_entry_id",
    )
    original = JournalEntry.get_by_id(int(reversal_entry_id), client_id=client_id)
    if not original:
        st.info("Enter an existing journal entry number for this client.")
    else:
        st.markdown(
            f"**JE #{original.id}** · "
            f"{display_date(original.entry_date, date_format)} · "
            f"{original.description or 'No description'} · ${original.total_debits():,.2f}"
        )
        for line in original.lines:
            debit = f"${line.debit:,.2f}" if line.debit else "—"
            credit = f"${line.credit:,.2f}" if line.credit else "—"
            st.caption(f"{line.account_number} {line.account_name} · Debit {debit} · Credit {credit}")

        linked_corrections = DraftEntry.get_for_originals(
            client_id, [original.id]
        ).get(original.id, [])
        render_correction_chain(original.id, linked_corrections)
        pending_corrections = [
            draft for draft in linked_corrections if draft.status == "pending"
        ]
        if pending_corrections:
            draft_numbers = ", ".join(
                f"#{draft.id}" for draft in pending_corrections
            )
            st.warning(
                f"Resolve pending correction draft {draft_numbers} before "
                "reversing this entry, so the same issue is not corrected twice."
            )

        reversal_date = st.date_input(
            "Reversal date", value=date.today(), key="reversal_date",
            format=date_format,
        )
        # The confirm checkbox key carries a generation nonce: a fresh key is
        # the only reset the frontend honors, and writing the widget's own key
        # after instantiation raises mid-render.
        confirm_gen = st.session_state.get("reversal_confirm_gen", 0)
        confirmed = st.checkbox(
            "I understand this posts a new entry and does not delete the original.",
            key=f"confirm_reversal_{confirm_gen}",
        )
        if st.button(
            "Post reversal", type="primary",
            disabled=not confirmed or bool(pending_corrections),
            key="post_reversal",
        ):
            try:
                reversal = JournalEntry.reverse(original.id, client_id, reversal_date)
                st.session_state["reversal_confirm_gen"] = confirm_gen + 1
                st.session_state["reversal_result"] = (
                    f"Reversal posted as JE #{reversal.id}."
                )
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


elif active_view == "Templates & recurring":
    render_recurring_view(client, date_format, load_template_into_form)


if active_view == "Drafts":
    st.subheader("Draft entries")
    st.caption(
        "Proposals filed by your assistant or generated from a recurring "
        "schedule. Nothing here is in the books: approving posts a real journal entry under your name, "
        "rejecting marks the proposal rejected, and the audit trail records both."
    )
    _draft_msg = st.session_state.pop("draft_result", None)
    if _draft_msg:
        st.success(_draft_msg)

    _pending = DraftEntry.get_pending(client_id)
    if not _pending:
        st.info("No drafts waiting for review.")
    else:
        _names = {a.account_number: a.name
                  for a in Account.get_all(client_id, active_only=False)}
        _recurring_contexts = recurring_draft_contexts(
            client_id, [draft.id for draft in _pending]
        )
        for d in _pending:
            with st.container(border=True):
                st.markdown(
                    f"**{display_date(d.entry_date, date_format)} · "
                    f"{d.description}**"
                )
                st.caption(f"Draft #{d.id} · {d.entry_type} · proposed by "
                           f"{d.proposed_by}"
                           + (f" · {d.proposed_at}" if d.proposed_at else ""))
                recurring_context = _recurring_contexts.get(d.id)
                if recurring_context:
                    role = recurring_context["role"].lower()
                    st.info(
                        f"Recurring {role} · {recurring_context['template_name']} · "
                        f"{recurring_context['period_name']} · generation "
                        f"{recurring_context['generation_number']}"
                    )
                    if (
                        recurring_context["role"] == "Primary"
                        and recurring_context["reversal_rule"] == "NextDay"
                    ):
                        st.caption(
                            "Approval will also create a separate reversal draft "
                            "dated the day after period end."
                        )
                if d.rationale:
                    # Plain body text, not italics — an assistant's rationale
                    # runs to a paragraph, and italics at that length is the
                    # hardest-reading text on the page.
                    st.markdown("**Why:** "
                                + d.rationale.replace("\n", "  \n"))
                proposed_rows = [
                    {"Account": f"{l.account_number} - "
                                f"{_names.get(str(l.account_number), '?')}",
                     "Debit": f"{l.debit_cents / 100:,.2f}" if l.debit_cents else "",
                     "Credit": f"{l.credit_cents / 100:,.2f}" if l.credit_cents else "",
                     "Memo": l.memo or ""}
                    for l in d.lines
                ]
                if d.original_entry_id is not None:
                    original = JournalEntry.get_by_id(
                        d.original_entry_id, client_id=client_id
                    )
                    st.info(
                        f"Correction proposal linked to original journal entry "
                        f"#{d.original_entry_id}. Review both sides before deciding."
                    )
                    original_col, proposed_col = st.columns(2)
                    with original_col:
                        st.markdown(f"**Original · JE #{d.original_entry_id}**")
                        if original:
                            st.caption(
                                f"{display_date(original.entry_date, date_format)} · "
                                f"{original.description or 'No description'}"
                            )
                            st.table([
                                {"Account": f"{line.account_number} - "
                                            f"{line.account_name or '?'}",
                                 "Debit": f"{line.debit:,.2f}" if line.debit else "",
                                 "Credit": f"{line.credit:,.2f}" if line.credit else "",
                                 "Memo": line.memo or ""}
                                for line in original.lines
                            ])
                        else:
                            st.error("The linked original entry is unavailable.")
                    with proposed_col:
                        st.markdown(f"**Proposed correction · Draft #{d.id}**")
                        st.caption(
                            f"{display_date(d.entry_date, date_format)} · "
                            f"{d.description}"
                        )
                        st.table(proposed_rows)
                else:
                    st.table(proposed_rows)
                _a1, _a2, _sp = st.columns([1, 1, 3])
                with _a1:
                    if st.button("Approve & post", type="primary",
                                 key=f"draft_approve_{d.id}",
                                 disabled=dbconn.READ_ONLY):
                        try:
                            _entry_id = d.approve()
                        except Exception as exc:
                            st.error(f"Could not post the draft: {exc}")
                        else:
                            result = (
                                f"Draft #{d.id} posted as journal entry #{_entry_id}."
                            )
                            if (
                                recurring_context
                                and recurring_context["role"] == "Primary"
                                and recurring_context["reversal_rule"] == "NextDay"
                            ):
                                result += " Its reversal draft now awaits review."
                            st.session_state.draft_result = result
                            st.rerun()
                with _a2:
                    if st.button("Reject", key=f"draft_reject_{d.id}",
                                 disabled=dbconn.READ_ONLY):
                        d.reject()
                        st.session_state.draft_result = f"Draft #{d.id} rejected."
                        st.rerun()

    _reviewed_drafts = DraftEntry.get_resolved(client_id)
    if _reviewed_drafts:
        _reviewed_contexts = recurring_draft_contexts(
            client_id, [draft.id for draft in _reviewed_drafts]
        )
        with st.expander(f"Recently reviewed drafts ({len(_reviewed_drafts)})"):
            st.dataframe([
                {
                    "Draft": f"#{d.id}",
                    "Entry date": d.entry_date,
                    "Description": d.description,
                    "Corrects": (f"JE #{d.original_entry_id}"
                                 if d.original_entry_id else "—"),
                    "Source": (
                        f"Recurring {_reviewed_contexts[d.id]['role'].lower()} · "
                        f"{_reviewed_contexts[d.id]['template_name']}"
                        if d.id in _reviewed_contexts else d.proposed_by
                    ),
                    "Result": d.status.title(),
                    "Reviewed by": d.resolved_by or "—",
                    "Reviewed at": d.resolved_at or "—",
                    "Journal entry": f"#{d.posted_entry_id}" if d.posted_entry_id else "—",
                }
                for d in _reviewed_drafts
            ], width="stretch", hide_index=True)
