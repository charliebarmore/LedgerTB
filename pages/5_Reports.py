import streamlit as st
import sys
from pathlib import Path
from datetime import date
from io import BytesIO
from urllib.parse import urlencode

import pandas as pd

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.account import Account
from models.client import Client
from models.audit_log import AuditLog
from models.reports import CASH_FLOW_STATEMENT_SECTIONS, ReportGenerator
from database import init_database
from database import connection as dbconn
from utils.client_context import (
    book_scoped_key,
    pop_client_intent,
    scope_page_to_client,
    set_client_intent,
)
from utils.client_selector import render_client_selector
from utils.unlock import authorized_ui_token, require_unlock
from utils.dates import display_date, long_date
from services.preferences import get_date_format
from utils.ui import (
    apply_default_on_change,
    financial_statement,
    ledger_table,
    view_switcher,
)


from utils import icons
from utils.export import sanitize_df
from utils.fiscal_dates import fiscal_year_bounds
from utils.report_dates import (
    AS_OF_PRESETS,
    PERIOD_PRESETS,
    as_of_for_preset,
    period_for_preset,
)

# Initialize database

st.set_page_config(page_title="Reports", page_icon=icons.REPORTS, layout="wide")

# Client selector in sidebar
# Gate on the database passphrase before any DB access, then ensure schema.
require_unlock()
init_database()

def _query_value(name):
    value = st.query_params.get(name)
    if isinstance(value, list):
        return value[0] if value else None
    return value


# Every URL parameter a report link can carry. One signature over all of them
# is the "have I applied this URL yet" marker for the client below and for the
# report route further down, so Back/Forward always count as a new URL.
_ROUTE_FIELDS = (
    "report", "client_id", "account_id", "start", "end", "as_of", "return_report",
    "return_start", "return_end", "return_as_of",
)

# A new browser tab starts a new Streamlit session. Carry the selected client
# in report links and validate it against the open book before the sidebar
# selector renders, so a drill-down cannot silently land on the first client.
route_client_value = _query_value("client_id")
try:
    route_client_id = int(route_client_value) if route_client_value else None
except (TypeError, ValueError):
    route_client_id = None
route_client = Client.get_by_id(route_client_id) if route_client_id else None
# Apply the URL's client once per URL, mirroring the report-route guard below.
# Reapplying on every rerun would override the user's own sidebar selection
# for the rest of the session after a drill-down. The marker key is
# book-scoped: the same URL against a different book is a different route.
_route_marker_key = book_scoped_key("_reports_client_route", dbconn.DATABASE_PATH)
_route_signature = tuple((name, _query_value(name)) for name in _ROUTE_FIELDS)
if st.session_state.get(_route_marker_key) != _route_signature:
    st.session_state[_route_marker_key] = _route_signature
    if route_client and route_client.is_active:
        st.session_state.selected_client_id = route_client_id
        st.session_state.client_selector = route_client_id

client_id = render_client_selector()

st.title("Reports")

if not client_id:
    st.warning("Please create a client first in the Clients page.")
    st.page_link("pages/0_Clients.py", label="Go to Clients →")
    st.stop()

# The user picked a different client than the URL's route: drop the stale
# route parameters (never the launch token) so a refresh cannot resurrect
# the old client or aim its account filters at the new client's books.
if route_client_id and client_id != route_client_id:
    for _name in _ROUTE_FIELDS:
        if _name in st.query_params:
            del st.query_params[_name]
    # Re-mark against the now-cleared URL, so browser Back to the old drill
    # URL reads as a new route and reapplies its client.
    st.session_state[_route_marker_key] = tuple(
        (name, _query_value(name)) for name in _ROUTE_FIELDS
    )

report_scope = scope_page_to_client(
    st.session_state, "reports", client_id, dbconn.DATABASE_PATH
)
if report_scope.changed:
    st.session_state.active_report = "Trial Balance"
    st.session_state.pop("_active_report_rendered", None)
    for key in ("gl_account_id", "gl_start_date", "gl_end_date"):
        st.session_state.pop(key, None)

