import streamlit as st
import sys
from pathlib import Path
from datetime import date, timedelta

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.client import Client
from models.account import Account
from models.journal_entry import JournalEntry
from models.transaction import ImportedTransaction
from models.reports import ReportGenerator
from database import init_database
from utils.client_selector import render_client_selector
from utils import icons

# Initialize database
init_database()

st.set_page_config(page_title="Dashboard", page_icon=icons.DASHBOARD, layout="wide")

# Client selector in sidebar
client_id = render_client_selector()

st.title("Dashboard")

if not client_id:
    st.warning("Please create a client first in the Clients page.")
    st.page_link("pages/0_Clients.py", label="Go to Clients →")
    st.stop()

# Get client info
client = Client.get_by_id(client_id)
st.caption(f"Viewing: **{client.name}**")

# Get data for metrics
accounts = Account.get_all(client_id, active_only=True)
pending_count = ImportedTransaction.get_pending_count(client_id)
recent_entries = JournalEntry.get_all(client_id, limit=5)

# Trial balance for totals
tb_rows = ReportGenerator.trial_balance(client_id)
asset_accounts = [(r.account_number, r.account_name, r.debit - r.credit) for r in tb_rows if r.account_type == 'Asset']
liability_accounts = [(r.account_number, r.account_name, r.credit - r.debit) for r in tb_rows if r.account_type == 'Liability']
equity_accounts = [(r.account_number, r.account_name, r.credit - r.debit) for r in tb_rows if r.account_type == 'Equity']

total_assets = sum(bal for _, _, bal in asset_accounts)
total_liabilities = sum(bal for _, _, bal in liability_accounts)
total_equity = sum(bal for _, _, bal in equity_accounts)

# Income statement for YTD
today = date.today()
year_start = date(today.year, 1, 1)
income_report = ReportGenerator.income_statement(client_id, year_start, today)

# Initialize expanded state
if 'dashboard_expanded' not in st.session_state:
    st.session_state.dashboard_expanded = None

# Quick stats row - clickable cards. These render with the same flat styling
# as a static st.metric, so a chevron is appended to signal they expand a
# detail panel on click (▾ collapsed / ▴ expanded) -- otherwise nothing about
# them reads as interactive.
def _toggle_icon(section: str) -> str:
    return "▴" if st.session_state.dashboard_expanded == section else "▾"


col1, col2, col3, col4 = st.columns(4)

with col1:
    if st.button(
        f"**Total Assets** {_toggle_icon('assets')}\n\n${total_assets:,.2f}",
        key="btn_assets",
        use_container_width=True,
        type="secondary" if st.session_state.dashboard_expanded != 'assets' else "primary"
    ):
        st.session_state.dashboard_expanded = 'assets' if st.session_state.dashboard_expanded != 'assets' else None
        st.rerun()

with col2:
    if st.button(
        f"**Total Liabilities** {_toggle_icon('liabilities')}\n\n${total_liabilities:,.2f}",
        key="btn_liabilities",
        use_container_width=True,
        type="secondary" if st.session_state.dashboard_expanded != 'liabilities' else "primary"
    ):
        st.session_state.dashboard_expanded = 'liabilities' if st.session_state.dashboard_expanded != 'liabilities' else None
        st.rerun()

with col3:
    if st.button(
        f"**YTD Net Income** {_toggle_icon('income')}\n\n${income_report['net_income']:,.2f}",
        key="btn_income",
        use_container_width=True,
        type="secondary" if st.session_state.dashboard_expanded != 'income' else "primary"
    ):
        st.session_state.dashboard_expanded = 'income' if st.session_state.dashboard_expanded != 'income' else None
        st.rerun()

with col4:
    if pending_count > 0:
        if st.button(
            f"**Pending Imports** →\n\n{pending_count} (Action needed)",
            key="btn_pending",
            use_container_width=True,
            type="secondary"
        ):
            st.switch_page("pages/4_Import_Transactions.py")
    else:
        st.button(
            f"**Pending Imports**\n\n0 (All clear)",
            key="btn_pending",
            use_container_width=True,
            type="secondary",
            disabled=True
        )

# Show expanded details
if st.session_state.dashboard_expanded == 'assets':
    st.markdown("---")
    st.subheader("Asset Breakdown")
    if asset_accounts:
        for acct_num, acct_name, balance in sorted(asset_accounts, key=lambda x: -x[2]):
            col_a, col_b = st.columns([4, 1])
            with col_a:
                st.text(f"{acct_num} - {acct_name}")
            with col_b:
                st.text(f"${balance:,.2f}")
        st.markdown(f"**Total: ${total_assets:,.2f}**")
    else:
        st.info("No assets with balances")

elif st.session_state.dashboard_expanded == 'liabilities':
    st.markdown("---")
    st.subheader("Liability Breakdown")
    if liability_accounts:
        for acct_num, acct_name, balance in sorted(liability_accounts, key=lambda x: -x[2]):
            col_a, col_b = st.columns([4, 1])
            with col_a:
                st.text(f"{acct_num} - {acct_name}")
            with col_b:
                st.text(f"${balance:,.2f}")
        st.markdown(f"**Total: ${total_liabilities:,.2f}**")
    else:
        st.info("No liabilities with balances")

