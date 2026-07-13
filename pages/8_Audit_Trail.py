"""Audit Trail - View accounting changes and sensitive operational events."""

import streamlit as st
import sys
from pathlib import Path
from datetime import date, datetime

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from database import init_database
from utils.client_selector import render_client_selector, get_selected_client
from utils import icons
from models.client import Client
from models.audit_log import AuditLog
from utils.fiscal_dates import fiscal_year_bounds

init_database()

st.set_page_config(
    page_title="Audit Trail",
    page_icon=icons.AUDIT_TRAIL,
    layout="wide"
)

# Render client selector
client_id = render_client_selector()

if not client_id:
    st.warning("Please select or create a client first.")
    st.stop()

client = Client.get_by_id(client_id)
if not client:
    st.error("Client not found.")
    st.stop()

st.title("Audit Trail")
st.caption(f"Viewing: **{client.name}**")

# Filters
st.markdown("---")

filter_cols = st.columns([2, 2, 1, 1, 2])

# Default "From Date" to the client's earliest activity rather than a fixed
# 30-day window, so older audit history isn't hidden on first view.
earliest_activity = AuditLog.get_earliest_date(client_id)
default_start_date = earliest_activity.date() if earliest_activity else fiscal_year_bounds(
    date.today(), client.fiscal_year_end_month
)[0]

with filter_cols[0]:
    start_date = st.date_input(
        "From Date",
        value=default_start_date,
        key="audit_start_date"
    )

with filter_cols[1]:
    end_date = st.date_input(
        "To Date",
        value=date.today(),
        key="audit_end_date"
    )

with filter_cols[2]:
    table_filter = st.selectbox(
        "Table",
        options=[
            "All", "clients", "accounts", "journal_entries",
            "imported_transactions", "categorization_rules", "fiscal_periods",
            "bank_reconciliations", "transactions_export", "trial_balance_export",
            "trial_balance_worksheet_export", "adjusted_trial_balance_export",
            "income_statement_export", "balance_sheet_export", "general_ledger_export",
            "database_backup", "database_restore",
        ],
        index=0,
        key="table_filter"
    )

with filter_cols[3]:
    action_filter = st.selectbox(
        "Action",
        options=[
            "All", "INSERT", "UPDATE", "DELETE", "REVERSE", "CLOSE",
            "REOPEN", "EXPORT", "BACKUP", "RESTORE", "OVERRIDE",
        ],
        index=0,
        key="action_filter"
    )

with filter_cols[4]:
    search_term = st.text_input(
        "Search",
        placeholder="Search in values...",
        key="audit_search"
    )

if start_date > end_date:
    st.error("Audit filter start date cannot be after the end date.")
    st.stop()

# Apply filters
start_datetime = datetime.combine(start_date, datetime.min.time())
end_datetime = datetime.combine(end_date, datetime.max.time())
table_param = table_filter if table_filter != "All" else None
action_param = action_filter if action_filter != "All" else None
search_param = search_term if search_term else None

filter_signature = (start_date, end_date, table_param, action_param, search_param)
if st.session_state.get("audit_filter_signature") != filter_signature:
    st.session_state.audit_filter_signature = filter_signature
    st.session_state.audit_page = 1

