"""
Audit Trail - View history of all changes to journal entries

This page displays the audit log with filters for:
- Date range
- Entry type (Journal Entries, etc.)
- Action type (INSERT, UPDATE, DELETE)
- Search by description or reference
"""

import streamlit as st
import sys
from pathlib import Path
from datetime import date, datetime, timedelta

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from database import init_database
from utils.client_selector import render_client_selector, get_selected_client
from utils import icons
from models.client import Client
from models.audit_log import AuditLog

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
default_start_date = earliest_activity.date() if earliest_activity else date.today() - timedelta(days=30)

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
        options=["All", "journal_entries"],
        index=0,
        key="table_filter"
    )

with filter_cols[3]:
    action_filter = st.selectbox(
        "Action",
        options=["All", "INSERT", "UPDATE", "DELETE"],
        index=0,
        key="action_filter"
    )

with filter_cols[4]:
    search_term = st.text_input(
        "Search",
        placeholder="Search in values...",
        key="audit_search"
    )

# Apply filters
start_datetime = datetime.combine(start_date, datetime.min.time())
end_datetime = datetime.combine(end_date, datetime.max.time())

logs = AuditLog.get_all(
    client_id=client_id,
    start_date=start_datetime,
    end_date=end_datetime,
    table_name=table_filter if table_filter != "All" else None,
    action=action_filter if action_filter != "All" else None,
    search_term=search_term if search_term else None,
    limit=500
)

st.markdown("---")

if not logs:
    st.info("No audit log entries found for the selected filters.")
else:
    st.write(f"Showing {len(logs)} audit log entries")

    # Display logs
    for log in logs:
        # Create an expandable section for each log entry
        action_label = {
            "INSERT": "Created",
            "UPDATE": "Updated",
            "DELETE": "Deleted",
        }.get(log.action, log.action.title())

        action_color = {
            "INSERT": "green",
            "UPDATE": "orange",
            "DELETE": "red"
        }.get(log.action, "gray")

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

            # Link to view the entry if it still exists (for INSERT and UPDATE)
            if log.action != "DELETE" and log.table_name == "journal_entries":
                st.markdown("---")
                if st.button(f"View Entry #{log.record_id}", key=f"view_{log.id}"):
                    st.session_state.view_entry_id = log.record_id
                    st.switch_page("pages/2_Journal_Entries.py")

    # Summary statistics
    st.markdown("---")
    st.subheader("Summary")

    summary_cols = st.columns(4)

    insert_count = sum(1 for l in logs if l.action == "INSERT")
    update_count = sum(1 for l in logs if l.action == "UPDATE")
    delete_count = sum(1 for l in logs if l.action == "DELETE")

    with summary_cols[0]:
        st.metric("Total Changes", len(logs))

    with summary_cols[1]:
        st.metric("Inserts", insert_count)

    with summary_cols[2]:
        st.metric("Updates", update_count)

    with summary_cols[3]:
        st.metric("Deletes", delete_count)
