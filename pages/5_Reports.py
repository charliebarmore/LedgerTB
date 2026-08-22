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
from utils.ui import (
    apply_default_on_change,
    financial_statement,
    ledger_table,
    view_switcher,
)


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

report_options = [
    "Trial Balance", "Income Statement", "Balance Sheet", "Cash Flow",
    "General Ledger",
]

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
    comparison = ReportGenerator.comparative_trial_balance(client_id, as_of_date)
    apply_default_on_change(
        "tb_compare_py", (client_id, as_of_date, comparison['prior_available']),
        comparison['prior_available'],
    )
    compare_py = st.toggle(
        "Compare to prior year",
        disabled=not comparison['prior_available'],
        key="tb_compare_py",
    )
    if not comparison['prior_available']:
        st.caption("No prior-year book history is available for this date.")

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
        if compare_py:
            statement_rows = [
                ("item", f"{row['account_number']} - {row['name']}",
                 [row['current_debit'] or None, row['current_credit'] or None,
                  row['prior_debit'] or None, row['prior_credit'] or None],
                 row['type'])
                for row in comparison['accounts']
            ]
            statement_rows.append((
                "total", "Totals",
                [comparison['current_total_debits'],
                 comparison['current_total_credits'],
                 comparison['prior_total_debits'],
                 comparison['prior_total_credits']],
            ))
            financial_statement(
                statement_rows,
                headers=["Current Dr", "Current Cr", "PY Dr", "PY Cr"],
            )
            st.caption(f"Prior year as of {long_date(comparison['prior_as_of'])}")
        else:
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
        df = (
            ReportGenerator.comparative_trial_balance_to_dataframe(comparison)
            if compare_py else ReportGenerator.trial_balance_to_dataframe(rows)
        )

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

    report = ReportGenerator.comparative_income_statement(
        client_id, is_start, is_end
    )
    apply_default_on_change(
        "is_compare_py",
        (client_id, is_start, is_end, report['prior_available']),
        report['prior_available'],
    )
    compare_py = st.toggle(
        "Compare to prior year",
        disabled=not report['prior_available'],
        key="is_compare_py",
    )
    if not report['prior_available']:
        st.caption("No prior-year book history is available for this period.")

    has_unclassified_is = any(
        group['key'] == 'unclassified'
        for group in report['revenue_groups'] + report['expense_groups']
    )
    apply_default_on_change(
        "is_group_subtypes",
        (client_id, is_start, is_end, has_unclassified_is),
        not has_unclassified_is,
    )
    group_is = st.toggle(
        "Group by statement subtype",
        key="is_group_subtypes",
        help=("Turn this off for the familiar flat statement. Accounts need a "
              "curated subtype before the grouped statement is fully useful."),
    )
    if has_unclassified_is and not group_is:
        st.info(
            "This statement is using the classic layout because one or more "
            "accounts still need a statement subtype. Review them on the Chart "
            "of Accounts page when you are ready to use grouped statements."
        )

    # Get accounts for drill-down
    accounts = Account.get_all(client_id, active_only=False)
    account_id_lookup = {a.account_number: a.id for a in accounts}

    st.markdown(
        f"**{long_date(is_start)} to {long_date(is_end)}**"
    )

    def _amounts(item):
        if not compare_py:
            return [item['current']]
        return [item['current'], item['prior'], item['change'],
                item['change_percent']]

    revenue_lines = (
        report['revenues'] if compare_py
        else [item for item in report['revenues'] if item['current'] != 0]
    )
    expense_lines = (
        report['expenses'] if compare_py
        else [item for item in report['expenses'] if item['current'] != 0]
    )

    def _visible_is_groups(groups):
        visible = []
        for group in groups:
            accounts = (
                group['accounts'] if compare_py else
                [item for item in group['accounts'] if item['current'] != 0]
            )
            if accounts:
                visible.append({**group, 'accounts': accounts})
        return visible

    revenue_groups = _visible_is_groups(report['revenue_groups'])
    expense_groups = _visible_is_groups(report['expense_groups'])
    for warning in report.get('statement_warnings', ()):
        st.warning(warning)

    layout_report = {
        **report,
        'revenues': revenue_lines,
        'expenses': expense_lines,
        'revenue_groups': revenue_groups,
        'expense_groups': expense_groups,
        # The warning is already shown with warning styling above the table.
        'statement_warnings': [],
    }
    statement_rows = []
    for kind, label, value in ReportGenerator.income_statement_rows(
        layout_report, grouped=group_is
    ):
        statement_rows.append((
            'subtotal' if kind == 'group_total' else kind,
            label,
            [] if value is None else _amounts(value),
        ))

    financial_statement(
        statement_rows,
        headers=["Current", "Prior Year", "$ Change", "% Change"]
        if compare_py else None,
        formats=["money", "money", "money", "percent"]
        if compare_py else None,
    )
    if compare_py:
        st.caption(
            f"Prior period: {long_date(report['prior_period']['start'])} to "
            f"{long_date(report['prior_period']['end'])}"
        )

    gl_drill_down(
        {account_id_lookup[r['account_number']]:
             f"{r['account_number']} - {r['name']}"
         for r in revenue_lines + expense_lines
         if r['account_number'] in account_id_lookup},
        key="is", start_date=is_start, end_date=is_end,
    )

    # Export
    st.divider()
    df = (
        ReportGenerator.comparative_income_statement_to_dataframe(
            report, grouped=group_is
        )
        if compare_py else ReportGenerator.income_statement_to_dataframe(
            ReportGenerator.income_statement(client_id, is_start, is_end),
            grouped=group_is,
        )
    )

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

    report = ReportGenerator.comparative_balance_sheet(client_id, bs_date)
    apply_default_on_change(
        "bs_compare_py", (client_id, bs_date, report['prior_available']),
        report['prior_available'],
    )
    compare_py = st.toggle(
        "Compare to prior year",
        disabled=not report['prior_available'],
        key="bs_compare_py",
    )
    if not report['prior_available']:
        st.caption("No prior-year book history is available for this date.")

    has_unclassified_bs = any(
        group['key'] == 'unclassified'
        for group in (
            report['asset_groups'] + report['liability_groups']
            + report['equity_groups']
        )
    )
    apply_default_on_change(
        "bs_group_subtypes",
        (client_id, bs_date, has_unclassified_bs),
        not has_unclassified_bs,
    )
    group_bs = st.toggle(
        "Group by statement subtype",
        key="bs_group_subtypes",
        help=("Turn this off for the familiar flat statement. Accounts need a "
              "curated subtype before the grouped statement is fully useful."),
    )
    if has_unclassified_bs and not group_bs:
        st.info(
            "This statement is using the classic layout because one or more "
            "accounts still need a statement subtype. Review them on the Chart "
            "of Accounts page when you are ready to use grouped statements."
        )

    # Get accounts for drill-down
    accounts = Account.get_all(client_id, active_only=False)
    account_id_lookup = {a.account_number: a.id for a in accounts}

    st.markdown(f"**As of {long_date(bs_date)}**")

    def _bs_amounts(item):
        if not compare_py:
            return [item['current']]
        return [item['current'], item['prior'], item['change'],
                item['change_percent']]

    def _bs_lines(items):
        return items if compare_py else [
            item for item in items if item['current'] != 0
        ]

    asset_lines = _bs_lines(report['assets'])
    liability_lines = _bs_lines(report['liabilities'])
    equity_lines = _bs_lines(report['equity'])

    def _visible_bs_groups(groups):
        visible = []
        for group in groups:
            accounts = (
                group['accounts'] if compare_py else
                [item for item in group['accounts'] if item['current'] != 0]
            )
            if accounts:
                visible.append({**group, 'accounts': accounts})
        return visible

    def _section(title, groups, flat_items, subtotal_label, subtotal_value):
        rows = [("section", title, [])]
        if group_bs:
            visible_groups = _visible_bs_groups(groups)
            for group in visible_groups:
                rows.append(("group", group['group'], []))
                rows.extend(
                    ("item", (f"{item['account_number']} - {item['name']}"
                              if item['account_number'] else item['name']),
                     _bs_amounts(item))
                    for item in group['accounts']
                )
                rows.append((
                    "subtotal", f"Total {group['group']}",
                    _bs_amounts(group['subtotal']),
                ))
            has_lines = bool(visible_groups)
        else:
            rows.extend(
                ("item", (f"{item['account_number']} - {item['name']}"
                          if item['account_number'] else item['name']),
                 _bs_amounts(item))
                for item in flat_items
            )
            has_lines = bool(flat_items)
        if not has_lines:
            rows.append(("note", f"No {title.lower()} recorded", []))
        rows.append(("total", subtotal_label, _bs_amounts(subtotal_value)))
        return rows

    statement_rows = (
        _section("Assets", report['asset_groups'], asset_lines,
                 "Total Assets", report['total_assets'])
        + _section("Liabilities", report['liability_groups'], liability_lines,
                   "Total Liabilities", report['total_liabilities'])
        + _section("Equity", report['equity_groups'], equity_lines,
                   "Total Equity", report['total_equity'])
        + [("total", "Total Liabilities & Equity",
            _bs_amounts(report['total_liabilities_equity']))]
    )
    financial_statement(
        statement_rows,
        headers=["Current", "Prior Year", "$ Change", "% Change"]
        if compare_py else None,
        formats=["money", "money", "money", "percent"]
        if compare_py else None,
    )
    if compare_py:
        st.caption(f"Prior year as of {long_date(report['prior_as_of'])}")

    if report['current_balanced']:
        st.success("Balance sheet is balanced.")
    else:
        st.error("Balance sheet is OUT OF BALANCE!")

    gl_drill_down(
        {account_id_lookup[e['account_number']]:
             f"{e['account_number']} - {e['name']}"
         for e in asset_lines + liability_lines + equity_lines
         if e['account_number'] and e['account_number'] in account_id_lookup},
        key="bs",
        start_date=fiscal_year_bounds(bs_date, client.fiscal_year_end_month)[0],
        end_date=bs_date,
    )

    # Export
    st.divider()
    df = (
        ReportGenerator.comparative_balance_sheet_to_dataframe(
            report, grouped=group_bs
        )
        if compare_py else ReportGenerator.balance_sheet_to_dataframe(
            ReportGenerator.balance_sheet(client_id, bs_date), grouped=group_bs
        )
    )

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

