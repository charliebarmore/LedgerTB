import streamlit as st
import sys
from pathlib import Path
from datetime import date, timedelta

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.client import Client
from models.account import Account
from models.transaction import ImportedTransaction
from models.journal_entry import JournalEntry
from models.audit_log import AuditLog
from database import init_database
from utils.client_selector import render_client_selector
from utils.unlock import require_unlock
from utils import icons
from utils.export import sanitize_df
from utils.fiscal_dates import fiscal_year_bounds, previous_fiscal_year_bounds

# Initialize database

st.set_page_config(page_title="Transactions", page_icon=icons.TRANSACTIONS, layout="wide")

# Client selector in sidebar
# Gate on the database passphrase before any DB access, then ensure schema.
require_unlock()
init_database()

client_id = render_client_selector()

st.title("Transactions")

if not client_id:
    st.warning("Please create a client first in the Clients page.")
    st.page_link("pages/0_Clients.py", label="Go to Clients →")
    st.stop()

# Get client info
client = Client.get_by_id(client_id)
st.caption(f"Viewing: **{client.name}**")
today = date.today()
current_fy_start, current_fy_end = fiscal_year_bounds(today, client.fiscal_year_end_month)
previous_fy_start, previous_fy_end = previous_fiscal_year_bounds(
    today, client.fiscal_year_end_month
)

# Filters
st.subheader("Filters")
col1, col2, col3, col4, col5 = st.columns(5)

with col1:
    # Date range. Defaults to All Time rather than a recent window so a
    # freshly-imported or older book of transactions isn't hidden on first view.
    date_range = st.selectbox(
        "Date Range",
        options=[
            "All Time", "Last 30 days", "Last 90 days",
            "This Fiscal Year", "Last Fiscal Year", "Custom",
        ],
        index=0
    )

with col2:
    if date_range == "Custom":
        start_date = st.date_input("Start Date", value=date.today() - timedelta(days=30))
    else:
        start_date = None

with col3:
    if date_range == "Custom":
        end_date = st.date_input("End Date", value=date.today())
    else:
        end_date = None

with col4:
    # Status filter
    status_filter = st.selectbox(
        "Status",
        options=["All", "Posted", "Pending", "Categorized", "Dismissed", "Reversed"],
        index=0
    )

with col5:
    clearance_filter = st.selectbox(
        "Reconciliation",
        options=["All", "Cleared", "Uncleared"],
        index=0,
    )

# Calculate date range
if date_range == "Last 30 days":
    start_date = today - timedelta(days=30)
    end_date = today
elif date_range == "Last 90 days":
    start_date = today - timedelta(days=90)
    end_date = today
elif date_range == "This Fiscal Year":
    start_date = current_fy_start
    end_date = min(today, current_fy_end)
elif date_range == "Last Fiscal Year":
    start_date = previous_fy_start
    end_date = previous_fy_end
elif date_range == "All Time":
    start_date = None
    end_date = None

if start_date and end_date and start_date > end_date:
    st.error("Transaction filter start date cannot be after the end date.")
    st.stop()

# Bank account filter
col1, col2 = st.columns([1, 3])
with col1:
    accounts = Account.get_all(client_id, active_only=True)
    bank_accounts = [a for a in accounts if a.type in ('Asset', 'Liability')]
    bank_options = {0: "All Accounts"}
    bank_options.update({a.id: a.display_name() for a in bank_accounts})

    selected_bank = st.selectbox(
        "Bank/Credit Card",
        options=list(bank_options.keys()),
        format_func=lambda x: bank_options[x]
    )

st.divider()

status_param = None if status_filter == "All" else status_filter
bank_param = None if selected_bank == 0 else selected_bank
cleared_param = None if clearance_filter == "All" else clearance_filter == "Cleared"

# Reset paging when the filter set changes.
filter_signature = (
    start_date, end_date, status_param, bank_param, cleared_param,
)
if st.session_state.get("transactions_filter_signature") != filter_signature:
    st.session_state.transactions_filter_signature = filter_signature
    st.session_state.transactions_page = 1

