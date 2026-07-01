import streamlit as st
from typing import Optional
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.client import Client
from models.transaction import ImportedTransaction


# Narrow the sidebar (default is ~21rem). Uses width only — Streamlit collapses
# the sidebar via transform, so the native collapse chevron still works.
# Widened from 240px to 260px so nav labels like "Trial Balance Worksheet"
# and the client name don't get hard-clipped.
_SIDEBAR_CSS = """
<style>
section[data-testid="stSidebar"] {
    width: 260px !important;
    min-width: 260px !important;
    max-width: 260px !important;
}
section[data-testid="stSidebar"] > div:first-child { width: 260px !important; }

/* st.page_link labels were clipping mid-word with no ellipsis. */
section[data-testid="stSidebar"] [data-testid="stPageLink"] p {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

/* st.expander default styling (filled box + border) is visually identical to
   a selectbox, so collapsible sections ("Quick Reports", "+ Add New Account")
   read as dropdowns instead of expandable sections. Strip the box, keep a
   plain header with just a bottom rule so it reads as a section toggle. */
[data-testid="stExpander"] details {
    border: none !important;
    background: transparent !important;
}
[data-testid="stExpander"] summary {
    border: none !important;
    border-bottom: 1px solid #d8dee8 !important;
    border-radius: 0 !important;
    background: transparent !important;
    padding-left: 0 !important;
}
[data-testid="stExpander"] summary:hover {
    background: transparent !important;
    color: #1f3a5f !important;
}
[data-testid="stExpanderDetails"] {
    padding-left: 0 !important;
}
</style>
"""


def apply_sidebar_style():
    """Inject the narrow-sidebar CSS. Safe to call on every page."""
    st.markdown(_SIDEBAR_CSS, unsafe_allow_html=True)


def render_client_selector() -> Optional[int]:
    """
    Render the client selector in the sidebar with sub-navigation and return the selected client ID.
    Returns None if no clients exist.
    """
    apply_sidebar_style()

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

    # Client sub-navigation - CPA-focused workflow
    if selected_id:
        st.sidebar.divider()

        # Get pending count for badge
        pending_count = ImportedTransaction.get_pending_count(selected_id)

        # Primary navigation - CPA workflow order
        st.sidebar.page_link("pages/1_Trial_Balance_Worksheet.py", label="Trial Balance Worksheet", icon="📊")
        st.sidebar.page_link("pages/2_Journal_Entries.py", label="Journal Entries", icon="📝")
        st.sidebar.page_link("pages/3_Chart_of_Accounts.py", label="Chart of Accounts", icon="📋")

        # Import with pending badge
        import_label = "Import Transactions"
        if pending_count > 0:
            import_label = f"Import Transactions ({pending_count})"
        st.sidebar.page_link("pages/4_Import_Transactions.py", label=import_label, icon="📥")

        st.sidebar.page_link("pages/5_Reports.py", label="Reports", icon="📈")
        st.sidebar.page_link("pages/6_Transactions.py", label="Transactions", icon="💳")
        st.sidebar.page_link("pages/7_Dashboard.py", label="Dashboard", icon="🏠")
        st.sidebar.page_link("pages/8_Audit_Trail.py", label="Audit Trail", icon="📜")

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
