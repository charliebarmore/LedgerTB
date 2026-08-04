import streamlit as st
import sys
from pathlib import Path
from datetime import date

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.client import Client
from models.account import Account
from models.transaction import ImportedTransaction
from models.reports import ReportGenerator
from services.activity_feed import describe_when, get_recent_activity
from database import init_database
from utils.client_selector import render_client_selector
from utils.unlock import require_unlock
from utils import icons
from utils.dates import long_date, slash_date
from utils.fiscal_dates import fiscal_year_bounds
from utils.ui import financial_statement

# Leading glyph per activity kind, so the feed can be scanned by shape.
ACTIVITY_ICONS = {
    "import": icons.IMPORT,
    "journal": icons.JOURNAL_ENTRIES,
    "audit": icons.AUDIT_TRAIL,
}

# Initialize database

st.set_page_config(page_title="Dashboard", page_icon=icons.DASHBOARD, layout="wide")

# Client selector in sidebar
# Gate on the database passphrase before any DB access, then ensure schema.
require_unlock()
init_database()

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
recent_activity = get_recent_activity(client_id, limit=6)

# Trial balance for totals
tb_rows = ReportGenerator.trial_balance(client_id)
asset_accounts = [(r.account_number, r.account_name, r.debit - r.credit) for r in tb_rows if r.account_type == 'Asset']
liability_accounts = [(r.account_number, r.account_name, r.credit - r.debit) for r in tb_rows if r.account_type == 'Liability']
equity_accounts = [(r.account_number, r.account_name, r.credit - r.debit) for r in tb_rows if r.account_type == 'Equity']

total_assets = sum(bal for _, _, bal in asset_accounts)
total_liabilities = sum(bal for _, _, bal in liability_accounts)
total_equity = sum(bal for _, _, bal in equity_accounts)

# Income statement for fiscal year-to-date
today = date.today()
fiscal_start, fiscal_end = fiscal_year_bounds(today, client.fiscal_year_end_month)
income_report = ReportGenerator.income_statement(client_id, fiscal_start, min(today, fiscal_end))

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
        width="stretch",
        type="secondary" if st.session_state.dashboard_expanded != 'assets' else "primary"
    ):
        st.session_state.dashboard_expanded = 'assets' if st.session_state.dashboard_expanded != 'assets' else None
        st.rerun()

with col2:
    if st.button(
        f"**Total Liabilities** {_toggle_icon('liabilities')}\n\n${total_liabilities:,.2f}",
        key="btn_liabilities",
        width="stretch",
        type="secondary" if st.session_state.dashboard_expanded != 'liabilities' else "primary"
    ):
        st.session_state.dashboard_expanded = 'liabilities' if st.session_state.dashboard_expanded != 'liabilities' else None
        st.rerun()

with col3:
    if st.button(
        f"**Fiscal YTD Net Income** {_toggle_icon('income')}\n\n${income_report['net_income']:,.2f}",
        key="btn_income",
        width="stretch",
        type="secondary" if st.session_state.dashboard_expanded != 'income' else "primary"
    ):
        st.session_state.dashboard_expanded = 'income' if st.session_state.dashboard_expanded != 'income' else None
        st.rerun()

