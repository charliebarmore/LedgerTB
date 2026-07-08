"""
ProBooks - Simple Double-Entry Bookkeeping System

A Streamlit-based accounting application for:
- Managing multiple clients with separate books
- Recording journal entries with double-entry validation
- Importing bank transactions from CSV
- AI-powered transaction categorization
- Generating financial reports (Trial Balance, Income Statement, Balance Sheet, General Ledger)

To run:
    streamlit run app.py

Configuration:
    Set ANTHROPIC_API_KEY environment variable for AI categorization features.
    Create a .env file in this directory with:
        ANTHROPIC_API_KEY=your-api-key-here
"""

import streamlit as st
import sys
from pathlib import Path

# Add current directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from database import init_database
from config import APP_NAME, ANTHROPIC_API_KEY
from models.client import Client
from models.account import Account
from models.journal_entry import JournalEntry
from utils.client_selector import render_client_selector

# Initialize database on startup
init_database()

# Page config
st.set_page_config(
    page_title=APP_NAME,
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Same persistent client selector + nav every other page shows, so the
# sidebar doesn't disappear/reappear when landing on or leaving Home.
selected_client_id = render_client_selector()

# When a client is selected, the Dashboard is the landing page for that client.
# The overview below is only shown before any client exists.
if selected_client_id:
    st.switch_page("pages/7_Dashboard.py")

# ---- Header ----
st.title(f"📚 {APP_NAME}")
st.caption("CPA-focused bookkeeping — trial balance worksheet, adjusting entries, and workpaper-ready reports.")

# ---- At-a-glance metrics ----
clients = Client.get_all()
# Count with COUNT(*) rather than hydrating every entry/account (the old
# get_all(limit=1000) both loaded thousands of objects and silently undercounted
# any client with more than 1000 entries).
total_entries = sum(JournalEntry.count(c.id) for c in clients)
total_accounts = sum(Account.count(c.id) for c in clients)

m1, m2, m3 = st.columns(3)
m1.metric("Clients", len(clients))
m2.metric("Journal Entries", total_entries)
m3.metric("Accounts", total_accounts)

st.divider()

# ---- Quick navigation ----
st.subheader("Jump to")
n1, n2, n3 = st.columns(3)
with n1:
    st.page_link("pages/1_Trial_Balance_Worksheet.py", label="Trial Balance Worksheet", icon="📊")
    st.page_link("pages/2_Journal_Entries.py", label="Journal Entries", icon="📝")
with n2:
    st.page_link("pages/4_Import_Transactions.py", label="Import Transactions", icon="📥")
    st.page_link("pages/3_Chart_of_Accounts.py", label="Chart of Accounts", icon="📒")
with n3:
    st.page_link("pages/5_Reports.py", label="Reports", icon="📄")
    st.page_link("pages/0_Clients.py", label="Clients", icon="👥")

st.divider()

# ---- Clients ----
# The create-client action lives in the sidebar (next to the client list); the
# home page just summarizes.
if clients:
    st.subheader("Your Clients")
    for c in clients[:5]:
        st.markdown(f"- {c.name}")
    if len(clients) > 5:
        st.caption(f"…and {len(clients) - 5} more")
    st.page_link("pages/0_Clients.py", label="Manage clients →")
else:
    st.caption("No clients yet — use **➕ Create your first client** in the sidebar to get started.")

# ---- AI status (compact, non-intrusive) ----
st.sidebar.divider()
if ANTHROPIC_API_KEY:
    st.sidebar.success("AI categorization: enabled")
else:
    st.sidebar.info("AI categorization: off")
    with st.expander("Enable AI transaction categorization"):
        st.markdown(
            "Add an `ANTHROPIC_API_KEY` to a `.env` file in the ProBooks folder, "
            "then restart the app. Pattern learning from your manual categorizations "
            "works without a key."
        )
