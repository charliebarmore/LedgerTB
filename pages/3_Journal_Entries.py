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

# Initialize database
init_database()

st.set_page_config(page_title="Journal Entries", page_icon="📝", layout="wide")

# Client selector in sidebar
client_id = render_client_selector()

st.title("📝 Journal Entries")

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


def reset_entry_form():
    st.session_state.je_lines = [
        {'account_id': 0, 'debit': 0.0, 'credit': 0.0, 'memo': ''},
        {'account_id': 0, 'debit': 0.0, 'credit': 0.0, 'memo': ''}
    ]
    st.session_state.editing_entry_id = None


# Tabs
tab1, tab2 = st.tabs(["New Entry", "View Entries"])

with tab1:
    st.subheader("Create Journal Entry" if not st.session_state.editing_entry_id else "Edit Journal Entry")

    # Get all active accounts for dropdown
    accounts = Account.get_all(client_id, active_only=True)
    account_options = {0: "-- Select Account --"}
    account_options.update({a.id: a.display_name() for a in accounts})

    # Entry header
    col1, col2, col3 = st.columns(3)

    with col1:
        entry_date = st.date_input("Date", value=date.today())

    with col2:
        entry_type = st.selectbox("Entry Type", options=['Regular', 'Adjusting', 'Closing'])

    with col3:
        source_reference = st.text_input("Source Reference", placeholder="e.g., Bank stmt pg 3")

    description = st.text_input("Description", placeholder="Description of the entry")

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
                if st.button("🗑️", key=f"delete_line_{i}"):
                    st.session_state.je_lines.pop(i)
                    st.rerun()

    # Add line button
    if st.button("➕ Add Line"):
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

    # Filters
    col1, col2, col3 = st.columns(3)

    with col1:
        filter_start = st.date_input("From Date", value=date.today() - timedelta(days=30), key="filter_start")

    with col2:
        filter_end = st.date_input("To Date", value=date.today(), key="filter_end")

    with col3:
        filter_type = st.selectbox("Entry Type", options=['All', 'Regular', 'Adjusting', 'Closing'], key="filter_type")

    # Get entries
    entries = JournalEntry.get_all(
        client_id=client_id,
        start_date=filter_start,
        end_date=filter_end,
        entry_type=filter_type if filter_type != 'All' else None
    )

    if not entries:
        st.info("No journal entries found for the selected filters.")
    else:
        for entry in entries:
            with st.expander(
                f"**#{entry.id}** | {entry.entry_date} | {entry.description or 'No description'} | "
                f"${entry.total_debits():,.2f}"
            ):
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
                        # Load entry into form
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
                        st.rerun()

                    if st.button("Delete", key=f"delete_entry_{entry.id}"):
                        JournalEntry.delete(entry.id)
                        st.success("Entry deleted!")
                        st.rerun()
