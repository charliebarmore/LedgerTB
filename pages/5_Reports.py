import streamlit as st
import sys
from pathlib import Path
from datetime import date, timedelta
from io import BytesIO

import pandas as pd

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.account import Account
from models.client import Client
from models.audit_log import AuditLog
from models.reports import ReportGenerator
from database import init_database
from utils.client_selector import render_client_selector
from utils.unlock import require_unlock
from utils.dates import long_date
from utils.ui import financial_statement, ledger_table, view_switcher


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

        st.markdown(f"**As of {long_date(as_of_date)}**")
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
        f"**{long_date(is_start)} to {long_date(is_end)}**"
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

    st.markdown(f"**As of {long_date(bs_date)}**")

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
            # The full book is the default; the picker narrows to one account
            # (and drill-downs from other reports land on theirs).
            option_ids = list(account_options.keys())
            default_idx = (option_ids.index(default_account)
                           if default_account in account_options else None)
            selected_account = st.selectbox(
                "Account filter",
                options=option_ids,
                format_func=lambda x: account_options[x],
                index=default_idx,
                placeholder="All accounts",
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
        accounts_to_show = [a for a in accounts if a.id == selected_account]
    else:
        accounts_to_show = sorted(accounts, key=lambda a: a.account_number)

    # Every account with period activity or a carried balance gets a section,
    # so the full view ties to the trial balance. Silent zero accounts don't.
    shown = []
    for account in accounts_to_show:
        entries = ReportGenerator.general_ledger(
            account.id, gl_start, gl_end, client_id=client_id
        )
        has_activity = any(e.entry_id for e in entries)
        carries_balance = bool(entries) and entries[-1].balance != 0
        if has_activity or carries_balance:
            shown.append((account, entries))

    if not shown:
        st.info("No transactions for this account in the selected period."
                if selected_account else
                "No activity or balances in the selected period.")
    else:
        if not selected_account:
            st.caption(f"{len(shown)} accounts with activity or balances · "
                       f"{gl_start} – {gl_end}")

        open_options = {}
        for account, entries in shown:
            period_debits = sum(e.debit for e in entries if e.entry_id != 0)
            period_credits = sum(e.credit for e in entries if e.entry_id != 0)
            final_balance = entries[-1].balance if entries else 0

            if not selected_account:
                st.markdown(f"**{account.display_name()}**")
            ledger_table(
                headers=["Date", "Entry #", "Description", "Reference",
                         "Debit", "Credit", "Balance"],
                rows=[
                    [e.entry_date.isoformat(),
                     f"#{e.entry_id}" if e.entry_id else "",
                     (e.description or "")[:48],
                     (e.source_reference or "")[:24],
                     f"{e.debit:,.2f}" if e.debit > 0 else "",
                     f"{e.credit:,.2f}" if e.credit > 0 else "",
                     f"{e.balance:,.2f}"]
                    for e in entries
                ],
                align=["l", "l", "l", "l", "r", "r", "r"],
                total_row=["", "", "Period totals · ending balance", "",
                           f"${period_debits:,.2f}", f"${period_credits:,.2f}",
                           f"${final_balance:,.2f}"],
            )
            open_options.update({
                e.entry_id: (f"#{e.entry_id} · {e.entry_date} · "
                             f"{(e.description or '')[:34]}")
                for e in entries if e.entry_id
            })

        # One control instead of a button per row.
        if open_options:
            oc1, oc2 = st.columns([3, 1])
            with oc1:
                picked_entry = st.selectbox(
                    "Open journal entry",
                    options=list(open_options.keys()),
                    format_func=lambda entry_id: open_options[entry_id],
                    key="gl_open_entry_pick",
                )
            with oc2:
                st.write("")
                if st.button("Open entry →", width="stretch", key="gl_open_entry"):
                    st.session_state.edit_entry_id = picked_entry
                    st.switch_page("pages/2_Journal_Entries.py")

        # Export
        st.divider()
        frames = []
        for account, entries in shown:
            frame = ReportGenerator.general_ledger_to_dataframe(entries)
            frame.insert(0, "Account", account.display_name())
            frames.append(frame)
        df = pd.concat(frames, ignore_index=True)
        row_count = sum(len(entries) for _, entries in shown)
        export_scope = (shown[0][0].account_number if selected_account
                        else "all-accounts")

        buffer = BytesIO()
        sanitize_df(df).to_excel(buffer, index=False, sheet_name="General Ledger")
        buffer.seek(0)

        st.download_button(
            label="Download Excel",
            data=buffer,
            file_name=f"general_ledger_{client.name}_{export_scope}_{gl_start}_to_{gl_end}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            on_click=AuditLog.log_event,
            args=(client_id, "EXPORT", "general_ledger_export", {
                "format": "xlsx", "account_id": selected_account or "all",
                "start_date": gl_start, "end_date": gl_end, "row_count": row_count,
            }),
        )
