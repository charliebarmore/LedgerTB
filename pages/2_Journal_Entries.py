import streamlit as st
import sys
from pathlib import Path
from datetime import date, timedelta

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.account import Account
from models.client import Client
from models.journal_entry import JournalEntry, JournalEntryLine
from database import init_database
from utils.client_selector import render_client_selector
from utils import icons
from constants import EntryType

# Initialize database
init_database()

st.set_page_config(page_title="Journal Entries", page_icon=icons.JOURNAL_ENTRIES, layout="wide")

# Client selector in sidebar
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

# Initialize session state
if 'je_lines' not in st.session_state:
    st.session_state.je_lines = [
        {'account_id': 0, 'debit': 0.0, 'credit': 0.0, 'memo': ''},
        {'account_id': 0, 'debit': 0.0, 'credit': 0.0, 'memo': ''}
    ]

if 'editing_entry_id' not in st.session_state:
    st.session_state.editing_entry_id = None

# Check if we're coming from General Ledger drill-down
if 'edit_entry_id' in st.session_state:
    entry_to_edit = JournalEntry.get_by_id(st.session_state.edit_entry_id, client_id=client_id)
    if entry_to_edit:
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
        st.success(f"Loaded Journal Entry #{entry_to_edit.id} for editing")
    del st.session_state.edit_entry_id


def reset_entry_form():
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


# Tabs
tab1, tab2, tab3 = st.tabs(["New Entry", "View Entries", "Reverse Entry"])

with tab1:
    st.subheader("Create Journal Entry" if not st.session_state.editing_entry_id else "Edit Journal Entry")

    # Get all active accounts for dropdown
    accounts = Account.get_all(client_id, active_only=True)
    account_options = {0: "-- Select Account --"}
    account_options.update({a.id: a.display_name() for a in accounts})
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

    with col1:
        entry_date = st.date_input("Date", value=default_date)

    with col2:
        type_index = entry_type_options.index(default_type) if default_type in entry_type_options else 0
        entry_type = st.selectbox("Entry Type", options=entry_type_options, index=type_index)

    with col3:
        source_reference = st.text_input("Source Reference", value=default_ref, placeholder="e.g., Bank stmt pg 3")

    description = st.text_input("Description", value=default_desc, placeholder="Description of the entry")

    st.divider()

    # Entry lines
    st.markdown("**Entry Lines**")

    # Display running totals
    total_debits = sum(line['debit'] for line in st.session_state.je_lines)
    total_credits = sum(line['credit'] for line in st.session_state.je_lines)
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

    st.divider()

    # Line items
    for i, line in enumerate(st.session_state.je_lines):
        col1, col2, col3, col4, col5 = st.columns([3, 1.5, 1.5, 2, 0.5])

        with col1:
            account_id = st.selectbox(
                "Account",
                options=list(account_options.keys()),
                format_func=lambda x: account_options[x],
                key=f"account_{i}",
                index=list(account_options.keys()).index(line['account_id']) if line['account_id'] in account_options else 0
            )
            st.session_state.je_lines[i]['account_id'] = account_id

        with col2:
            debit = st.number_input(
                "Debit",
                min_value=0.0,
                value=float(line['debit']),
                step=0.01,
                key=f"debit_{i}"
            )
            st.session_state.je_lines[i]['debit'] = debit

        with col3:
            credit = st.number_input(
                "Credit",
                min_value=0.0,
                value=float(line['credit']),
                step=0.01,
                key=f"credit_{i}"
            )
            st.session_state.je_lines[i]['credit'] = credit

        with col4:
            memo = st.text_input(
                "Memo",
                value=line['memo'],
                key=f"memo_{i}",
                placeholder="Optional"
            )
            st.session_state.je_lines[i]['memo'] = memo

        with col5:
            st.write("")  # Spacing
            st.write("")  # Spacing
            if len(st.session_state.je_lines) > 2:
                if st.button("Remove", key=f"delete_line_{i}", help="Remove this line"):
                    st.session_state.je_lines.pop(i)
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

            entry = JournalEntry(
                id=st.session_state.editing_entry_id,
                client_id=client_id,
                entry_date=entry_date,
                description=description,
                source_reference=source_reference if source_reference else None,
                entry_type=entry_type,
                lines=lines
            )

            errors = entry.validate()
            if errors:
                for error in errors:
                    st.error(error)
            else:
                try:
                    entry.save()
                    if st.session_state.editing_entry_id:
                        st.success("Journal entry updated!")
                    else:
                        st.success(f"Journal entry #{entry.id} created!")
                    reset_entry_form()
                    st.rerun()
                except Exception as e:
                    st.error(f"Error saving entry: {e}")

    with col2:
        if st.button("Clear Form"):
            reset_entry_form()
            st.rerun()