elif selected_report == "Cash Flow":
    st.subheader("Statement of Cash Flows")

    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        cf_start = st.date_input(
            "Start Date", value=current_fy_start, key="cf_start"
        )
    with col2:
        cf_end = st.date_input("End Date", value=date.today(), key="cf_end")

    if cf_start > cf_end:
        st.error("Cash flow statement start date cannot be after the end date.")
        st.stop()

    report = ReportGenerator.comparative_cash_flow_statement(
        client_id, cf_start, cf_end
    )
    apply_default_on_change(
        "cf_compare_py",
        (client_id, cf_start, cf_end, report['prior_available']),
        report['prior_available'],
    )
    compare_py = st.toggle(
        "Compare to prior year",
        disabled=not report['prior_available'],
        key="cf_compare_py",
    )
    if not report['prior_available']:
        st.caption("No prior-year book history is available for this period.")

    st.markdown(f"**{long_date(cf_start)} to {long_date(cf_end)}**")

    def _cf_amounts(item):
        if not compare_py:
            return [item['current']]
        return [
            item['current'], item['prior'], item['change'],
            item['change_percent'],
        ]

    def _cf_section(title, section, total_label):
        rows = [("section", title, [])]
        lines = (
            section['lines'] if compare_py else
            [line for line in section['lines'] if line['current'] != 0]
        )
        if lines:
            rows.extend(
                ("item", line['name'], _cf_amounts(line)) for line in lines
            )
        else:
            rows.append(("note", f"No {title.lower()} recorded", []))
        rows.append(("subtotal", total_label, _cf_amounts(section['total'])))
        return rows

    statement_rows = (
        _cf_section(
            "Operating Activities", report['operating'],
            "Net Cash Provided by Operating Activities",
        )
        + _cf_section(
            "Investing Activities", report['investing'],
            "Net Cash Provided by Investing Activities",
        )
        + _cf_section(
            "Financing Activities", report['financing'],
            "Net Cash Provided by Financing Activities",
        )
    )
    show_unclassified = bool(
        report['unclassified']['lines']
        or report['unclassified']['current_entries']
        or (compare_py and report['unclassified']['prior_entries'])
    )
    if show_unclassified:
        statement_rows += _cf_section(
            "Unclassified Cash Activity", report['unclassified'],
            "Net Unclassified Cash Activity",
        )
    statement_rows += [
        ("total", "Net Change in Cash", _cf_amounts(report['computed_cash_change'])),
    ]
    reconciliation = report['reconciliation_difference']
    if reconciliation['current'] or (compare_py and reconciliation['prior']):
        statement_rows.append((
            "item", "Cash Flow Reconciliation Difference",
            _cf_amounts(reconciliation),
        ))
    statement_rows.extend([
        ("item", "Cash at Beginning of Period", _cf_amounts(report['cash_beginning'])),
        ("total", "Cash at End of Period", _cf_amounts(report['cash_ending'])),
    ])
    financial_statement(
        statement_rows,
        headers=["Current", "Prior Year", "$ Change", "% Change"]
        if compare_py else None,
        formats=["money", "money", "money", "percent"]
        if compare_py else None,
    )

    if report['current_ready']:
        st.success(
            "Cash flow is tied, the operating reconciliation agrees, and all "
            "cash activity is classified."
        )
    elif report['current_ties']:
        st.warning(
            "Cash movement ties, but the statement still has classification "
            "or operating-reconciliation items to review."
        )
    else:
        st.error("Cash flow does not tie to the Cash-subtype account balances.")
    for warning in report['current_warnings']:
        st.caption(f"• {warning}")

    if compare_py:
        if report['prior_ready']:
            st.success(
                "Prior-year cash flow is tied, reconciled, and fully classified."
            )
        else:
            st.warning(
                "Prior-year cash flow has items to review; see its warnings below."
            )
        for warning in report['prior_warnings']:
            st.caption(f"• Prior year: {warning}")

    if report['unclassified']['current_entries']:
        with st.expander(
            "Unclassified cash activity details "
            f"({len(report['unclassified']['current_entries'])})"
        ):
            for item in report['unclassified']['current_entries']:
                accounts_text = ", ".join(item['account_numbers']) or "none"
                st.write(
                    f"{item['entry_date']} · Entry #{item['entry_id']} · "
                    f"{item['reason']} · "
                    f"{item['description'] or 'No description'} · "
                    f"Accounts {accounts_text} · ${item['amount']:,.2f}"
                )

    if compare_py and report['unclassified']['prior_entries']:
        with st.expander(
            "Prior-year unclassified cash activity details "
            f"({len(report['unclassified']['prior_entries'])})"
        ):
            for item in report['unclassified']['prior_entries']:
                accounts_text = ", ".join(item['account_numbers']) or "none"
                st.write(
                    f"{item['entry_date']} · Entry #{item['entry_id']} · "
                    f"{item['reason']} · "
                    f"{item['description'] or 'No description'} · "
                    f"Accounts {accounts_text} · ${item['amount']:,.2f}"
                )

    if report['current_noncash_items']:
        with st.expander(
            f"Noncash investing and financing activity "
            f"({len(report['current_noncash_items'])})"
        ):
            for item in report['current_noncash_items']:
                accounts_text = ", ".join(item['accounts'])
                st.write(
                    f"{item['entry_date']} · Entry #{item['entry_id']} · "
                    f"{item['description'] or 'No description'} · "
                    f"Accounts {accounts_text} · ${item['amount']:,.2f}"
                )

    if compare_py and report['prior_noncash_items']:
        with st.expander(
            f"Prior-year noncash investing and financing activity "
            f"({len(report['prior_noncash_items'])})"
        ):
            for item in report['prior_noncash_items']:
                accounts_text = ", ".join(item['accounts'])
                st.write(
                    f"{item['entry_date']} · Entry #{item['entry_id']} · "
                    f"{item['description'] or 'No description'} · "
                    f"Accounts {accounts_text} · ${item['amount']:,.2f}"
                )

    accounts = Account.get_all(client_id, active_only=False)
    account_by_id = {account.id: account for account in accounts}
    drill_options = {}
    for section_name in ('operating', 'investing', 'financing', 'unclassified'):
        for line in report[section_name]['lines']:
            account_id = line.get('account_id')
            account_ids = line.get('account_ids') or []
            if not account_id and len(account_ids) == 1:
                account_id = account_ids[0]
            if account_id in account_by_id:
                drill_options[account_id] = account_by_id[account_id].display_name()
    gl_drill_down(
        drill_options, key="cf", start_date=cf_start, end_date=cf_end
    )

    st.divider()
    if compare_py:
        df = ReportGenerator.comparative_cash_flow_statement_to_dataframe(report)
    else:
        current_report = ReportGenerator.cash_flow_statement(
            client_id, cf_start, cf_end
        )
        df = ReportGenerator.cash_flow_statement_to_dataframe(current_report)

    buffer = BytesIO()
    sanitize_df(df).to_excel(buffer, index=False, sheet_name="Cash Flow")
    buffer.seek(0)
    st.download_button(
        label="Download Excel",
        data=buffer,
        file_name=f"cash_flow_{client.name}_{cf_start}_to_{cf_end}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        on_click=AuditLog.log_event,
        args=(client_id, "EXPORT", "cash_flow_export", {
            "format": "xlsx", "start_date": cf_start, "end_date": cf_end,
            "row_count": len(df),
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

    hide_reversed_imports = st.checkbox(
        "Hide fully reversed import corrections",
        value=True,
        help=("Hides an original imported entry and its reversal only when both "
              "are inside this date range. Replacement entries stay visible."),
        key="gl_hide_reversed_imports",
    )
    st.caption(
        "This changes only the on-screen view. Excel downloads always include "
        "the complete ledger and correction labels."
    )

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
        displayed = []
        hidden_row_count = 0
        for account, entries in shown:
            if hide_reversed_imports:
                visible_entries, hidden_count = (
                    ReportGenerator.compact_reversed_import_entries(
                        entries, account.type
                    )
                )
                hidden_row_count += hidden_count
            else:
                visible_entries = entries
            has_visible_activity = any(e.entry_id for e in visible_entries)
            carries_visible_balance = (
                bool(visible_entries) and visible_entries[-1].balance != 0
            )
            if has_visible_activity or carries_visible_balance:
                displayed.append((account, visible_entries))

        if hidden_row_count:
            st.caption(
                f"{hidden_row_count} original/reversal ledger rows hidden from "
                "this view."
            )

        if not displayed:
            st.info(
                "All activity in this period is from fully reversed imports. "
                "Uncheck the option above to see the complete accounting detail."
            )
        elif not selected_account:
            st.caption(f"{len(displayed)} accounts with activity or balances · "
                       f"{gl_start} – {gl_end}")

        open_options = {}
        for account, entries in displayed:
            period_debits = sum(e.debit for e in entries if e.entry_id != 0)
            period_credits = sum(e.credit for e in entries if e.entry_id != 0)
            final_balance = entries[-1].balance if entries else 0

            if not selected_account:
                st.markdown(f"**{account.display_name()}**")
            ledger_table(
                headers=["Date", "Entry #", "Description", "Import correction",
                         "Reference",
                         "Debit", "Credit", "Balance"],
                rows=[
                    [e.entry_date.isoformat(),
                     f"#{e.entry_id}" if e.entry_id else "",
                     (e.description or "")[:48],
                     e.import_correction_label,
                     (e.source_reference or "")[:24],
                     f"{e.debit:,.2f}" if e.debit > 0 else "",
                     f"{e.credit:,.2f}" if e.credit > 0 else "",
                     f"{e.balance:,.2f}"]
                    for e in entries
                ],
                align=["l", "l", "l", "l", "l", "r", "r", "r"],
                total_row=["", "", "Period totals · ending balance", "", "",
                           f"${period_debits:,.2f}", f"${period_credits:,.2f}",
                           f"${final_balance:,.2f}"],
                row_classes=[
                    ("muted" if e.is_reversed_import_detail else "")
                    for e in entries
                ],
            )
            open_options.update({
                e.entry_id: (f"#{e.entry_id} · {e.entry_date} · "
                             f"{(e.description or '')[:34]}"
                             f"{' · ' + e.import_correction_label if e.import_correction_label else ''}")
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
