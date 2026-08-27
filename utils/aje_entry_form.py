"""
Reusable Adjusting Journal Entry (AJE) form component.

This utility provides a standardized form for creating adjusting journal entries
that can be used across multiple pages.
"""

import streamlit as st
from datetime import date
from typing import Optional, List, Callable

from models.journal_entry import JournalEntry, JournalEntryLine
from models.account import Account
from services.preferences import get_date_format


def render_aje_form(
    client_id: int,
    period_start: date,
    period_end: date,
    prefill_account_id: Optional[int] = None,
    on_save: Optional[Callable[[JournalEntry], None]] = None,
    on_cancel: Optional[Callable[[], None]] = None,
    form_key: str = "aje_form"
) -> Optional[JournalEntry]:
    """
    Render a form for creating an Adjusting Journal Entry.

    Args:
        client_id: The client ID
        period_start: Start of the period (for AJE reference numbering)
        period_end: End of the period (default entry date)
        prefill_account_id: Optional account ID to pre-fill in the first line
        on_save: Optional callback function called after successful save
        on_cancel: Optional callback function called when form is cancelled
        form_key: Unique key for the form (default: "aje_form")

    Returns:
        The saved JournalEntry if successful, None otherwise
    """
    # Get next AJE reference
    next_aje_ref = JournalEntry.get_next_aje_reference(client_id, period_start, period_end)

    # Get accounts for selection
    accounts = Account.get_all(client_id)
    account_options = {a.id: f"{a.account_number} - {a.name}" for a in accounts}

    if not account_options:
        st.warning("No accounts found. Please set up your Chart of Accounts first.")
        return None

    st.subheader("Add Adjusting Journal Entry")

    with st.form(form_key):
        # Header fields
        header_cols = st.columns([1, 2, 1])

        with header_cols[0]:
            aje_ref = st.text_input("AJE Reference", value=next_aje_ref, disabled=True)
            aje_date = st.date_input(
                "Date", value=period_end, format=get_date_format()
            )

        with header_cols[1]:
            aje_desc = st.text_input("Description", placeholder="Describe the adjusting entry...")

        with header_cols[2]:
            aje_source = st.text_input("Source Reference", placeholder="W/P Reference...")

        st.markdown("**Entry Lines**")

        # Column headers
        line_header_cols = st.columns([3, 2, 2, 2])
        with line_header_cols[0]:
            st.markdown("**Account**")
        with line_header_cols[1]:
            st.markdown("**Debit**")
        with line_header_cols[2]:
            st.markdown("**Credit**")
        with line_header_cols[3]:
            st.markdown("**Memo**")

        lines_data = []

        # Line 1 (required)
        line1_cols = st.columns([3, 2, 2, 2])
        with line1_cols[0]:
            default_idx = 0
            if prefill_account_id and prefill_account_id in account_options:
                default_idx = list(account_options.keys()).index(prefill_account_id)
            line1_account = st.selectbox(
                "Account 1",
                options=list(account_options.keys()),
                format_func=lambda x: account_options[x],
                index=default_idx,
                key=f"{form_key}_line1_acct",
                label_visibility="collapsed"
            )
        with line1_cols[1]:
            line1_debit = st.number_input(
                "Debit 1", min_value=0.0, step=0.01,
                key=f"{form_key}_line1_dr", label_visibility="collapsed"
            )
        with line1_cols[2]:
            line1_credit = st.number_input(
                "Credit 1", min_value=0.0, step=0.01,
                key=f"{form_key}_line1_cr", label_visibility="collapsed"
            )
        with line1_cols[3]:
            line1_memo = st.text_input(
                "Memo 1", key=f"{form_key}_line1_memo", label_visibility="collapsed"
            )
        lines_data.append((line1_account, line1_debit, line1_credit, line1_memo))

        # Line 2 (required)
        line2_cols = st.columns([3, 2, 2, 2])
        with line2_cols[0]:
            line2_account = st.selectbox(
                "Account 2",
                options=list(account_options.keys()),
                format_func=lambda x: account_options[x],
                index=0,
                key=f"{form_key}_line2_acct",
                label_visibility="collapsed"
            )
        with line2_cols[1]:
            line2_debit = st.number_input(
                "Debit 2", min_value=0.0, step=0.01,
                key=f"{form_key}_line2_dr", label_visibility="collapsed"
            )
        with line2_cols[2]:
            line2_credit = st.number_input(
                "Credit 2", min_value=0.0, step=0.01,
                key=f"{form_key}_line2_cr", label_visibility="collapsed"
            )
        with line2_cols[3]:
            line2_memo = st.text_input(
                "Memo 2", key=f"{form_key}_line2_memo", label_visibility="collapsed"
            )
        lines_data.append((line2_account, line2_debit, line2_credit, line2_memo))

        # Line 3 (optional)
        line3_cols = st.columns([3, 2, 2, 2])
        with line3_cols[0]:
            line3_account = st.selectbox(
                "Account 3",
                options=[None] + list(account_options.keys()),
                format_func=lambda x: account_options[x] if x else "(Optional)",
                index=0,
                key=f"{form_key}_line3_acct",
                label_visibility="collapsed"
            )
        with line3_cols[1]:
            line3_debit = st.number_input(
                "Debit 3", min_value=0.0, step=0.01,
                key=f"{form_key}_line3_dr", label_visibility="collapsed"
            )
        with line3_cols[2]:
            line3_credit = st.number_input(
                "Credit 3", min_value=0.0, step=0.01,
                key=f"{form_key}_line3_cr", label_visibility="collapsed"
            )
        with line3_cols[3]:
            line3_memo = st.text_input(
                "Memo 3", key=f"{form_key}_line3_memo", label_visibility="collapsed"
            )
        lines_data.append((line3_account, line3_debit, line3_credit, line3_memo))

        # Line 4 (optional)
        line4_cols = st.columns([3, 2, 2, 2])
        with line4_cols[0]:
            line4_account = st.selectbox(
                "Account 4",
                options=[None] + list(account_options.keys()),
                format_func=lambda x: account_options[x] if x else "(Optional)",
                index=0,
                key=f"{form_key}_line4_acct",
                label_visibility="collapsed"
            )
        with line4_cols[1]:
            line4_debit = st.number_input(
                "Debit 4", min_value=0.0, step=0.01,
                key=f"{form_key}_line4_dr", label_visibility="collapsed"
            )
        with line4_cols[2]:
            line4_credit = st.number_input(
                "Credit 4", min_value=0.0, step=0.01,
                key=f"{form_key}_line4_cr", label_visibility="collapsed"
            )
        with line4_cols[3]:
            line4_memo = st.text_input(
                "Memo 4", key=f"{form_key}_line4_memo", label_visibility="collapsed"
            )
        lines_data.append((line4_account, line4_debit, line4_credit, line4_memo))

        # Submit buttons
        submit_cols = st.columns([1, 1, 4])

        with submit_cols[0]:
            submitted = st.form_submit_button("Save AJE", type="primary")

        with submit_cols[1]:
            cancelled = st.form_submit_button("Cancel")

        if cancelled:
            if on_cancel:
                on_cancel()
            return None

        if submitted:
            # Build journal entry lines
            lines = []
            for account_id, debit, credit, memo in lines_data:
                if account_id and (debit > 0 or credit > 0):
                    lines.append(JournalEntryLine(
                        account_id=account_id,
                        debit=debit,
                        credit=credit,
                        memo=memo if memo else None
                    ))

            # Create the journal entry
            entry = JournalEntry(
                client_id=client_id,
                entry_date=aje_date,
                description=aje_desc,
                source_reference=aje_source if aje_source else None,
                entry_type='Adjusting',
                aje_reference=next_aje_ref,
                lines=lines
            )

            try:
                entry.save()
                st.success(f"AJE {next_aje_ref} saved successfully!")
                if on_save:
                    on_save(entry)
                return entry
            except ValueError as e:
                st.error(str(e))
                return None

    return None


def render_aje_quick_add(
    client_id: int,
    period_start: date,
    period_end: date,
    debit_account_id: int,
    credit_account_id: int,
    amount: float,
    description: str,
    source_reference: Optional[str] = None
) -> Optional[JournalEntry]:
    """
    Create an AJE with a simple debit/credit to two accounts.

    Args:
        client_id: The client ID
        period_start: Start of the period
        period_end: End of the period (entry date)
        debit_account_id: Account to debit
        credit_account_id: Account to credit
        amount: Amount to post
        description: Entry description
        source_reference: Optional source/workpaper reference

    Returns:
        The saved JournalEntry if successful, None otherwise
    """
    next_aje_ref = JournalEntry.get_next_aje_reference(client_id, period_start, period_end)

    entry = JournalEntry(
        client_id=client_id,
        entry_date=period_end,
        description=description,
        source_reference=source_reference,
        entry_type='Adjusting',
        aje_reference=next_aje_ref,
        lines=[
            JournalEntryLine(account_id=debit_account_id, debit=amount, credit=0),
            JournalEntryLine(account_id=credit_account_id, debit=0, credit=amount)
        ]
    )

    try:
        entry.save()
        return entry
    except ValueError:
        return None
