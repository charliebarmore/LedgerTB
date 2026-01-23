import streamlit as st
import sys
from pathlib import Path
from datetime import date, timedelta
from io import BytesIO

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.account import Account
from models.client import Client
from models.reports import ReportGenerator
from database import init_database
from utils.client_selector import render_client_selector

# Initialize database
init_database()

st.set_page_config(page_title="Reports", page_icon="📊", layout="wide")

# Client selector in sidebar
client_id = render_client_selector()

st.title("📊 Reports")

if not client_id:
    st.warning("Please create a client first in the Clients page.")
    st.page_link("pages/0_Clients.py", label="Go to Clients →")
    st.stop()

# Get client info
client = Client.get_by_id(client_id)
st.caption(f"Viewing: **{client.name}**")

# Track active report in session state for sidebar navigation
if 'active_report' not in st.session_state:
    st.session_state.active_report = "Trial Balance"

report_options = ["Trial Balance", "Income Statement", "Balance Sheet", "General Ledger"]

# Report selector using radio buttons (allows programmatic control from sidebar)
selected_report = st.radio(
    "Select Report",
    options=report_options,
    index=report_options.index(st.session_state.active_report),
    horizontal=True,
    label_visibility="collapsed"
)
st.session_state.active_report = selected_report

st.divider()

