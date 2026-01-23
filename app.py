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
from config import APP_NAME
from models.client import Client
from models.account import Account
from models.journal_entry import JournalEntry

# Initialize database on startup
init_database()

# Page config
st.set_page_config(
    page_title=APP_NAME,
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Main page content
st.title(f"📚 {APP_NAME}")

st.markdown("""
Welcome to your accounting system. Use the sidebar to navigate between pages.

### Getting Started

1. **Clients** - Add your clients (or yourself as a client)
2. **Chart of Accounts** - Review and customize accounts for each client
3. **Journal Entries** - Record transactions manually
4. **Import Transactions** - Upload bank CSV files for bulk entry
5. **Reports** - Generate Trial Balance, Income Statement, Balance Sheet, and General Ledger

### Features

- **Multi-Client Support** - Manage separate books for each client
- **Double-Entry Validation** - All entries must balance (debits = credits)
- **AI Categorization** - Claude suggests account categories for imported transactions
- **Pattern Learning** - The system learns from your categorizations (per client)
- **Excel Export** - Download all reports as Excel files
- **Source References** - Track audit documentation for each entry
""")

# Quick status
st.divider()

clients = Client.get_all()
clients_count = len(clients)

col1, col2, col3 = st.columns(3)

with col1:
    st.metric("Clients", clients_count)

with col2:
    total_entries = 0
    for c in clients:
        entries = JournalEntry.get_all(c.id, limit=1000)
        total_entries += len(entries)
    st.metric("Total Journal Entries", total_entries)

with col3:
    total_accounts = 0
    for c in clients:
        accounts = Account.get_all(c.id)
        total_accounts += len(accounts)
    st.metric("Total Accounts", total_accounts)

st.divider()

# Show clients
if clients:
    st.subheader("Your Clients")
    for c in clients[:5]:
        st.text(f"  • {c.name}")
    if len(clients) > 5:
        st.caption(f"  ... and {len(clients) - 5} more")
    st.page_link("pages/0_Clients.py", label="Manage Clients →")
else:
    st.info("No clients yet. Create your first client to get started.")
    st.page_link("pages/0_Clients.py", label="Create First Client →")

st.divider()

st.markdown("""
### Configuration

To enable AI-powered transaction categorization, set your Anthropic API key:

1. Create a file named `.env` in the `accounting_app` folder
2. Add the line: `ANTHROPIC_API_KEY=your-api-key-here`
3. Restart the application

Without the API key, the pattern learning feature will still work based on your manual categorizations.
""")

# Sidebar
st.sidebar.success("Select a page above to get started.")
