import streamlit as st
from typing import Optional
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.client import Client
from models.transaction import ImportedTransaction
from database import connection as db_connection
from services.backups import backup_health
from utils import icons


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
/* Streamlit puts a copy-anchor-URL link on every header; in a desktop app
   the URL is localhost noise, so hide them everywhere. */
[data-testid="stHeaderActionElements"] { display: none !important; }

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

/* Sidebar tertiary buttons ("Add client", Quick Reports) styled like the
   st.page_link nav entries around them: left-aligned, full row, subtle hover. */
section[data-testid="stSidebar"] button[data-testid="stBaseButton-tertiary"] {
    justify-content: flex-start;
    text-align: left;
    padding: 0.25rem 0.5rem;
    min-height: 2rem;
    font-weight: 400;
    color: inherit;
}
/* The button's inner flex wrapper centers its content; left-align it too. */
section[data-testid="stSidebar"] button[data-testid="stBaseButton-tertiary"] > div {
    justify-content: flex-start;
}
section[data-testid="stSidebar"] button[data-testid="stBaseButton-tertiary"]:hover {
    background: rgba(151, 166, 195, 0.15);
    color: inherit;
}

/* Streamlit's default caption gray misses WCAG AA on a white background. */
[data-testid="stCaptionContainer"] {
    color: #606773;
}

/* Streamlit renders the number-input "Press Enter to apply" hint INSIDE the
   input box, where it overlaps the typed value in narrow columns (e.g. the
   journal entry line grid). Repositioning it proved fragile — its absolute
   positioning context shifts with page structure — so it is hidden for
   number inputs; the entry form carries a permanent caption explaining that
   values apply on Enter/focus-out. Text inputs keep their native hint. */
