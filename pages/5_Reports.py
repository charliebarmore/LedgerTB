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
from utils.unlock import require_unlock
from utils.ui import financial_statement, view_switcher


def gl_drill_down(options, key, start_date, end_date):
    """One selectbox + button instead of a button per account row."""
    if not options:
        return
    dd1, dd2 = st.columns([3, 1])
    with dd1:
        picked = st.selectbox(
            "Drill into general ledger",
            options=list(options.keys()),
            format_func=lambda account_id: options[account_id],
            key=f"{key}_gl_pick",
        )
    with dd2:
        st.write("")
        if st.button("Open GL →", width="stretch", key=f"{key}_gl_open"):
            st.session_state.gl_account_id = picked
            st.session_state.gl_start_date = start_date
            st.session_state.gl_end_date = end_date
            st.session_state.active_report = "General Ledger"
            st.rerun()
from utils import icons
from utils.export import sanitize_df
from utils.fiscal_dates import fiscal_year_bounds

# Initialize database

st.set_page_config(page_title="Reports", page_icon=icons.REPORTS, layout="wide")

# Client selector in sidebar
# Gate on the database passphrase before any DB access, then ensure schema.
require_unlock()
init_database()

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
        total_debits = sum(r.debit for r in rows)
        total_credits = sum(r.credit for r in rows)

        st.markdown(f"**As of {as_of_date.strftime('%B %-d, %Y')}**")
        statement_rows = [
            ("item",
             f"{row.account_number} - {row.account_name}",
             [row.debit if row.debit > 0 else None,
              row.credit if row.credit > 0 else None],
             row.account_type)
            for row in rows
        ]
        statement_rows.append(("total", "Totals", [total_debits, total_credits]))
        financial_statement(statement_rows, headers=["Debit", "Credit"])

        if abs(total_debits - total_credits) < 0.01:
            st.success("Trial balance is in balance.")
        else:
            st.error(f"Trial balance is OUT OF BALANCE by ${abs(total_debits - total_credits):,.2f}")

        gl_drill_down(
            {account_id_lookup[r.account_number]:
                 f"{r.account_number} - {r.account_name}"
             for r in rows if r.account_number in account_id_lookup},
            key="tb",
            start_date=fiscal_year_bounds(as_of_date, client.fiscal_year_end_month)[0],
            end_date=as_of_date,
        )

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

    st.markdown(
        f"**{is_start.strftime('%B %-d, %Y')} to {is_end.strftime('%B %-d, %Y')}**"
    )

    statement_rows = [("section", "Revenue", [])]
    if report['revenues']:
        statement_rows += [
            ("item", f"{r['account_number']} - {r['name']}", [r['balance']])
            for r in report['revenues']
        ]
    else:
        statement_rows.append(("note", "No revenue recorded", []))
    statement_rows.append(("subtotal", "Total Revenue", [report['total_revenue']]))

    statement_rows.append(("section", "Expenses", []))
    if report['expenses']:
        statement_rows += [
            ("item", f"{e['account_number']} - {e['name']}", [e['balance']])
            for e in report['expenses']
        ]
    else:
        statement_rows.append(("note", "No expenses recorded", []))
    statement_rows.append(("subtotal", "Total Expenses", [report['total_expenses']]))
    statement_rows.append(("total", "Net Income", [report['net_income']]))

    financial_statement(statement_rows)

    gl_drill_down(
        {account_id_lookup[r['account_number']]:
             f"{r['account_number']} - {r['name']}"
         for r in report['revenues'] + report['expenses']
         if r['account_number'] in account_id_lookup},
        key="is", start_date=is_start, end_date=is_end,
    )

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

    st.markdown(f"**As of {bs_date.strftime('%B %-d, %Y')}**")

    def _section(title, entries, subtotal_label, subtotal_value):
        rows = [("section", title, [])]
        if entries:
            rows += [
                ("item", (f"{e['account_number']} - {e['name']}"
                          if e['account_number'] else e['name']),
                 [e['balance']])
                for e in entries
            ]
        else:
            rows.append(("note", f"No {title.lower()} recorded", []))
        rows.append(("subtotal", subtotal_label, [subtotal_value]))
        return rows

    statement_rows = (
        _section("Assets", report['assets'], "Total Assets", report['total_assets'])
        + _section("Liabilities", report['liabilities'],
                   "Total Liabilities", report['total_liabilities'])
        + _section("Equity", report['equity'], "Total Equity", report['total_equity'])
        + [("total", "Total Liabilities & Equity", [report['total_liabilities_equity']])]
    )
    financial_statement(statement_rows)

    if abs(report['total_assets'] - report['total_liabilities_equity']) < 0.01:
        st.success("Balance sheet is balanced.")
    else:
        st.error("Balance sheet is OUT OF BALANCE!")

    gl_drill_down(
        {account_id_lookup[e['account_number']]:
             f"{e['account_number']} - {e['name']}"
         for e in report['assets'] + report['liabilities'] + report['equity']
         if e['account_number'] and e['account_number'] in account_id_lookup},
        key="bs",
        start_date=fiscal_year_bounds(bs_date, client.fiscal_year_end_month)[0],
        end_date=bs_date,
    )

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