page_size = 50
summary = AuditLog.get_filtered_counts(
    client_id=client_id,
    start_date=start_datetime,
    end_date=end_datetime,
    table_name=table_param,
    action=action_param,
    search_term=search_param,
)
page_count = max(1, (summary["total"] + page_size - 1) // page_size)
current_page = min(max(1, st.session_state.get("audit_page", 1)), page_count)
st.session_state.audit_page = current_page

logs = AuditLog.get_all(
    client_id=client_id,
    start_date=start_datetime,
    end_date=end_datetime,
    table_name=table_param,
    action=action_param,
    search_term=search_param,
    limit=page_size,
    offset=(current_page - 1) * page_size,
)

st.markdown("---")

nav_left, nav_status, nav_right = st.columns([1, 2, 1])
with nav_left:
    if st.button("Previous", disabled=current_page <= 1, key="audit_previous"):
        st.session_state.audit_page = current_page - 1
        st.rerun()
with nav_status:
    first_row = (current_page - 1) * page_size + 1 if summary["total"] else 0
    last_row = min(current_page * page_size, summary["total"])
    st.caption(
        f"Page {current_page} of {page_count} · showing {first_row}–{last_row} "
        f"of {summary['total']}"
    )
with nav_right:
    if st.button("Next", disabled=current_page >= page_count, key="audit_next"):
        st.session_state.audit_page = current_page + 1
        st.rerun()

if not logs:
    st.info("No audit log entries found for the selected filters.")
else:
    st.write(f"Showing {len(logs)} audit log entries on this page")

    # Display logs
    for log in logs:
        # Create an expandable section for each log entry
        action_label = {
            "INSERT": "Created",
            "UPDATE": "Updated",
            "DELETE": "Deleted",
            "REVERSE": "Reversed",
            "CLOSE": "Closed",
            "REOPEN": "Reopened",
            "EXPORT": "Exported",
            "BACKUP": "Backed up",
            "RESTORE": "Restored",
            "OVERRIDE": "Overrode duplicate warning for",
        }.get(log.action, log.action.title())

        header = f"{action_label} · {log.table_name} #{log.record_id}"
        if log.changed_at:
            header += f" - {log.changed_at.strftime('%m/%d/%Y %H:%M:%S')}"

        with st.expander(header, expanded=False):
            cols = st.columns([1, 1])

            with cols[0]:
                st.markdown("**Details:**")
                st.text(f"Table: {log.table_name}")
                st.text(f"Record ID: {log.record_id}")
                st.text(f"Action: {log.action}")
                if log.changed_at:
                    st.text(f"Changed At: {log.changed_at.strftime('%m/%d/%Y %H:%M:%S')}")
                if log.session_id:
                    st.text(f"Session: {log.session_id[:8]}...")

            with cols[1]:
                if log.action == "INSERT":
                    st.markdown("**New Values:**")
                    if log.new_values:
                        for key, value in log.new_values.items():
                            st.text(f"  {key}: {value}")
                    else:
                        st.text("  (no values recorded)")

                elif log.action == "UPDATE":
                    st.markdown("**Changes:**")
                    if log.old_values and log.new_values:
                        all_keys = set(list(log.old_values.keys()) + list(log.new_values.keys()))
                        for key in sorted(all_keys):
                            old_val = log.old_values.get(key)
                            new_val = log.new_values.get(key)
                            if old_val != new_val:
                                st.text(f"  {key}:")
                                st.text(f"    Before: {old_val}")
                                st.text(f"    After: {new_val}")
                    else:
                        st.text("  (no change details recorded)")

                elif log.action == "DELETE":
                    st.markdown("**Deleted Values:**")
                    if log.old_values:
                        for key, value in log.old_values.items():
                            st.text(f"  {key}: {value}")
                    else:
                        st.text("  (no values recorded)")

                else:
                    st.markdown("**Event Details:**")
                    if log.old_values:
                        st.markdown("Before")
                        for key, value in log.old_values.items():
                            st.text(f"  {key}: {value}")
                    if log.new_values:
                        st.markdown("After / details")
                        for key, value in log.new_values.items():
                            st.text(f"  {key}: {value}")
                    if not log.old_values and not log.new_values:
                        st.text("  (no values recorded)")

            # Link to view the entry if it still exists (for INSERT and UPDATE)
            if log.action != "DELETE" and log.table_name == "journal_entries":
                st.markdown("---")
                if st.button(f"View Entry #{log.record_id}", key=f"view_{log.id}"):
                    st.session_state.view_entry_id = log.record_id
                    st.switch_page("pages/2_Journal_Entries.py")

    # Summary statistics
    st.markdown("---")
    st.subheader("Summary")

    summary_cols = st.columns(5)

    with summary_cols[0]:
        st.metric("Total Changes", summary["total"])

    with summary_cols[1]:
        st.metric("Inserts", summary["inserts"])

    with summary_cols[2]:
        st.metric("Updates", summary["updates"])

    with summary_cols[3]:
        st.metric("Deletes", summary["deletes"])

    with summary_cols[4]:
        st.metric("Other Events", summary["events"])