with col4:
    if pending_count > 0:
        if st.button(
            f"**Pending Imports** →\n\n{pending_count} (Action needed)",
            key="btn_pending",
            width="stretch",
            type="secondary"
        ):
            st.switch_page("pages/4_Import_Transactions.py")
    else:
        st.button(
            f"**Pending Imports**\n\n0 (All clear)",
            key="btn_pending",
            width="stretch",
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
    st.subheader("Recent Activity")

    if not recent_activity:
        st.info("No activity yet. Import transactions or create a journal entry to get started.")
    else:
        # Reported as work done ("imported 45 transactions into …"), not as
        # individual entries — an imported month would otherwise fill this panel
        # with rows that say nothing about what was actually done.
        for event in recent_activity:
            col_a, col_b = st.columns([4, 1], vertical_alignment="top")
            with col_a:
                line = f"{ACTIVITY_ICONS.get(event.kind, '')} **{event.summary}**"
                # Attribution rides on the detail line ("· by Charlie Barmore").
                # Events from before actor tracking simply omit it.
                detail = event.detail or ""
                if event.actor:
                    detail = f"{detail} · by {event.actor}" if detail else f"by {event.actor}"
                if detail:
                    line += f"  \n{detail}"
                st.markdown(line)
            with col_b:
                # Right-aligned so the timestamps form a column against the
                # variable-length summaries on the left.
                st.markdown(
                    f"<div style='text-align: right; color: #6b7280; font-size: 0.85em'>"
                    f"{describe_when(event.when)}</div>",
                    unsafe_allow_html=True,
                )

    st.page_link("pages/8_Audit_Trail.py", label="View full history", icon=icons.AUDIT_TRAIL)

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
_summary_fy_start, _ = fiscal_year_bounds(date.today(), client.fiscal_year_end_month)
st.caption(
    f"As of {long_date(date.today())} · revenue and expenses are "
    f"fiscal year-to-date ({slash_date(_summary_fy_start)} – "
    f"{slash_date(date.today())})"
)

# Get balances by type. Balances are signed by each account's normal balance
# (contra accounts show negative), so every section total is a simple sum and
# the accounting-equation check below is exact.
balance_data = {
    'Assets': [],
    'Liabilities': [],
    'Equity': [],
    'Revenue': [],
    'Expenses': []
}
_TYPE_BUCKET = {'Asset': 'Assets', 'Liability': 'Liabilities',
                'Equity': 'Equity', 'Revenue': 'Revenue', 'Expense': 'Expenses'}

for account in accounts:
    balance = Account.get_balance(account.id)
    if abs(balance) > 0.01:
        bucket = _TYPE_BUCKET.get(account.type)
        if bucket:
            balance_data[bucket].append((account.display_name(), balance))

section_totals = {bucket: round(sum(bal for _, bal in rows), 2)
                  for bucket, rows in balance_data.items()}
summary_net_income = round(section_totals['Revenue'] - section_totals['Expenses'], 2)


def _section_rows(heading, bucket, empty_text, total_label):
    rows = [("section", heading, [])]
    entries = balance_data[bucket]
    if entries:
        # Every account, not a top-N: a truncated list hides exactly the
        # account someone is looking for and makes the total look wrong.
        rows += [("item", name, [bal])
                 for name, bal in sorted(entries, key=lambda x: -x[1])]
    else:
        rows.append(("note", empty_text, []))
    rows.append(("subtotal", total_label, [section_totals[bucket]]))
    return rows


col1, col2 = st.columns(2)

with col1:
    financial_statement(
        _section_rows("Assets", 'Assets', "No assets with balances", "Total assets")
        + _section_rows("Liabilities", 'Liabilities',
                        "No liabilities with balances", "Total liabilities")
        + _section_rows("Equity", 'Equity', "No equity balances", "Total equity")
    )

with col2:
    financial_statement(
        _section_rows("Revenue (Fiscal YTD)", 'Revenue', "No revenue recorded",
                      "Total revenue")
        + _section_rows("Expenses (Fiscal YTD)", 'Expenses',
                        "No expenses recorded", "Total expenses")
        + [("total", "Net income (fiscal YTD)", [summary_net_income])]
    )

# The accounting equation, checked from the same balances shown above. Any gap
# means a journal entry posted one-sided or an account type is misassigned.
equation_gap = round(
    section_totals['Assets']
    - (section_totals['Liabilities'] + section_totals['Equity'] + summary_net_income),
    2,
)
if abs(equation_gap) < 0.01:
    st.success(
        f"In balance — assets ${section_totals['Assets']:,.2f} = "
        f"liabilities ${section_totals['Liabilities']:,.2f} "
        f"+ equity ${section_totals['Equity']:,.2f} "
        f"+ net income ${summary_net_income:,.2f}"
    )
else:
    st.error(
        f"OUT OF BALANCE by ${abs(equation_gap):,.2f} — assets "
        f"${section_totals['Assets']:,.2f} vs liabilities + equity + net income "
        f"${section_totals['Liabilities'] + section_totals['Equity'] + summary_net_income:,.2f}"
    )