page_size = 50
summary = ImportedTransaction.get_filtered_summary(
    client_id=client_id,
    start_date=start_date,
    end_date=end_date,
    status=status_param,
    bank_account_id=bank_param,
    cleared=cleared_param,
)
page_count = max(1, (summary["total_count"] + page_size - 1) // page_size)
current_page = min(max(1, st.session_state.get("transactions_page", 1)), page_count)
st.session_state.transactions_page = current_page

transactions = ImportedTransaction.get_all(
    client_id=client_id,
    start_date=start_date,
    end_date=end_date,
    status=status_param,
    bank_account_id=bank_param,
    cleared=cleared_param,
    limit=page_size,
    offset=(current_page - 1) * page_size,
)

# Summary metrics
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric("Filtered Transactions", summary["total_count"])
with col2:
    st.metric("Deposits", f"${summary['total_deposits']:,.2f}")
with col3:
    st.metric("Withdrawals", f"${abs(summary['total_withdrawals']):,.2f}")
with col4:
    st.metric("Posted / Pending", f"{summary['posted_count']} / {summary['pending_count']}")

nav_left, nav_status, nav_right = st.columns([1, 2, 1])
with nav_left:
    if st.button("Previous", disabled=current_page <= 1, key="transactions_previous"):
        st.session_state.transactions_page = current_page - 1
        st.rerun()
with nav_status:
    first_row = (current_page - 1) * page_size + 1 if summary["total_count"] else 0
    last_row = min(current_page * page_size, summary["total_count"])
    st.caption(
        f"Page {current_page} of {page_count} · showing {first_row}–{last_row} "
        f"of {summary['total_count']}"
    )
with nav_right:
    if st.button("Next", disabled=current_page >= page_count, key="transactions_next"):
        st.session_state.transactions_page = current_page + 1
        st.rerun()

st.divider()

# Transaction list
if not transactions:
    st.info("No transactions found for the selected filters. Import transactions to see them here.")
    st.page_link("pages/4_Import_Transactions.py", label="Go to Import Transactions →")
else:
    # Header row
    header_cols = st.columns([1.1, 2.3, 1.1, 1.3, 1.3, 0.8, 1.0])
    with header_cols[0]:
        st.markdown("**Date**")
    with header_cols[1]:
        st.markdown("**Description**")
    with header_cols[2]:
        st.markdown("**Amount**")
    with header_cols[3]:
        st.markdown("**Account**")
    with header_cols[4]:
        st.markdown("**Category**")
    with header_cols[5]:
        st.markdown("**Status**")
    with header_cols[6]:
        st.markdown("**Reconciliation**")

    st.divider()

    for t in transactions:
        cols = st.columns([1.1, 2.3, 1.1, 1.3, 1.3, 0.8, 1.0])

        with cols[0]:
            st.text(str(t.transaction_date) if t.transaction_date else "")

        with cols[1]:
            st.text(t.description[:40] if t.description else "")
            if t.journal_entry_id:
                st.caption(f"JE #{t.journal_entry_id}")
            if t.replaces_transaction_id:
                st.caption(f"Replacement for transaction #{t.replaces_transaction_id}")

        with cols[2]:
            color = "green" if t.amount >= 0 else "red"
            st.markdown(f":{color}[${abs(t.amount):,.2f}]")

        with cols[3]:
            st.caption(t.bank_account_name or "—")

        with cols[4]:
            st.caption(t.suggested_account_name or "—")

        with cols[5]:
            if t.status == "Posted":
                st.markdown(":green[Posted]")
            elif t.status == "Pending":
                st.markdown(":orange[Pending]")
            elif t.status == "Dismissed":
                st.markdown(":gray[Dismissed]")
            elif t.status == "Reversed":
                st.markdown(":gray[Reversed]")
                if t.superseded_by_batch:
                    st.caption(f"Replaced by {t.superseded_by_batch}")
                if t.reversal_journal_entry_id:
                    st.caption(f"Reversal JE #{t.reversal_journal_entry_id}")
            else:
                st.markdown(f":blue[{t.status}]")

        with cols[6]:
            if t.is_cleared:
                label = "Cleared" if t.reconciliation_status == "Completed" else "Cleared (draft)"
                st.markdown(f":green[{label}]")
                if t.statement_end_date:
                    st.caption(f"Stmt {t.statement_end_date}")
            else:
                st.caption("Uncleared")

    # Export option
    st.divider()

    if st.button("Export to CSV"):
        import pandas as pd
        from io import StringIO

        export_transactions = ImportedTransaction.get_all(
            client_id=client_id, start_date=start_date, end_date=end_date,
            status=status_param, bank_account_id=bank_param, cleared=cleared_param,
            limit=max(1, summary["total_count"]),
        )
        export_data = []
        for t in export_transactions:
            export_data.append({
                'Date': t.transaction_date.isoformat() if t.transaction_date else '',
                'Description': t.description,
                'Amount': t.amount,
                'Bank Account': t.bank_account_name or '',
                'Category': t.suggested_account_name or '',
                'Status': t.status,
                'Reconciliation': 'Cleared' if t.is_cleared else 'Uncleared',
                'Statement End': t.statement_end_date.isoformat() if t.statement_end_date else '',
                'Journal Entry': t.journal_entry_id or ''
            })

        df = pd.DataFrame(export_data)
        csv = sanitize_df(df).to_csv(index=False)

        st.download_button(
            label="Download CSV",
            data=csv,
            file_name=f"transactions_{client.name}_{date.today()}.csv",
            mime="text/csv",
            on_click=AuditLog.log_event,
            args=(client_id, "EXPORT", "transactions_export", {
                "format": "csv", "start_date": start_date, "end_date": end_date,
                "status": status_param or "All", "account_id": bank_param,
                "reconciliation": clearance_filter, "row_count": len(export_transactions),
            }),
        )
