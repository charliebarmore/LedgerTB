import streamlit as st
from typing import Optional
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.client import Client
from models.transaction import ImportedTransaction


def render_client_selector() -> Optional[int]:
    """
    Render the client selector in the sidebar with sub-navigation and return the selected client ID.
    Returns None if no clients exist.
    """
    clients = Client.get_all(active_only=True)

    if not clients:
        st.sidebar.warning("No clients found. Create one first.")
        return None

    # Build options dict
    client_options = {c.id: c.name for c in clients}

    # Get current selection from session state
    if 'selected_client_id' not in st.session_state:
        st.session_state.selected_client_id = clients[0].id

    # Validate current selection still exists
    if st.session_state.selected_client_id not in client_options:
        st.session_state.selected_client_id = clients[0].id

    # Render selector
    selected_id = st.sidebar.selectbox(
        "Select Client",
        options=list(client_options.keys()),
        format_func=lambda x: client_options[x],
        index=list(client_options.keys()).index(st.session_state.selected_client_id),
        key="client_selector"
    )

    # Update session state
    st.session_state.selected_client_id = selected_id

    # Client sub-navigation
    if selected_id:
        st.sidebar.divider()

        # Get pending count for badge
        pending_count = ImportedTransaction.get_pending_count(selected_id)

        # Navigation links
        st.sidebar.page_link("pages/1_Dashboard.py", label="Dashboard", icon="📊")
        st.sidebar.page_link("pages/2_Chart_of_Accounts.py", label="Chart of Accounts", icon="📋")
        st.sidebar.page_link("pages/3_Journal_Entries.py", label="Journal Entries", icon="📝")
        st.sidebar.page_link("pages/6_Transactions.py", label="Transactions", icon="💳")

        # Import with pending badge
        import_label = "Import Transactions"
        if pending_count > 0:
            import_label = f"Import Transactions ({pending_count})"
        st.sidebar.page_link("pages/4_Import_Transactions.py", label=import_label, icon="📥")

        st.sidebar.page_link("pages/5_Reports.py", label="Reports", icon="📈")

        # Quick report links (collapsible)
        with st.sidebar.expander("Quick Reports"):
            if st.button("Trial Balance", key="qr_tb", use_container_width=True):
                st.session_state.active_report = "Trial Balance"
                st.switch_page("pages/5_Reports.py")
            if st.button("Income Statement", key="qr_is", use_container_width=True):
                st.session_state.active_report = "Income Statement"
                st.switch_page("pages/5_Reports.py")
            if st.button("Balance Sheet", key="qr_bs", use_container_width=True):
                st.session_state.active_report = "Balance Sheet"
                st.switch_page("pages/5_Reports.py")
            if st.button("General Ledger", key="qr_gl", use_container_width=True):
                st.session_state.active_report = "General Ledger"
                st.switch_page("pages/5_Reports.py")

        st.sidebar.divider()
        st.sidebar.page_link("pages/0_Clients.py", label="Manage Clients", icon="👥")

    return selected_id


def get_selected_client() -> Optional[int]:
    """
    Get the currently selected client ID from session state.
    Returns None if no client is selected.
    """
    return st.session_state.get('selected_client_id', None)