[data-testid="stNumberInput"] div:has(> [data-testid="InputInstructions"]) {
    display: none;
}
</style>
"""


_ASSETS_DIR = Path(__file__).parent.parent / "assets"


def apply_sidebar_style():
    """Inject the narrow-sidebar CSS and the app logo. Safe to call on every page."""
    wordmark = _ASSETS_DIR / "probooks-wordmark.png"
    if wordmark.exists():
        st.logo(str(wordmark), size="large",
                icon_image=str(_ASSETS_DIR / "probooks-mark.png"))
    st.html(_SIDEBAR_CSS)


def render_safety_status():
    """Keep encryption and backup posture visible without dominating navigation."""
    health = backup_health()
    encryption_ready = db_connection.ENCRYPTION_AVAILABLE
    needs_attention = not encryption_ready or not health["healthy"]
    label = "Data Safety (action needed)" if needs_attention else "Data Safety"
    st.sidebar.page_link("pages/9_Data_Safety.py", label=label, icon=icons.SECURITY)
    if needs_attention:
        reason = "Encryption is off." if not encryption_ready else health["reason"]
        st.sidebar.caption(f"Safety: {reason}")
    st.sidebar.page_link("pages/12_Firm_Settings.py", label="Firm Settings", icon=icons.FIRM)


def render_client_selector() -> Optional[int]:
    """
    Render the client selector in the sidebar with sub-navigation and return the selected client ID.
    Returns None if no clients exist.
    """
    apply_sidebar_style()

    clients = Client.get_all(active_only=True)

    if not clients:
        st.sidebar.warning("No clients yet.")
        if st.sidebar.button(f"{icons.ADD_CLIENT} Create your first client",
                             key="nav_create_first_client", type="tertiary", width="stretch"):
            st.session_state['clients_view'] = "Add Client"
            st.switch_page("pages/0_Clients.py")
        st.sidebar.divider()
        render_safety_status()
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

    # Add-client affordance right where the clients are listed. A button rather
    # than a page_link so it can land on the Add Client view directly — a
    # page_link can only open the page's default (View Clients) view.
    if st.sidebar.button(f"{icons.ADD_CLIENT} Add client",
                         key="nav_add_client", type="tertiary", width="stretch"):
        st.session_state['clients_view'] = "Add Client"
        st.switch_page("pages/0_Clients.py")

    # Client sub-navigation - CPA-focused workflow
    if selected_id:
        st.sidebar.divider()

        # Get pending count for badge
        pending_count = ImportedTransaction.get_pending_count(selected_id)

        # Primary navigation - Dashboard first (client landing), then CPA workflow
        st.sidebar.page_link("pages/7_Dashboard.py", label="Dashboard", icon=icons.DASHBOARD)
        st.sidebar.page_link("pages/1_Trial_Balance_Worksheet.py", label="Trial Balance Worksheet", icon=icons.TRIAL_BALANCE)
        je_label = "Journal Entries"
        try:
            from models.draft_entry import DraftEntry as _DraftEntry
            _drafts = _DraftEntry.pending_count(selected_id)
            if _drafts:
                je_label = f"Journal Entries ({_drafts} draft{'s' if _drafts != 1 else ''})"
        except Exception:
            pass  # pre-migration database: no drafts table yet
        st.sidebar.page_link("pages/2_Journal_Entries.py", label=je_label, icon=icons.JOURNAL_ENTRIES)
        st.sidebar.page_link("pages/3_Chart_of_Accounts.py", label="Chart of Accounts", icon=icons.CHART_OF_ACCOUNTS)

        # Import with pending badge
        import_label = "Import Transactions"
        if pending_count > 0:
            import_label = f"Import Transactions ({pending_count})"
        st.sidebar.page_link("pages/4_Import_Transactions.py", label=import_label, icon=icons.IMPORT)

        st.sidebar.page_link("pages/5_Reports.py", label="Reports", icon=icons.REPORTS)
        st.sidebar.page_link("pages/6_Transactions.py", label="Transactions", icon=icons.TRANSACTIONS)
        st.sidebar.page_link("pages/10_Bank_Reconciliation.py", label="Bank Reconciliation", icon=icons.RECONCILIATION)
        st.sidebar.page_link("pages/11_Book_Review.py", label="Book Review", icon=icons.REVIEW)
        ar_label = "Assistant Review"
        try:
            from models import assistant_review as _ar
            _unreviewed = _ar.unreviewed_count(selected_id)
            if _unreviewed:
                ar_label = f"Assistant Review ({_unreviewed})"
        except Exception:
            pass  # pre-migration database: no review-marks table yet
        st.sidebar.page_link("pages/13_Assistant_Review.py", label=ar_label, icon=icons.ASSISTANT)
        st.sidebar.page_link("pages/8_Audit_Trail.py", label="Audit Trail", icon=icons.AUDIT_TRAIL)
        render_safety_status()

        # Quick report links (collapsible)
        with st.sidebar.expander("Quick Reports"):
            if st.button("Trial Balance", key="qr_tb", type="tertiary", width="stretch"):
                st.session_state.active_report = "Trial Balance"
                st.switch_page("pages/5_Reports.py")
            if st.button("Income Statement", key="qr_is", type="tertiary", width="stretch"):
                st.session_state.active_report = "Income Statement"
                st.switch_page("pages/5_Reports.py")
            if st.button("Balance Sheet", key="qr_bs", type="tertiary", width="stretch"):
                st.session_state.active_report = "Balance Sheet"
                st.switch_page("pages/5_Reports.py")
            if st.button("General Ledger", key="qr_gl", type="tertiary", width="stretch"):
                st.session_state.active_report = "General Ledger"
                st.switch_page("pages/5_Reports.py")

        st.sidebar.divider()
        st.sidebar.page_link("pages/0_Clients.py", label="Manage Clients", icon=icons.CLIENTS)

    return selected_id


def get_selected_client() -> Optional[int]:
    """
    Get the currently selected client ID from session state.
    Returns None if no client is selected.
    """
    return st.session_state.get('selected_client_id', None)