report_key = report_scope.key

# Get client info
client = Client.get_by_id(client_id)
st.caption(f"Viewing: **{client.name}**")
today = date.today()
current_fy_start, _ = fiscal_year_bounds(today, client.fiscal_year_end_month)
date_format = get_date_format()


def _query_date(name):
    raw = _query_value(name)
    try:
        return date.fromisoformat(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _report_href(report, **values):
    params = {"report": report, "client_id": client_id}
    params.update({
        key: value.isoformat() if isinstance(value, date) else value
        for key, value in values.items()
        if value is not None
    })
    ui_token = authorized_ui_token()
    if ui_token:
        params["t"] = ui_token
    return "?" + urlencode(params)


def _drill_href(
    account_id, start_date, end_date, return_report,
    *, return_start=None, return_end=None, return_as_of=None,
):
    return _report_href(
        "General Ledger",
        account_id=account_id,
        start=start_date,
        end=end_date,
        return_report=return_report,
        return_start=return_start,
        return_end=return_end,
        return_as_of=return_as_of,
    )


def _as_of_selector(prefix, label="As of Date"):
    preset_col, date_col, _ = st.columns([1.2, 1, 2])
    with preset_col:
        preset = st.selectbox(
            "Date preset",
            AS_OF_PRESETS,
            key=report_key(f"{prefix}_date_preset"),
        )
    selected = as_of_for_preset(
        preset, today, client.fiscal_year_end_month
    )
    date_key = report_key(f"{prefix}_date")
    if selected is not None:
        apply_default_on_change(
            date_key, (client_id, preset, today), selected
        )
    date_value = {} if date_key in st.session_state else {
        "value": selected or today
    }
    with date_col:
        return st.date_input(
            label,
            key=date_key,
            format=date_format,
            disabled=selected is not None,
            **date_value,
        )


def _period_selector(prefix, default_preset="This Fiscal Year"):
    preset_col, start_col, end_col, _ = st.columns([1.2, 1, 1, 1])
    with preset_col:
        preset = st.selectbox(
            "Date range",
            PERIOD_PRESETS,
            index=PERIOD_PRESETS.index(default_preset),
            key=report_key(f"{prefix}_date_preset"),
        )
    selected = period_for_preset(
        preset, today, client.fiscal_year_end_month
    )
    start_key = report_key(f"{prefix}_start")
    end_key = report_key(f"{prefix}_end")
    if selected is not None:
        apply_default_on_change(
            start_key, (client_id, preset, today), selected[0]
        )
        apply_default_on_change(
            end_key, (client_id, preset, today), selected[1]
        )
    start_value_arg = {} if start_key in st.session_state else {
        "value": selected[0] if selected else current_fy_start
    }
    end_value_arg = {} if end_key in st.session_state else {
        "value": selected[1] if selected else today
    }
    with start_col:
        start_value = st.date_input(
            "Start Date",
            key=start_key,
            format=date_format,
            disabled=selected is not None,
            **start_value_arg,
        )
    with end_col:
        end_value = st.date_input(
            "End Date",
            key=end_key,
            format=date_format,
            disabled=selected is not None,
            **end_value_arg,
        )
    return start_value, end_value

# Track active report in session state for sidebar navigation
if 'active_report' not in st.session_state:
    st.session_state.active_report = "Trial Balance"

report_options = [
    "Trial Balance", "Income Statement", "Balance Sheet", "Cash Flow",
    "General Ledger",
]

report_intent = pop_client_intent(
    st.session_state, "report", client_id, dbconn.DATABASE_PATH
)
if isinstance(report_intent, dict):
    requested_report = report_intent.get("report")
    if requested_report in report_options:
        st.session_state.active_report = requested_report
        st.session_state.pop("_active_report_rendered", None)
        if requested_report == "General Ledger":
            st.session_state.gl_account_id = report_intent.get("account_id")
            st.session_state.gl_start_date = report_intent.get(
                "start_date", current_fy_start
            )
            st.session_state.gl_end_date = report_intent.get(
                "end_date", today
            )
            nested_return = report_intent.get("return_route")
            if isinstance(nested_return, dict):
                st.session_state[report_key("return_route")] = nested_return

# Account lines are ordinary links so they support browser Back, right-click,
# and Command/Ctrl-click into a new tab. Apply a URL route only once per URL;
# otherwise its original account and dates would overwrite deliberate changes
# to the General Ledger filters on every Streamlit rerun.
route_signature = tuple((name, _query_value(name)) for name in _ROUTE_FIELDS)
route_key = report_key("query_route")
if st.session_state.get(route_key) != route_signature:
    st.session_state[route_key] = route_signature
    route_report = _query_value("report")
    if route_report in report_options:
        st.session_state.active_report = route_report
        st.session_state.pop("_active_report_rendered", None)
        route_start = _query_date("start")
        route_end = _query_date("end")
        route_as_of = _query_date("as_of")

        if route_report == "General Ledger":
            raw_account = _query_value("account_id")
            try:
                st.session_state.gl_account_id = int(raw_account)
            except (TypeError, ValueError):
                st.session_state.gl_account_id = None
            st.session_state.gl_start_date = route_start or current_fy_start
            st.session_state.gl_end_date = route_end or today
            return_report = _query_value("return_report")
            if return_report in report_options and return_report != "General Ledger":
                st.session_state[report_key("return_route")] = {
                    "report": return_report,
                    "start": _query_date("return_start"),
                    "end": _query_date("return_end"),
                    "as_of": _query_date("return_as_of"),
                }
        elif route_report in {"Income Statement", "Cash Flow"}:
            prefix = "is" if route_report == "Income Statement" else "cf"
            st.session_state[report_key(f"{prefix}_date_preset")] = "Custom"
            if route_start:
                st.session_state[report_key(f"{prefix}_start")] = route_start
            if route_end:
                st.session_state[report_key(f"{prefix}_end")] = route_end
        elif route_report in {"Trial Balance", "Balance Sheet"}:
            prefix = "tb" if route_report == "Trial Balance" else "bs"
            st.session_state[report_key(f"{prefix}_date_preset")] = "Custom"
            if route_as_of:
                st.session_state[report_key(f"{prefix}_date")] = route_as_of
    elif not route_report:
        # Browser Back removes the drill-down query string. Restore the report
        # that created the link, matching the explicit Back control below.
        prior_route = st.session_state.get(report_key("return_route"))
        if isinstance(prior_route, dict) and prior_route.get("report") in report_options:
            prior_report = prior_route["report"]
            st.session_state.active_report = prior_report
            st.session_state.pop("_active_report_rendered", None)
            if prior_report in {"Income Statement", "Cash Flow"}:
                prefix = "is" if prior_report == "Income Statement" else "cf"
                st.session_state[report_key(f"{prefix}_date_preset")] = "Custom"
                if prior_route.get("start"):
                    st.session_state[report_key(f"{prefix}_start")] = prior_route["start"]
                if prior_route.get("end"):
                    st.session_state[report_key(f"{prefix}_end")] = prior_route["end"]
            elif prior_report in {"Trial Balance", "Balance Sheet"}:
                prefix = "tb" if prior_report == "Trial Balance" else "bs"
                st.session_state[report_key(f"{prefix}_date_preset")] = "Custom"
                if prior_route.get("as_of"):
                    st.session_state[report_key(f"{prefix}_date")] = prior_route["as_of"]

# Report selector (segmented tabs; programmatically controllable via
# st.session_state.active_report from the sidebar quick links and GL drills).
selected_report = view_switcher(report_options, key="active_report",
                                label="Select Report")

st.divider()

if selected_report == "Trial Balance":
    st.subheader("Trial Balance")

    as_of_date = _as_of_selector("tb")

    rows = ReportGenerator.trial_balance(client_id, as_of_date)
    comparison = ReportGenerator.comparative_trial_balance(client_id, as_of_date)
    apply_default_on_change(
        report_key("tb_compare_py"),
        (client_id, as_of_date, comparison['prior_available']),
        comparison['prior_available'],
    )
    compare_py = st.toggle(
        "Compare to prior year",
        disabled=not comparison['prior_available'],
        key=report_key("tb_compare_py"),
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
        gl_start = fiscal_year_bounds(
            as_of_date, client.fiscal_year_end_month
        )[0]

        st.markdown(f"**As of {long_date(as_of_date)}**")
        st.caption(
            "Click an account to open its ledger. Use your browser's Back "
            "button to return to this report."
        )
        if compare_py:
            statement_rows = [
                ("item", f"{row['account_number']} - {row['name']}",
                 [row['current_debit'] or None, row['current_credit'] or None,
                  row['prior_debit'] or None, row['prior_credit'] or None],
                 row['type'],
                 _drill_href(
                     account_id_lookup[row['account_number']], gl_start,
                     as_of_date, "Trial Balance", return_as_of=as_of_date,
                 ) if row['account_number'] in account_id_lookup else None)
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
                 row.account_type,
                 _drill_href(
                     account_id_lookup[row.account_number], gl_start,
                     as_of_date, "Trial Balance", return_as_of=as_of_date,
                 ) if row.account_number in account_id_lookup else None)
                for row in rows
            ]
            statement_rows.append(("total", "Totals", [total_debits, total_credits]))
            financial_statement(statement_rows, headers=["Debit", "Credit"])

        if abs(total_debits - total_credits) < 0.01:
            st.success("Trial balance is in balance.")
        else:
            st.error(f"Trial balance is OUT OF BALANCE by ${abs(total_debits - total_credits):,.2f}")

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

    is_start, is_end = _period_selector("is")

    if is_start > is_end:
        st.error("Income statement start date cannot be after the end date.")
        st.stop()

    report = ReportGenerator.comparative_income_statement(
        client_id, is_start, is_end
    )
    apply_default_on_change(
        report_key("is_compare_py"),
        (client_id, is_start, is_end, report['prior_available']),
        report['prior_available'],
    )
    compare_py = st.toggle(
        "Compare to prior year",
        disabled=not report['prior_available'],
        key=report_key("is_compare_py"),
    )
    if not report['prior_available']:
        st.caption("No prior-year book history is available for this period.")

    has_unclassified_is = any(
        group['key'] == 'unclassified'
        for group in report['revenue_groups'] + report['expense_groups']
    )
    apply_default_on_change(
        report_key("is_group_subtypes"),
        (client_id, is_start, is_end, has_unclassified_is),
        not has_unclassified_is,
    )
    group_is = st.toggle(
        "Group by statement subtype",
        key=report_key("is_group_subtypes"),
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
    st.caption(
        "Click an account to open its ledger. Use your browser's Back "
        "button to return to this report."
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
    line_href_lookup = {
        f"{item['account_number']} - {item['name']}": _drill_href(
            account_id_lookup[item['account_number']], is_start, is_end,
            "Income Statement", return_start=is_start, return_end=is_end,
        )
        for item in revenue_lines + expense_lines
        if item['account_number'] in account_id_lookup
    }
    statement_rows = []
    for kind, label, value in ReportGenerator.income_statement_rows(
        layout_report, grouped=group_is
    ):
        statement_rows.append((
            'subtotal' if kind == 'group_total' else kind,
            label,
            [] if value is None else _amounts(value),
            None,
            line_href_lookup.get(label) if kind == "item" else None,
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

    bs_date = _as_of_selector("bs")

    report = ReportGenerator.comparative_balance_sheet(client_id, bs_date)
    apply_default_on_change(
        report_key("bs_compare_py"),
        (client_id, bs_date, report['prior_available']),
        report['prior_available'],
    )
    compare_py = st.toggle(
        "Compare to prior year",
        disabled=not report['prior_available'],
        key=report_key("bs_compare_py"),
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
        report_key("bs_group_subtypes"),
        (client_id, bs_date, has_unclassified_bs),
        not has_unclassified_bs,
    )
    group_bs = st.toggle(
        "Group by statement subtype",
        key=report_key("bs_group_subtypes"),
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
    st.caption(
        "Click an account to open its ledger. Use your browser's Back "
        "button to return to this report."
    )
    bs_gl_start = fiscal_year_bounds(
        bs_date, client.fiscal_year_end_month
    )[0]

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

    def _bs_item_row(item):
        account_number = item['account_number']
        label = (
            f"{account_number} - {item['name']}"
            if account_number else item['name']
        )
        href = None
        if account_number and account_number in account_id_lookup:
            href = _drill_href(
                account_id_lookup[account_number], bs_gl_start, bs_date,
                "Balance Sheet", return_as_of=bs_date,
            )
        return ("item", label, _bs_amounts(item), None, href)

    def _section(title, groups, flat_items, subtotal_label, subtotal_value):
        rows = [("section", title, [])]
        if group_bs:
            visible_groups = _visible_bs_groups(groups)
            for group in visible_groups:
                rows.append(("group", group['group'], []))
                rows.extend(_bs_item_row(item) for item in group['accounts'])
                rows.append((
                    "subtotal", f"Total {group['group']}",
                    _bs_amounts(group['subtotal']),
                ))
            has_lines = bool(visible_groups)
        else:
            rows.extend(_bs_item_row(item) for item in flat_items)
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

    cf_start, cf_end = _period_selector("cf")

    if cf_start > cf_end:
        st.error("Cash flow statement start date cannot be after the end date.")
        st.stop()

    report = ReportGenerator.comparative_cash_flow_statement(
        client_id, cf_start, cf_end
    )
    apply_default_on_change(
        report_key("cf_compare_py"),
        (client_id, cf_start, cf_end, report['prior_available']),
        report['prior_available'],
    )
    compare_py = st.toggle(
        "Compare to prior year",
        disabled=not report['prior_available'],
        key=report_key("cf_compare_py"),
    )
    if not report['prior_available']:
        st.caption("No prior-year book history is available for this period.")

    st.markdown(f"**{long_date(cf_start)} to {long_date(cf_end)}**")
    st.caption(
        "Click a single-account line to open its ledger. Use your browser's "
        "Back button to return to this report."
    )
    accounts = Account.get_all(client_id, active_only=False)
    account_by_id = {account.id: account for account in accounts}

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
            for line in lines:
                account_id = line.get('account_id')
                account_ids = line.get('account_ids') or []
                if not account_id and len(account_ids) == 1:
                    account_id = account_ids[0]
                href = None
                if account_id in account_by_id:
                    href = _drill_href(
                        account_id, cf_start, cf_end, "Cash Flow",
                        return_start=cf_start, return_end=cf_end,
                    )
                rows.append((
                    "item", line['name'], _cf_amounts(line), None, href
                ))
        else:
            rows.append(("note", f"No {title.lower()} recorded", []))
        rows.append(("subtotal", total_label, _cf_amounts(section['total'])))
        return rows

    statement_rows = []
    for title, key, total_label in CASH_FLOW_STATEMENT_SECTIONS:
        statement_rows += _cf_section(title, report[key], total_label)
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
                    f"{display_date(item['entry_date'], date_format)} · "
                    f"Entry #{item['entry_id']} · "
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
                    f"{display_date(item['entry_date'], date_format)} · "
                    f"Entry #{item['entry_id']} · "
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
                    f"{display_date(item['entry_date'], date_format)} · "
                    f"Entry #{item['entry_id']} · "
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
                    f"{display_date(item['entry_date'], date_format)} · "
                    f"Entry #{item['entry_id']} · "
                    f"{item['description'] or 'No description'} · "
                    f"Accounts {accounts_text} · ${item['amount']:,.2f}"
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

    return_route = st.session_state.get(report_key("return_route"))
    if isinstance(return_route, dict) and return_route.get("report") in report_options:
        return_params = {
            "report": return_route["report"], "client_id": client_id,
        }
        if return_route.get("start"):
            return_params["start"] = return_route["start"].isoformat()
        if return_route.get("end"):
            return_params["end"] = return_route["end"].isoformat()
        if return_route.get("as_of"):
            return_params["as_of"] = return_route["as_of"].isoformat()
        ui_token = authorized_ui_token()
        if ui_token:
            return_params["t"] = ui_token
        st.page_link(
            "pages/5_Reports.py",
            label=f"← Back to {return_route['report']}",
            query_params=return_params,
        )

    # Account selection
    accounts = Account.get_all(client_id, active_only=False)
    account_options = {
        a.id: a.display_name() + (" (inactive)" if not a.is_active else "")
        for a in accounts
    }

    # A report link or sidebar intent seeds the actual keyed widgets once. The
    # account widget then owns its value independently of the dates, so changing
    # the period cannot fall back to All accounts.
    account_filter_key = report_key("gl_account_filter")
    pending_account = st.session_state.pop("gl_account_id", None)
    pending_start = st.session_state.pop("gl_start_date", None)
    pending_end = st.session_state.pop("gl_end_date", None)
    if pending_account is not None:
        st.session_state[account_filter_key] = (
            pending_account if pending_account in account_options else None
        )
    if pending_start is not None or pending_end is not None:
        st.session_state[report_key("gl_date_preset")] = "Custom"
        if pending_start is not None:
            st.session_state[report_key("gl_start")] = pending_start
        if pending_end is not None:
            st.session_state[report_key("gl_end")] = pending_end

    gl_start, gl_end = _period_selector("gl")

    account_col, _ = st.columns([1.5, 2.5])
    with account_col:
        if account_options:
            selected_account = st.selectbox(
                "Account filter",
                options=list(account_options.keys()),
                format_func=lambda x: account_options[x],
                index=None,
                placeholder="All accounts",
                key=account_filter_key,
            )
        else:
            st.warning("No accounts available")
            selected_account = None

    if gl_start > gl_end:
        st.error("General ledger start date cannot be after the end date.")
        st.stop()

    hide_reversed_imports = st.checkbox(
        "Hide fully reversed import corrections",
        value=True,
        help=("Hides an original imported entry and its reversal only when both "
              "are inside this date range. Replacement entries stay visible."),
        key=report_key("gl_hide_reversed_imports"),
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
                       f"{display_date(gl_start, date_format)} – "
                       f"{display_date(gl_end, date_format)}")

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
                    [display_date(e.entry_date, date_format),
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
                e.entry_id: (f"#{e.entry_id} · "
                             f"{display_date(e.entry_date, date_format)} · "
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
                    key=report_key("gl_open_entry_pick"),
                )
            with oc2:
                st.write("")
                if st.button(
                    "Open entry →", width="stretch",
                    key=report_key("gl_open_entry"),
                ):
                    set_client_intent(
                        st.session_state,
                        "journal",
                        {
                            "entry_id": picked_entry,
                            "view": "New Entry",
                            "return_report": {
                                "report": "General Ledger",
                                "account_id": selected_account,
                                "start_date": gl_start,
                                "end_date": gl_end,
                                "return_route": return_route,
                            },
                        },
                        client_id,
                        dbconn.DATABASE_PATH,
                    )
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
