import streamlit as st
import sys
from pathlib import Path
from datetime import date, timedelta
from io import BytesIO

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.account import Account
from models.client import Client
from models.audit_log import AuditLog
from models.reports import ReportGenerator
from database import init_database
from utils.client_selector import render_client_selector
from utils.ui import view_switcher
from utils import icons
from utils.export import sanitize_df
from utils.fiscal_dates import fiscal_year_bounds

# Initialize database
init_database()

st.set_page_config(page_title="Reports", page_icon=icons.REPORTS, layout="wide")

# Client selector in sidebar
client_id = render_client_selector()

st.title("Reports")

if not client_id:
    st.warning("Please create a client first in the Clients page.")
    st.page_link("pages/0_Clients.py", label="Go to Clients →")
    st.stop()

# Get client info
client = Client.get_by_id(client_id)
st.caption(f"Viewing: **{client.name}**")
current_fy_start, _ = fiscal_year_bounds(date.today(), client.fiscal_year_end_month)

# Track active report in session state for sidebar navigation
if 'active_report' not in st.session_state:
    st.session_state.active_report = "Trial Balance"

report_options = ["Trial Balance", "Income Statement", "Balance Sheet", "General Ledger"]

# Report selector (segmented tabs; programmatically controllable via
# st.session_state.active_report from the sidebar quick links and GL drills).
selected_report = view_switcher(report_options, key="active_report",
                                label="Select Report")

st.divider()

if selected_report == "Trial Balance":
    st.subheader("Trial Balance")

    col1, col2 = st.columns([1, 3])
    with col1:
        as_of_date = st.date_input("As of Date", value=date.today(), key="tb_date")

    rows = ReportGenerator.trial_balance(client_id, as_of_date)

    # Get accounts for drill-down
    # Include deactivated accounts for historical drill-down. They remain
    # unavailable in new-entry pickers, but their ledger history must be reachable.
    accounts = Account.get_all(client_id, active_only=False)
    account_id_lookup = {f"{a.account_number}": a.id for a in accounts}

    if not rows:
        st.info("No transactions recorded yet.")
    else:
        # Display report
        total_debits = sum(r.debit for r in rows)
        total_credits = sum(r.credit for r in rows)

        st.caption("Click an account to view its General Ledger")

        # Display as interactive rows instead of dataframe
        header_cols = st.columns([1, 3, 1, 1, 1])
        with header_cols[0]:
            st.markdown("**Account #**")
        with header_cols[1]:
            st.markdown("**Account Name**")
        with header_cols[2]:
            st.markdown("**Type**")
        with header_cols[3]:
            st.markdown("**Debit**")
        with header_cols[4]:
            st.markdown("**Credit**")

        for row in rows:
            cols = st.columns([1, 3, 1, 1, 1])
            with cols[0]:
                st.text(row.account_number)
            with cols[1]:
                # Make account name clickable. No width="stretch" here --
                # a full-width bordered button reads as a text input; a
                # content-width one reads as a link.
                account_id = account_id_lookup.get(row.account_number)
                if account_id and st.button(row.account_name, key=f"tb_acct_{row.account_number}"):
                    st.session_state.gl_account_id = account_id
                    st.session_state.gl_start_date = fiscal_year_bounds(
                        as_of_date, client.fiscal_year_end_month
                    )[0]
                    st.session_state.gl_end_date = as_of_date
                    st.session_state.active_report = "General Ledger"
                    st.rerun()
            with cols[2]:
                st.text(row.account_type)
            with cols[3]:
                st.text(f"${row.debit:,.2f}" if row.debit > 0 else "")
            with cols[4]:
                st.text(f"${row.credit:,.2f}" if row.credit > 0 else "")

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
            sanitize_df(df).to_excel(buffer, index=False, sheet_name="Trial Balance")
            buffer.seek(0)

        st.download_button(
            label="Download Excel",
            data=buffer,
            file_name=f"trial_balance_{client.name}_{as_of_date}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            on_click=AuditLog.log_event,
            args=(client_id, "EXPORT", "trial_balance_export", {
                "format": "xlsx", "as_of_date": as_of_date, "row_count": len(rows),
            }),
        )