elif st.session_state.dashboard_expanded == 'income':
    st.markdown("---")
    col_rev, col_exp = st.columns(2)

    with col_rev:
        st.subheader("Revenue")
        if income_report['revenues']:
            for r in income_report['revenues']:
                col_a, col_b = st.columns([3, 1])
                with col_a:
                    st.text(f"{r['account_number']} - {r['name']}")
                with col_b:
                    st.text(f"${r['balance']:,.2f}")
            st.markdown(f"**Total Revenue: ${income_report['total_revenue']:,.2f}**")
        else:
            st.info("No revenue recorded")

    with col_exp:
        st.subheader("Expenses")
        if income_report['expenses']:
            for e in income_report['expenses']:
                col_a, col_b = st.columns([3, 1])
                with col_a:
                    st.text(f"{e['account_number']} - {e['name']}")
                with col_b:
                    st.text(f"${e['balance']:,.2f}")
            st.markdown(f"**Total Expenses: ${income_report['total_expenses']:,.2f}**")
        else:
            st.info("No expenses recorded")

    st.markdown("---")
    color = "green" if income_report['net_income'] >= 0 else "red"
    st.markdown(f"### Net Income: :{color}[${income_report['net_income']:,.2f}]")

st.divider()

# Two column layout
col1, col2 = st.columns(2)

with col1:
    st.subheader("Recent Journal Entries")

    if not recent_entries:
        st.info("No journal entries yet. Create one to get started.")
    else:
        for entry in recent_entries:
            with st.container():
                col_a, col_b = st.columns([3, 1])
                with col_a:
                    st.markdown(f"**#{entry.id}** - {entry.entry_date}")
                    st.caption(entry.description or "No description")
                with col_b:
                    st.text(f"${entry.total_debits():,.2f}")
                st.divider()

    st.page_link("pages/2_Journal_Entries.py", label="View all entries", icon=icons.JOURNAL_ENTRIES)

with col2:
    st.subheader("Quick Actions")

    st.page_link("pages/2_Journal_Entries.py", label="New journal entry", icon=icons.JOURNAL_ENTRIES)
    st.page_link("pages/4_Import_Transactions.py", label="Import transactions", icon=icons.IMPORT)
    st.page_link("pages/10_Bank_Reconciliation.py", label="Reconcile accounts", icon=icons.RECONCILIATION)
    st.page_link("pages/5_Reports.py", label="Generate reports", icon=icons.REPORTS)
    st.page_link("pages/3_Chart_of_Accounts.py", label="Manage accounts", icon=icons.CHART_OF_ACCOUNTS)

st.divider()

# Account balances summary
st.subheader("Account Balances Summary")

# Get balances by type
balance_data = {
    'Assets': [],
    'Liabilities': [],
    'Revenue': [],
    'Expenses': []
}

for account in accounts:
    balance = Account.get_balance(account.id)
    if abs(balance) > 0.01:
        if account.type == 'Asset':
            balance_data['Assets'].append((account.display_name(), balance))
        elif account.type == 'Liability':
            balance_data['Liabilities'].append((account.display_name(), balance))
        elif account.type == 'Revenue':
            balance_data['Revenue'].append((account.display_name(), balance))
        elif account.type == 'Expense':
            balance_data['Expenses'].append((account.display_name(), balance))

col1, col2 = st.columns(2)

with col1:
    st.markdown("**Assets**")
    if balance_data['Assets']:
        for name, bal in sorted(balance_data['Assets'], key=lambda x: -x[1])[:5]:
            col_a, col_b = st.columns([3, 1])
            with col_a:
                st.text(name[:35])
            with col_b:
                st.text(f"${bal:,.2f}")
    else:
        st.caption("No assets with balances")

    st.markdown("**Liabilities**")
    if balance_data['Liabilities']:
        for name, bal in sorted(balance_data['Liabilities'], key=lambda x: -x[1])[:5]:
            col_a, col_b = st.columns([3, 1])
            with col_a:
                st.text(name[:35])
            with col_b:
                st.text(f"${bal:,.2f}")
    else:
        st.caption("No liabilities with balances")

with col2:
    st.markdown("**Revenue (YTD)**")
    if balance_data['Revenue']:
        for name, bal in sorted(balance_data['Revenue'], key=lambda x: -x[1])[:5]:
            col_a, col_b = st.columns([3, 1])
            with col_a:
                st.text(name[:35])
            with col_b:
                st.text(f"${bal:,.2f}")
    else:
        st.caption("No revenue recorded")

    st.markdown("**Expenses (YTD)**")
    if balance_data['Expenses']:
        for name, bal in sorted(balance_data['Expenses'], key=lambda x: -x[1])[:5]:
            col_a, col_b = st.columns([3, 1])
            with col_a:
                st.text(name[:35])
            with col_b:
                st.text(f"${bal:,.2f}")
    else:
        st.caption("No expenses recorded")