if selected_report == "Trial Balance":
    st.subheader("Trial Balance")

    col1, col2 = st.columns([1, 3])
    with col1:
        as_of_date = st.date_input("As of Date", value=date.today(), key="tb_date")

    rows = ReportGenerator.trial_balance(client_id, as_of_date)

    if not rows:
        st.info("No transactions recorded yet.")
    else:
        # Display report
        total_debits = sum(r.debit for r in rows)
        total_credits = sum(r.credit for r in rows)

        # Create display data
        data = []
        for row in rows:
            data.append({
                "Account #": row.account_number,
                "Account Name": row.account_name,
                "Type": row.account_type,
                "Debit": f"${row.debit:,.2f}" if row.debit > 0 else "",
                "Credit": f"${row.credit:,.2f}" if row.credit > 0 else ""
            })

        st.dataframe(data, use_container_width=True, hide_index=True)

        # Totals
        col1, col2, col3 = st.columns([2, 1, 1])
        with col2:
            st.markdown(f"**Total Debits: ${total_debits:,.2f}**")
        with col3:
            st.markdown(f"**Total Credits: ${total_credits:,.2f}**")

        # Check if balanced
        if abs(total_debits - total_credits) < 0.01:
            st.success("Trial balance is in balance.")
        else:
            st.error(f"Trial balance is OUT OF BALANCE by ${abs(total_debits - total_credits):,.2f}")

        # Export
        st.divider()
        df = ReportGenerator.trial_balance_to_dataframe(rows)

        buffer = BytesIO()
        with st.spinner("Preparing export..."):
            df.to_excel(buffer, index=False, sheet_name="Trial Balance")
            buffer.seek(0)

        st.download_button(
            label="📥 Download Excel",
            data=buffer,
            file_name=f"trial_balance_{client.name}_{as_of_date}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

elif selected_report == "Income Statement":
    st.subheader("Income Statement")

    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        is_start = st.date_input("Start Date", value=date.today().replace(month=1, day=1), key="is_start")
    with col2:
        is_end = st.date_input("End Date", value=date.today(), key="is_end")

    report = ReportGenerator.income_statement(client_id, is_start, is_end)

    st.markdown(f"**Period: {is_start} to {is_end}**")
    st.divider()

    # Revenue section
    st.markdown("### Revenue")
    if report['revenues']:
        for r in report['revenues']:
            col1, col2 = st.columns([3, 1])
            with col1:
                st.text(f"  {r['account_number']} - {r['name']}")
            with col2:
                st.text(f"${r['balance']:,.2f}")
    else:
        st.caption("  No revenue recorded")

    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown("**Total Revenue**")
    with col2:
        st.markdown(f"**${report['total_revenue']:,.2f}**")

    st.divider()

    # Expenses section
    st.markdown("### Expenses")
    if report['expenses']:
        for e in report['expenses']:
            col1, col2 = st.columns([3, 1])
            with col1:
                st.text(f"  {e['account_number']} - {e['name']}")
            with col2:
                st.text(f"${e['balance']:,.2f}")
    else:
        st.caption("  No expenses recorded")

    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown("**Total Expenses**")
    with col2:
        st.markdown(f"**${report['total_expenses']:,.2f}**")

    st.divider()

    # Net Income
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown("## Net Income")
    with col2:
        color = "green" if report['net_income'] >= 0 else "red"
        st.markdown(f"## :{color}[${report['net_income']:,.2f}]")

    # Export
    st.divider()
    df = ReportGenerator.income_statement_to_dataframe(report)

    buffer = BytesIO()
    df.to_excel(buffer, index=False, sheet_name="Income Statement")
    buffer.seek(0)

    st.download_button(
        label="📥 Download Excel",
        data=buffer,
        file_name=f"income_statement_{client.name}_{is_start}_to_{is_end}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

elif selected_report == "Balance Sheet":
    st.subheader("Balance Sheet")

    col1, col2 = st.columns([1, 3])
    with col1:
        bs_date = st.date_input("As of Date", value=date.today(), key="bs_date")

    report = ReportGenerator.balance_sheet(client_id, bs_date)

    st.markdown(f"**As of: {bs_date}**")
    st.divider()

    # Assets
    st.markdown("### Assets")
    if report['assets']:
        for a in report['assets']:
            col1, col2 = st.columns([3, 1])
            with col1:
                st.text(f"  {a['account_number']} - {a['name']}")
            with col2:
                st.text(f"${a['balance']:,.2f}")
    else:
        st.caption("  No assets recorded")

    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown("**Total Assets**")
    with col2:
        st.markdown(f"**${report['total_assets']:,.2f}**")

    st.divider()

    # Liabilities
    st.markdown("### Liabilities")
    if report['liabilities']:
        for l in report['liabilities']:
            col1, col2 = st.columns([3, 1])
            with col1:
                st.text(f"  {l['account_number']} - {l['name']}")
            with col2:
                st.text(f"${l['balance']:,.2f}")
    else:
        st.caption("  No liabilities recorded")

    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown("**Total Liabilities**")
    with col2:
        st.markdown(f"**${report['total_liabilities']:,.2f}**")

    st.divider()

    # Equity
    st.markdown("### Equity")
    if report['equity']:
        for e in report['equity']:
            col1, col2 = st.columns([3, 1])
            with col1:
                st.text(f"  {e['account_number']} - {e['name']}")
            with col2:
                st.text(f"${e['balance']:,.2f}")
    else:
        st.caption("  No equity recorded")

    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown("**Total Equity**")
    with col2:
        st.markdown(f"**${report['total_equity']:,.2f}**")

    st.divider()

    # Total L&E
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown("## Total Liabilities & Equity")
    with col2:
        st.markdown(f"## ${report['total_liabilities_equity']:,.2f}")

    # Balance check
    if abs(report['total_assets'] - report['total_liabilities_equity']) < 0.01:
        st.success("Balance sheet is balanced.")
    else:
        st.error("Balance sheet is OUT OF BALANCE!")

    # Export
    st.divider()
    df = ReportGenerator.balance_sheet_to_dataframe(report)

    buffer = BytesIO()
    df.to_excel(buffer, index=False, sheet_name="Balance Sheet")
    buffer.seek(0)

    st.download_button(
        label="📥 Download Excel",
        data=buffer,
        file_name=f"balance_sheet_{client.name}_{bs_date}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

elif selected_report == "General Ledger":
    st.subheader("General Ledger")

    # Account selection
    accounts = Account.get_all(client_id, active_only=True)
    account_options = {a.id: a.display_name() for a in accounts}

    col1, col2, col3 = st.columns(3)

    with col1:
        if account_options:
            selected_account = st.selectbox(
                "Select Account",
                options=list(account_options.keys()),
                format_func=lambda x: account_options[x]
            )
        else:
            st.warning("No accounts available")
            selected_account = None

    with col2:
        gl_start = st.date_input("Start Date", value=date.today() - timedelta(days=90), key="gl_start")

    with col3:
        gl_end = st.date_input("End Date", value=date.today(), key="gl_end")

    if selected_account:
        entries = ReportGenerator.general_ledger(selected_account, gl_start, gl_end)

        if not entries:
            st.info("No transactions for this account in the selected period.")
        else:
            # Display entries
            data = []
            for e in entries:
                data.append({
                    "Date": e.entry_date.isoformat(),
                    "Entry #": e.entry_id,
                    "Description": e.description[:40],
                    "Reference": e.source_reference[:20] if e.source_reference else "",
                    "Debit": f"${e.debit:,.2f}" if e.debit > 0 else "",
                    "Credit": f"${e.credit:,.2f}" if e.credit > 0 else "",
                    "Balance": f"${e.balance:,.2f}"
                })

            st.dataframe(data, use_container_width=True, hide_index=True)

            # Summary
            account = Account.get_by_id(selected_account)
            final_balance = entries[-1].balance if entries else 0

            col1, col2 = st.columns([3, 1])
            with col2:
                st.metric(f"Ending Balance", f"${final_balance:,.2f}")

            # Export
            st.divider()
            df = ReportGenerator.general_ledger_to_dataframe(entries)

            buffer = BytesIO()
            df.to_excel(buffer, index=False, sheet_name="General Ledger")
            buffer.seek(0)

            st.download_button(
                label="📥 Download Excel",
                data=buffer,
                file_name=f"general_ledger_{client.name}_{account.account_number}_{gl_start}_to_{gl_end}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