elif selected_report == "Income Statement":
    st.subheader("Income Statement")

    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        is_start = st.date_input("Start Date", value=current_fy_start, key="is_start")
    with col2:
        is_end = st.date_input("End Date", value=date.today(), key="is_end")

    if is_start > is_end:
        st.error("Income statement start date cannot be after the end date.")
        st.stop()

    report = ReportGenerator.income_statement(client_id, is_start, is_end)

    # Get accounts for drill-down
    accounts = Account.get_all(client_id, active_only=False)
    account_id_lookup = {a.account_number: a.id for a in accounts}

    def is_drill_down_link(account_number, account_name, balance, key_prefix):
        """Same drill-down pattern as the Trial Balance / Balance Sheet views,
        scoped to the Income Statement's own date range."""
        col1, col2 = st.columns([3, 1])
        with col1:
            account_id = account_id_lookup.get(account_number)
            if account_id:
                if st.button(f"{account_number} - {account_name}", key=f"{key_prefix}_{account_number}"):
                    st.session_state.gl_account_id = account_id
                    st.session_state.gl_start_date = is_start
                    st.session_state.gl_end_date = is_end
                    st.session_state.active_report = "General Ledger"
                    st.rerun()
            else:
                st.text(f"  {account_name}")
        with col2:
            st.text(f"${balance:,.2f}")

    st.markdown(f"**Period: {is_start} to {is_end}**")
    st.caption("Click an account to view its General Ledger")
    st.divider()

    # Revenue section
    st.markdown("### Revenue")
    if report['revenues']:
        for r in report['revenues']:
            is_drill_down_link(r['account_number'], r['name'], r['balance'], "is_rev")
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
            is_drill_down_link(e['account_number'], e['name'], e['balance'], "is_exp")
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
    sanitize_df(df).to_excel(buffer, index=False, sheet_name="Income Statement")
    buffer.seek(0)

    st.download_button(
        label="Download Excel",
        data=buffer,
        file_name=f"income_statement_{client.name}_{is_start}_to_{is_end}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        on_click=AuditLog.log_event,
        args=(client_id, "EXPORT", "income_statement_export", {
            "format": "xlsx", "start_date": is_start, "end_date": is_end,
            "row_count": len(df),
        }),
    )

elif selected_report == "Balance Sheet":
    st.subheader("Balance Sheet")

    col1, col2 = st.columns([1, 3])
    with col1:
        bs_date = st.date_input("As of Date", value=date.today(), key="bs_date")

    report = ReportGenerator.balance_sheet(client_id, bs_date)

    # Get accounts for drill-down
    accounts = Account.get_all(client_id, active_only=False)
    account_id_lookup = {a.account_number: a.id for a in accounts}

    def drill_down_link(account_number, account_name, balance, key_prefix):
        """Create a clickable account link for drill-down. Falls back to plain
        text when there's no backing account (e.g. computed Current Year Earnings)."""
        col1, col2 = st.columns([3, 1])
        with col1:
            account_id = account_id_lookup.get(account_number) if account_number else None
            if account_id:
                if st.button(f"  {account_number} - {account_name}", key=f"{key_prefix}_{account_number}"):
                    st.session_state.gl_account_id = account_id
                    st.session_state.gl_start_date = fiscal_year_bounds(
                        bs_date, client.fiscal_year_end_month
                    )[0]
                    st.session_state.gl_end_date = bs_date
                    st.session_state.active_report = "General Ledger"
                    st.rerun()
            else:
                st.text(f"  {account_name}")
        with col2:
            st.text(f"${balance:,.2f}")

    st.markdown(f"**As of: {bs_date}**")
    st.caption("Click an account to view its General Ledger")
    st.divider()

    # Assets
    st.markdown("### Assets")
    if report['assets']:
        for a in report['assets']:
            drill_down_link(a['account_number'], a['name'], a['balance'], "bs_asset")
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
            drill_down_link(l['account_number'], l['name'], l['balance'], "bs_liab")
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
            drill_down_link(e['account_number'], e['name'], e['balance'], "bs_equity")
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
    sanitize_df(df).to_excel(buffer, index=False, sheet_name="Balance Sheet")
    buffer.seek(0)

    st.download_button(
        label="Download Excel",
        data=buffer,
        file_name=f"balance_sheet_{client.name}_{bs_date}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        on_click=AuditLog.log_event,
        args=(client_id, "EXPORT", "balance_sheet_export", {
            "format": "xlsx", "as_of_date": bs_date, "row_count": len(df),
        }),
    )

