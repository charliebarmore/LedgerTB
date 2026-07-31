import streamlit as st
import sys
from pathlib import Path
from datetime import date

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.account import Account
from models.client import Client
from models.journal_entry import JournalEntry, JournalEntryLine
from models.transaction import ImportedTransaction
from services.import_corrections import correct_imported_category
from database import init_database
from utils.client_selector import render_client_selector
from utils.unlock import require_unlock
from utils import icons
from constants import EntryType
from utils.fiscal_dates import fiscal_year_bounds
from utils.ui import view_switcher


_FORM_WIDGET_PREFIXES = ("account_", "debit_", "credit_", "memo_", "je_hdr_")


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

client_id = render_client_selector()

st.title("Journal Entries")

# Quick link to Trial Balance Worksheet
st.page_link("pages/1_Trial_Balance_Worksheet.py", label="Back to Trial Balance Worksheet", icon=icons.TRIAL_BALANCE)

if not client_id:
    st.warning("Please create a client first in the Clients page.")
    st.page_link("pages/0_Clients.py", label="Go to Clients →")
    st.stop()

# Get client info
client = Client.get_by_id(client_id)
st.caption(f"Viewing: **{client.name}**")
current_fy_start, _ = fiscal_year_bounds(date.today(), client.fiscal_year_end_month)

# Initialize session state
if 'je_lines' not in st.session_state:
    st.session_state.je_lines = [
        {'account_id': 0, 'debit': 0.0, 'credit': 0.0, 'memo': ''},
        {'account_id': 0, 'debit': 0.0, 'credit': 0.0, 'memo': ''}
    ]

if "je_form_gen" not in st.session_state:
    st.session_state.je_form_gen = 0

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
    st.session_state.je_lines = [
        {'account_id': 0, 'debit': 0.0, 'credit': 0.0, 'memo': ''},
        {'account_id': 0, 'debit': 0.0, 'credit': 0.0, 'memo': ''}
    ]
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
    ["New Entry", "View Entries", "Reverse Entry"], key="journal_active_tab"
)

if active_view == "New Entry":
    st.subheader("Create Journal Entry" if not st.session_state.editing_entry_id else "Edit Journal Entry")

    # Shown after the post-save rerun; a plain st.success before st.rerun()
    # renders for one frame and is wiped before anyone can read it.
    saved_message = st.session_state.pop("je_saved_message", None)
    if saved_message:
        st.success(saved_message)

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
        entry_date = st.date_input("Date", value=default_date, key=hdr_key("date"))

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
        filter_start = st.date_input("From Date", value=current_fy_start, key="filter_start")

    with col2:
        filter_end = st.date_input("To Date", value=date.today(), key="filter_end")

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
            header += f" | {entry.entry_date} | {entry.description or 'No description'} | ${entry.total_debits():,.2f}"

            # Use different styling for special entry types
            if entry.entry_type == 'Beginning Balance':
                with st.expander(header, expanded=False):
                    st.success("**Beginning Balance Entry**")
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

    reversal_entry_id = st.number_input(
        "Original journal entry #", min_value=1, value=1, step=1,
        key="reversal_entry_id",
    )
    original = JournalEntry.get_by_id(int(reversal_entry_id), client_id=client_id)
    if not original:
        st.info("Enter an existing journal entry number for this client.")
    else:
        st.markdown(
            f"**JE #{original.id}** · {original.entry_date} · "
            f"{original.description or 'No description'} · ${original.total_debits():,.2f}"
        )
        for line in original.lines:
            debit = f"${line.debit:,.2f}" if line.debit else "—"
            credit = f"${line.credit:,.2f}" if line.credit else "—"
            st.caption(f"{line.account_number} {line.account_name} · Debit {debit} · Credit {credit}")

        reversal_date = st.date_input(
            "Reversal date", value=date.today(), key="reversal_date",
        )
        confirmed = st.checkbox(
            "I understand this posts a new entry and does not delete the original.",
            key="confirm_reversal",
        )
        if st.button(
            "Post reversal", type="primary", disabled=not confirmed,
            key="post_reversal",
        ):
            try:
                reversal = JournalEntry.reverse(original.id, client_id, reversal_date)
                st.success(f"Reversal posted as JE #{reversal.id}.")
                st.session_state.confirm_reversal = False
            except ValueError as exc:
                st.error(str(exc))