with tab2:
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
                    # Load into edit form (including header fields)
                    st.session_state.editing_entry_id = found_entry.id
                    st.session_state.je_lines = [
                        {
                            'account_id': line.account_id,
                            'debit': line.debit,
                            'credit': line.credit,
                            'memo': line.memo or ''
                        }
                        for line in found_entry.lines
                    ]
                    st.session_state.je_entry_date = found_entry.entry_date
                    st.session_state.je_entry_type = found_entry.entry_type
                    st.session_state.je_source_reference = found_entry.source_reference or ''
                    st.session_state.je_description = found_entry.description or ''
                    st.rerun()
                else:
                    st.error(f"Entry #{search_id} not found for this client.")

    st.divider()

    # Filters
    col1, col2, col3 = st.columns(3)

    with col1:
        filter_start = st.date_input("From Date", value=date.today() - timedelta(days=365), key="filter_start")

    with col2:
        filter_end = st.date_input("To Date", value=date.today(), key="filter_end")

    with col3:
        filter_type = st.selectbox("Entry Type", options=['All'] + EntryType.ALL, key="filter_type")

    entry_type_param = filter_type if filter_type != 'All' else None
    filter_signature = (filter_start, filter_end, entry_type_param)
    if st.session_state.get("journal_filter_signature") != filter_signature:
        st.session_state.journal_filter_signature = filter_signature
        st.session_state.journal_page = 1

    page_size = 25
    summary = JournalEntry.get_filtered_summary(
        client_id=client_id, start_date=filter_start, end_date=filter_end,
        entry_type=entry_type_param,
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
        limit=page_size,
        offset=(current_page - 1) * page_size,
    )

    if not entries:
        st.info("No journal entries found for the selected filters.")
    else:
        for entry in entries:
            # Build header with AJE reference if applicable
            header = f"**#{entry.id}**"
            if entry.entry_type == 'Adjusting' and entry.aje_reference:
                header += f" ({entry.aje_reference})"
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
                        if st.button("Edit", key=f"edit_entry_{entry.id}"):
                            # Load entry into form (including header fields)
                            st.session_state.editing_entry_id = entry.id
                            st.session_state.je_lines = [
                                {
                                    'account_id': line.account_id,
                                    'debit': line.debit,
                                    'credit': line.credit,
                                    'memo': line.memo or ''
                                }
                                for line in entry.lines
                            ]
                            st.session_state.je_entry_date = entry.entry_date
                            st.session_state.je_entry_type = entry.entry_type
                            st.session_state.je_source_reference = entry.source_reference or ''
                            st.session_state.je_description = entry.description or ''
                            st.rerun()

                        if st.button("Delete", key=f"delete_entry_{entry.id}"):
                            try:
                                JournalEntry.delete(entry.id, client_id=client_id)
                                st.success("Entry deleted!")
                                st.rerun()
                            except ValueError as e:
                                st.error(str(e))

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
                        if st.button("Edit", key=f"edit_entry_{entry.id}"):
                            # Load entry into form (including header fields)
                            st.session_state.editing_entry_id = entry.id
                            st.session_state.je_lines = [
                                {
                                    'account_id': line.account_id,
                                    'debit': line.debit,
                                    'credit': line.credit,
                                    'memo': line.memo or ''
                                }
                                for line in entry.lines
                            ]
                            st.session_state.je_entry_date = entry.entry_date
                            st.session_state.je_entry_type = entry.entry_type
                            st.session_state.je_source_reference = entry.source_reference or ''
                            st.session_state.je_description = entry.description or ''
                            st.rerun()

                        if st.button("Delete", key=f"delete_entry_{entry.id}"):
                            try:
                                JournalEntry.delete(entry.id, client_id=client_id)
                                st.success("Entry deleted!")
                                st.rerun()
                            except ValueError as e:
                                st.error(str(e))

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
                        if st.button("Edit", key=f"edit_entry_{entry.id}"):
                            # Load entry into form (including header fields)
                            st.session_state.editing_entry_id = entry.id
                            st.session_state.je_lines = [
                                {
                                    'account_id': line.account_id,
                                    'debit': line.debit,
                                    'credit': line.credit,
                                    'memo': line.memo or ''
                                }
                                for line in entry.lines
                            ]
                            st.session_state.je_entry_date = entry.entry_date
                            st.session_state.je_entry_type = entry.entry_type
                            st.session_state.je_source_reference = entry.source_reference or ''
                            st.session_state.je_description = entry.description or ''
                            st.rerun()

                        if st.button("Delete", key=f"delete_entry_{entry.id}"):
                            try:
                                JournalEntry.delete(entry.id, client_id=client_id)
                                st.success("Entry deleted!")
                                st.rerun()
                            except ValueError as e:
                                st.error(str(e))

with tab3:
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