elif selected_report == "General Ledger":
    st.subheader("General Ledger")

    # Account selection
    accounts = Account.get_all(client_id, active_only=False)
    account_options = {
        a.id: a.display_name() + (" (inactive)" if not a.is_active else "")
        for a in accounts
    }

    # Check for drill-down from another report
    default_account = st.session_state.get('gl_account_id', None)
    default_start = st.session_state.get('gl_start_date', current_fy_start)
    default_end = st.session_state.get('gl_end_date', date.today())

    # Clear the session state after using it
    if 'gl_account_id' in st.session_state:
        del st.session_state.gl_account_id
    if 'gl_start_date' in st.session_state:
        del st.session_state.gl_start_date
    if 'gl_end_date' in st.session_state:
        del st.session_state.gl_end_date

    col1, col2, col3 = st.columns(3)

    with col1:
        if account_options:
            # Find default index
            default_idx = 0
            if default_account and default_account in account_options:
                default_idx = list(account_options.keys()).index(default_account)

            selected_account = st.selectbox(
                "Select Account",
                options=list(account_options.keys()),
                format_func=lambda x: account_options[x],
                index=default_idx
            )
        else:
            st.warning("No accounts available")
            selected_account = None

    with col2:
        gl_start = st.date_input("Start Date", value=default_start, key="gl_start")

    with col3:
        gl_end = st.date_input("End Date", value=default_end, key="gl_end")

    if gl_start > gl_end:
        st.error("General ledger start date cannot be after the end date.")
        st.stop()

    if selected_account:
        entries = ReportGenerator.general_ledger(selected_account, gl_start, gl_end, client_id=client_id)

        if not entries:
            st.info("No transactions for this account in the selected period.")
        else:
            # Calculate period totals (excluding beginning balance)
            period_debits = sum(e.debit for e in entries if e.entry_id != 0)
            period_credits = sum(e.credit for e in entries if e.entry_id != 0)

            # Check for beginning balance
            beginning_balance = 0
            if entries and entries[0].entry_id == 0:
                beginning_balance = entries[0].balance

            # Display entries as interactive table with clickable Entry #
            st.caption("Click an Entry # to edit that journal entry")

            # Header row
            header_cols = st.columns([1, 0.8, 2.5, 1.5, 1, 1, 1])
            with header_cols[0]:
                st.markdown("**Date**")
            with header_cols[1]:
                st.markdown("**Entry #**")
            with header_cols[2]:
                st.markdown("**Description**")
            with header_cols[3]:
                st.markdown("**Reference**")
            with header_cols[4]:
                st.markdown("**Debit**")
            with header_cols[5]:
                st.markdown("**Credit**")
            with header_cols[6]:
                st.markdown("**Balance**")

            for idx, e in enumerate(entries):
                cols = st.columns([1, 0.8, 2.5, 1.5, 1, 1, 1])
                with cols[0]:
                    st.text(e.entry_date.isoformat())
                with cols[1]:
                    if e.entry_id == 0:
                        st.text("")  # Beginning balance has no entry
                    else:
                        # Make entry # clickable to edit (use idx for unique key).
                        # No width="stretch", for the same reason as the other
                        # drill-down links: content-width reads as a compact tag,
                        # full-width reads as an empty input field.
                        if st.button(f"#{e.entry_id}", key=f"gl_je_{idx}"):
                            st.session_state.edit_entry_id = e.entry_id
                            st.switch_page("pages/2_Journal_Entries.py")
                with cols[2]:
                    st.text(e.description[:35] if e.description else "")
                with cols[3]:
                    st.text(e.source_reference[:18] if e.source_reference else "")
                with cols[4]:
                    st.text(f"${e.debit:,.2f}" if e.debit > 0 else "")
                with cols[5]:
                    st.text(f"${e.credit:,.2f}" if e.credit > 0 else "")
                with cols[6]:
                    st.text(f"${e.balance:,.2f}")

            # Summary
            account = Account.get_by_id(selected_account, client_id=client_id)
            final_balance = entries[-1].balance if entries else 0

            st.divider()
            st.markdown("**Summary**")
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Beginning Balance", f"${beginning_balance:,.2f}")
            with col2:
                st.metric("Period Debits", f"${period_debits:,.2f}")
            with col3:
                st.metric("Period Credits", f"${period_credits:,.2f}")
            with col4:
                st.metric("Ending Balance", f"${final_balance:,.2f}")

            # Export
            st.divider()
            df = ReportGenerator.general_ledger_to_dataframe(entries)

            buffer = BytesIO()
            sanitize_df(df).to_excel(buffer, index=False, sheet_name="General Ledger")
            buffer.seek(0)

            st.download_button(
                label="Download Excel",
                data=buffer,
                file_name=f"general_ledger_{client.name}_{account.account_number}_{gl_start}_to_{gl_end}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                on_click=AuditLog.log_event,
                args=(client_id, "EXPORT", "general_ledger_export", {
                    "format": "xlsx", "account_id": selected_account,
                    "start_date": gl_start, "end_date": gl_end, "row_count": len(entries),
                }),
            )
