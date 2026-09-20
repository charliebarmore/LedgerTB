"""
Trial Balance Worksheet - Primary CPA working view

This page displays a comprehensive trial balance worksheet with columns for:
- Beginning Balance
- Period Activity (Debits/Credits)
- Unadjusted Trial Balance
- Adjusting Journal Entries (AJEs)
- Adjusted Trial Balance
"""

import streamlit as st
import sys
from pathlib import Path
from datetime import date

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from database import init_database
from database import connection as dbconn
from utils.client_context import scope_page_to_client, set_client_intent
from utils.client_selector import render_client_selector
from utils.ui import apply_default_on_change
from utils.unlock import require_unlock
from utils import icons
from utils.recovery import save_error_message
from utils.fiscal_dates import fiscal_year_ending_year
from models.client import Client
from models.fiscal_period import FiscalPeriod
from models.reports import ReportGenerator
from models.journal_entry import JournalEntry, JournalEntryLine
from models.account import Account
from models.audit_log import AuditLog
from services.worksheet_export_cache import worksheet_exports

from services.preferences import get_date_format


st.set_page_config(
    page_title="Trial Balance Worksheet",
    page_icon=icons.TRIAL_BALANCE,
    layout="wide"
)

# Render client selector
# Gate on the database passphrase before any DB access, then ensure schema.
require_unlock()
init_database()
date_format = get_date_format()

client_id = render_client_selector()

if not client_id:
    st.warning("Please select or create a client first.")
    st.stop()

worksheet_scope = scope_page_to_client(
    st.session_state, "trial_balance_worksheet", client_id,
    dbconn.DATABASE_PATH,
)
if worksheet_scope.changed:
    # Period ids and every unsaved AJE value belong to the prior client.
    # Mutation widgets use the generation-scoped keys below so the browser
    # cannot restore their old values after these Python values are cleared.
    for key in (
        "selected_period_id",
        "last_year",
        "show_aje_form",
        "aje_prefill_account",
        "_worksheet_exports",
    ):
        st.session_state.pop(key, None)

worksheet_key = worksheet_scope.key

client = Client.get_by_id(client_id)
if not client:
    st.error("Client not found.")
    st.stop()

st.title("Trial Balance Worksheet")
st.caption(f"Viewing: **{client.name}**")
if dbconn.READ_ONLY:
    st.info("Read-only book. You can view the worksheet; year setup, closing and journal changes are disabled.")

# Period Selection
st.markdown("---")

col1, col2, col3, col4 = st.columns([2, 1, 1, 1])

with col1:
    # Get current year and ensure periods exist
    fiscal_year_end = client.fiscal_year_end_month
    current_year = fiscal_year_ending_year(date.today(), fiscal_year_end)

    # Check available years from existing periods
    periods = FiscalPeriod.get_all(client_id)

    # Get unique years from period names
    available_years = set()
    for p in periods:
        if p.period_type == "Year":
            try:
                year_str = p.period_name.replace("FY ", "")
                available_years.add(int(year_str))
            except ValueError:
                pass

    # Add current year if not present
    if current_year not in available_years and not dbconn.READ_ONLY:
        FiscalPeriod.ensure_periods_exist(client_id, current_year, fiscal_year_end)
        available_years.add(current_year)

    if not available_years:
        st.info("No fiscal year has been set up. Reopen with editing access to add a year.")
        st.stop()

    # Allow user to add other years (show last 5 years as options).
    # bottom-align so the "Add Year" popover lines up with the selectbox box
    # (it has no label above it, unlike the selectbox).
    year_col1, year_col2 = st.columns([3, 1], vertical_alignment="bottom")

    with year_col1:
        # Sort years descending
        years_list = sorted(available_years, reverse=True)

        selected_year = st.selectbox(
            "Fiscal Year",
            options=years_list,
            index=0,
            key=worksheet_key("worksheet_year")
        )

    with year_col2:
        # Add year button with popover for year selection
        with st.popover("+ Add Year"):
            # Show years that aren't already available
            potential_years = [y for y in range(current_year, current_year - 10, -1) if y not in available_years]
            if potential_years:
                new_year = st.selectbox(
                    "Select year to add", options=potential_years,
                    key=worksheet_key("add_year_select"),
                )
                if st.button(
                    "Add Fiscal Year", key=worksheet_key("add_year_btn"), disabled=dbconn.READ_ONLY
                ):
                    FiscalPeriod.generate_periods(client_id, new_year, fiscal_year_end)
                    st.success(f"Added FY {new_year}")
                    st.rerun()
            else:
                st.info("All recent years are already available")

    # Ensure periods exist for selected year
    if not dbconn.READ_ONLY:
        FiscalPeriod.ensure_periods_exist(client_id, selected_year, fiscal_year_end)

# Get periods for selected year
all_periods = FiscalPeriod.get_all(client_id)
year_periods = [p for p in all_periods if f"FY {selected_year}" in p.period_name]

# Sort by period type then by start date
type_order = {"Year": 0, "Quarter": 1, "Month": 2, "Custom": 3}
year_periods.sort(key=lambda x: (type_order.get(x.period_type, 4), x.start_date))

with col2:
    period_options = {p.id: f"{p.period_name} ({p.start_date.strftime('%m/%d/%y')} - {p.end_date.strftime('%m/%d/%y')})" for p in year_periods}

    if not period_options:
        st.warning("No periods available")
        st.stop()

    # Default to year period
    default_period = next((p for p in year_periods if p.period_type == "Year"), year_periods[0])

    if 'selected_period_id' not in st.session_state or st.session_state.get('last_year') != selected_year:
        st.session_state.selected_period_id = default_period.id
        st.session_state.last_year = selected_year

    selected_period_id = st.selectbox(
        "Period",
        options=list(period_options.keys()),
        format_func=lambda x: period_options[x],
        index=list(period_options.keys()).index(st.session_state.selected_period_id) if st.session_state.selected_period_id in period_options else 0,
        key=worksheet_key("period_selector")
    )
    st.session_state.selected_period_id = selected_period_id

selected_period = FiscalPeriod.get_by_id(selected_period_id)

# Keyed date inputs ignore value= once they hold state, so picking a new
# Period would silently leave the old dates (and numbers) in place. Re-apply
# the period's dates only when the period actually changes — a hand-edited
# range survives everything else.
period_start_key = worksheet_key("period_start")
period_end_key = worksheet_key("period_end")
apply_default_on_change(period_start_key, depends_on=selected_period_id,
                        default_value=selected_period.start_date)
apply_default_on_change(period_end_key, depends_on=selected_period_id,
                        default_value=selected_period.end_date)

with col3:
    period_start = st.date_input(
        "From",
        key=period_start_key,
        format=date_format,
    )

with col4:
    period_end = st.date_input(
        "To",
        key=period_end_key,
        format=date_format,
    )

if period_start > period_end:
    st.error("Worksheet period start date cannot be after the end date.")
    st.stop()

# Show all accounts toggle
show_all = st.checkbox(
    "Show all accounts (default: only accounts with activity)",
    value=False, key=worksheet_key("show_all_accounts"),
)

# Year close / reopen — locks all journal entries dated within the fiscal year
year_period = next((p for p in year_periods if p.period_type == "Year"), None)
if year_period:
    checklist = FiscalPeriod.get_close_checklist(year_period.id, client_id)
    st.subheader("Year-close checklist")
    check_cols = st.columns(5)
    with check_cols[0]:
        if checklist["trial_balance_balanced"]:
            st.success("Trial balance balanced")
        else:
            st.error("Trial balance out of balance")
        # \$ so markdown doesn't treat the paired $…$ as inline LaTeX.
        st.caption(
            f"Debits \\${checklist['total_debits']:,.2f} · "
            f"Credits \\${checklist['total_credits']:,.2f}"
        )
    with check_cols[1]:
        if checklist["pending_imports"]:
            st.warning(f"{checklist['pending_imports']} pending imports")
        else:
            st.success("No pending imports")
    with check_cols[2]:
        if checklist["uncategorized_items"]:
            st.warning(f"{checklist['uncategorized_items']} uncategorized items")
        else:
            st.success("No uncategorized items")
    with check_cols[3]:
        if checklist["unresolved_duplicates"]:
            st.warning(f"{checklist['unresolved_duplicates']} potential duplicates")
        else:
            st.success("No potential duplicates")
    with check_cols[4]:
        if checklist["close_map_incomplete"]:
            st.warning(f"{checklist['close_map_incomplete']} balances need review")
        else:
            st.success("Close Map ready")

    if checklist["warning_count"]:
        st.warning(
            "Outstanding items should be resolved before close. If they are understood and "
            "intentional, the close requires a separate acknowledgement."
        )
        if (checklist["pending_imports"] or checklist["uncategorized_items"] or
                checklist["unresolved_duplicates"]):
            st.page_link("pages/4_Import_Transactions.py", label="Review imports")
        if checklist["close_map_incomplete"]:
            st.page_link("pages/14_Close_Map.py", label="Review Close Map")

    if year_period.is_closed:
        lock_cols = st.columns([3, 1])
        with lock_cols[0]:
            st.warning(f"FY {selected_year} is closed. Entries in this year are locked.")
        with lock_cols[1]:
            if st.button(
                "Reopen year", key=worksheet_key("reopen_year"), disabled=dbconn.READ_ONLY,
                width="stretch",
            ):
                FiscalPeriod.set_closed(year_period.id, False, client_id)
                st.rerun()
    else:
        # Collapsed by default: closing the year is an occasional action, and
        # expanded it pushed the worksheet itself below the fold.
        with st.expander(f"Close fiscal year {selected_year} (FY is open)"):
            confirmation_phrase = f"CLOSE FY {selected_year}"
            close_confirmation = st.text_input(
                f"Type {confirmation_phrase} to confirm",
                key=worksheet_key("close_year_confirmation"),
                placeholder=confirmation_phrase,
            )
            warnings_acknowledged = not checklist["warning_count"] or st.checkbox(
                "I reviewed the outstanding items and accept closing with these warnings.",
                key=worksheet_key("close_warning_acknowledgement"),
            )
            close_map_exception_reason = ""
            if checklist["close_map_incomplete"]:
                close_map_exception_reason = st.text_area(
                    "Reason for closing with incomplete Close Map reviews",
                    key=worksheet_key("close_map_exception_reason"),
                    placeholder="Explain why these balances are intentionally being closed before review is complete.",
                )
            exception_reason_complete = (
                not checklist["close_map_incomplete"] or
                bool(close_map_exception_reason.strip())
            )
            explicitly_confirmed = close_confirmation.strip().upper() == confirmation_phrase
            if st.button(
                "Close fiscal year", key=worksheet_key("close_year"),
                type="primary",
                disabled=(dbconn.READ_ONLY or not explicitly_confirmed or not warnings_acknowledged or
                          not exception_reason_complete),
            ):
                try:
                    FiscalPeriod.set_closed(
                        year_period.id, True, client_id,
                        confirmation={
                            "explicit_confirmation": explicitly_confirmed,
                            "warnings_acknowledged": warnings_acknowledged,
                            "close_map_exception_reason": close_map_exception_reason,
                        },
                    )
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(save_error_message(exc))

st.markdown("---")

# Generate the trial balance worksheet
rows, aje_details = ReportGenerator.trial_balance_worksheet(
    client_id=client_id,
    period_start=period_start,
    period_end=period_end,
    show_all_accounts=show_all
)
py_comparison = ReportGenerator.comparative_trial_balance(client_id, period_end)
py_by_account = {
    row['account_number']: row for row in py_comparison['accounts']
}

if not rows:
    st.info("No transactions found for the selected period. Try selecting 'Show all accounts' or a different period.")

    # Still show action buttons
    col1, col2, col3 = st.columns([1, 1, 4])
    with col1:
        if st.button(
            "+ Add AJE", type="primary",
            key=worksheet_key("add_aje_empty"), disabled=dbconn.READ_ONLY,
        ):
            st.session_state.show_aje_form = True
            st.session_state.aje_prefill_account = None
else:
    # Calculate totals
    total_beg_dr = sum(r.beginning_dr for r in rows)
    total_beg_cr = sum(r.beginning_cr for r in rows)
    total_period_dr = sum(r.period_debits for r in rows)
    total_period_cr = sum(r.period_credits for r in rows)
    total_unadj_dr = sum(r.unadjusted_dr for r in rows)
    total_unadj_cr = sum(r.unadjusted_cr for r in rows)
    total_aje_dr = sum(r.aje_debits for r in rows)
    total_aje_cr = sum(r.aje_credits for r in rows)
    total_adj_dr = sum(r.adjusted_dr for r in rows)
    total_adj_cr = sum(r.adjusted_cr for r in rows)

    # Build a clean trial-balance table. Numbers are pre-formatted to strings
    # (commas, 2 decimals) with a dash for empty cells — Streamlit's dataframe
    # ignores a Styler's na_rep, so we format here rather than leave blanks.
    import pandas as pd

    def amt(v):
        return f"{v:,.2f}" if v and v > 0 else "-"

    def make_row(acct, name, beg_dr, beg_cr, act_dr, act_cr, un_dr, un_cr,
                 aje_dr, aje_cr, adj_dr, adj_cr, py_dr=None, py_cr=None):
        return {
            "Acct #": acct,
            "Account Name": name,
            "Beg Dr": amt(beg_dr), "Beg Cr": amt(beg_cr),
            "Activity Dr": amt(act_dr), "Activity Cr": amt(act_cr),
            "Unadj Dr": amt(un_dr), "Unadj Cr": amt(un_cr),
            "AJE Dr": amt(aje_dr), "AJE Cr": amt(aje_cr),
            "Adj Dr": amt(adj_dr), "Adj Cr": amt(adj_cr),
            "PY Final Dr": amt(py_dr), "PY Final Cr": amt(py_cr),
        }

    table = [
        make_row(r.account_number, r.account_name,
                 r.beginning_dr, r.beginning_cr, r.period_debits, r.period_credits,
                 r.unadjusted_dr, r.unadjusted_cr, r.aje_debits, r.aje_credits,
                 r.adjusted_dr, r.adjusted_cr,
                 py_by_account.get(r.account_number, {}).get('prior_debit'),
                 py_by_account.get(r.account_number, {}).get('prior_credit'))
        for r in rows
    ]
    current_account_numbers = {r.account_number for r in rows}
    table.extend(
        make_row(
            comparison_row['account_number'], comparison_row['name'],
            0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
            comparison_row.get('prior_debit'),
            comparison_row.get('prior_credit'),
        )
        for comparison_row in py_comparison['accounts']
        if comparison_row['account_number'] not in current_account_numbers
        and (comparison_row.get('prior_debit') or
             comparison_row.get('prior_credit'))
    )
    table.append(make_row(
        "", "TOTALS",
        total_beg_dr, total_beg_cr, total_period_dr, total_period_cr,
        total_unadj_dr, total_unadj_cr, total_aje_dr, total_aje_cr,
        total_adj_dr, total_adj_cr,
        py_comparison['prior_total_debits'], py_comparison['prior_total_credits']))

    df = pd.DataFrame(table)
    num_cols = ["Beg Dr", "Beg Cr", "Activity Dr", "Activity Cr",
                "Unadj Dr", "Unadj Cr", "AJE Dr", "AJE Cr", "Adj Dr", "Adj Cr",
                "PY Final Dr", "PY Final Cr"]

    if py_comparison['prior_available']:
        st.caption(f"PY final balances are as of {py_comparison['prior_as_of'].strftime('%m/%d/%Y')}.")
    else:
        st.caption("No prior-year book history is available for comparison.")

    # Right-align the numeric columns (best-effort; Streamlit honors text-align)
    styler = df.style.set_properties(subset=num_cols, **{"text-align": "right"})

    st.dataframe(
        styler,
        hide_index=True,
        width="stretch",
        height=min(len(df) * 36 + 40, 660),
        column_config={"Account Name": st.column_config.TextColumn(width="medium")},
    )

    # Drill into the general ledger for a chosen account (replaces the old
    # clickable account-name buttons)
    acct_label = {r.account_id: f"{r.account_number} - {r.account_name}" for r in rows}
    dd1, dd2 = st.columns([3, 1])
    with dd1:
        gl_pick = st.selectbox(
            "Drill into general ledger",
            options=list(acct_label.keys()),
            format_func=lambda aid: acct_label.get(aid, ""),
            key=worksheet_key("gl_drill_select"),
        )
    with dd2:
        st.write("")
        if st.button(
            "Open GL →", width="stretch", key=worksheet_key("open_gl_btn")
        ):
            set_client_intent(
                st.session_state,
                "report",
                {
                    "report": "General Ledger",
                    "account_id": gl_pick,
                    "start_date": period_start,
                    "end_date": period_end,
                },
                client_id,
                dbconn.DATABASE_PATH,
            )
            st.switch_page("pages/5_Reports.py")

    # AJE detail (previously shown inline in the AJE cells)
    if aje_details:
        with st.expander("AJE detail"):
            for aid, ajes in aje_details.items():
                name = acct_label.get(aid, str(aid))
                for aje in ajes:
                    side = (f"${aje['debit']:,.2f} Dr" if aje['debit'] > 0
                            else f"${aje['credit']:,.2f} Cr")
                    st.caption(f"{aje['aje_reference']} · {name}: {aje['description']} — {side}")

    # Balance check indicators
    st.markdown("---")
    check_cols = st.columns(4)

    with check_cols[0]:
        beg_diff = abs(total_beg_dr - total_beg_cr)
        if beg_diff < 0.01:
            st.success("Beginning Balance: Balanced")
        else:
            st.error(f"Beginning Balance: Out of balance by ${beg_diff:,.2f}")

    with check_cols[1]:
        unadj_diff = abs(total_unadj_dr - total_unadj_cr)
        if unadj_diff < 0.01:
            st.success("Unadjusted TB: Balanced")
        else:
            st.error(f"Unadjusted TB: Out of balance by ${unadj_diff:,.2f}")

    with check_cols[2]:
        aje_diff = abs(total_aje_dr - total_aje_cr)
        if aje_diff < 0.01:
            st.success("AJEs: Balanced")
        else:
            st.error(f"AJEs: Out of balance by ${aje_diff:,.2f}")

    with check_cols[3]:
        adj_diff = abs(total_adj_dr - total_adj_cr)
        if adj_diff < 0.01:
            st.success("Adjusted TB: Balanced")
        else:
            st.error(f"Adjusted TB: Out of balance by ${adj_diff:,.2f}")

st.markdown("---")

# Action buttons
exports = None
if rows:
    try:
        exports = worksheet_exports(st.session_state, client_id, period_start, period_end, show_all=show_all)
    except Exception:
        st.warning("Exports could not be prepared. Your worksheet is unchanged. Try Refresh again.")
if dbconn.READ_ONLY and rows:
    st.caption("Downloads from a read-only session are not added to the book’s audit history.")
# Content-sized actions wrap as a group on smaller desktop windows.
with st.container(horizontal=True, key="worksheet_actions"):

    if st.button(
        "+ Add AJE", type="primary", key=worksheet_key("add_aje_btn"), disabled=dbconn.READ_ONLY
    ):
        st.session_state.show_aje_form = True
        st.session_state.aje_prefill_account = None

    # Export to Excel with formulas
    if exports:
        output = exports["worksheet"]

        st.download_button(
            label="Export Excel",
            data=output,
            file_name=f"TB_Worksheet_{client.name}_{period_end.strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            on_click=None if dbconn.READ_ONLY else AuditLog.log_event,
            args=(client_id, "EXPORT", "trial_balance_worksheet_export", {
                "format": "xlsx", "period_start": period_start,
                "period_end": period_end, "row_count": len(rows),
            }),
        )

    # Everything needed to hand off a finished period: financial statements,
    # final TB, all transactions, AJEs, and cash-account activity.
    # PDF is the file/record copy; the Excel workbook is for further work.
    # (Replaced the old attest-claw bridge export.)
    if exports:
        pdf = exports["pdf"]
        package = exports["package"]
        export_rows = exports["close_rows"]
        st.download_button(
            label="Close Package (PDF)",
            data=pdf,
            file_name=f"ClosePackage_{client.name}_{period_end.strftime('%Y%m%d')}.pdf",
            mime="application/pdf",
            help="One PDF: summary with tie-outs, income statement, balance "
                 "sheet, final trial balance, transactions, adjusting entries, "
                 "receipts & disbursements",
            on_click=None if dbconn.READ_ONLY else AuditLog.log_event,
            args=(client_id, "EXPORT", "close_package_export", {
                "format": "pdf",
                "period_start": period_start, "period_end": period_end,
                "row_count": len(export_rows),
            }),
        )
        st.download_button(
            label="Close Package (Excel)",
            data=package,
            file_name=f"ClosePackage_{client.name}_{period_end.strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            help="Same package as an Excel workbook, one sheet per report",
            on_click=None if dbconn.READ_ONLY else AuditLog.log_event,
            args=(client_id, "EXPORT", "close_package_export", {
                "format": "xlsx",
                "period_start": period_start, "period_end": period_end,
                "row_count": len(export_rows),
            }),
        )

    if st.button("Refresh", key=worksheet_key("refresh_btn")):
        st.session_state.pop("_worksheet_exports", None)
        st.rerun()


# AJE Entry Form (modal-like experience)
if st.session_state.get('show_aje_form', False):
    st.markdown("---")
    st.subheader("Add Adjusting Journal Entry")

    # Get next AJE reference
    next_aje_ref = JournalEntry.get_next_aje_reference(client_id, period_start, period_end)

    # Get accounts for selection
    accounts = Account.get_all(client_id)
    account_options = {a.id: f"{a.account_number} - {a.name}" for a in accounts}

    with st.form(worksheet_key("aje_form")):
        form_cols = st.columns([1, 2, 1])

        with form_cols[0]:
            aje_ref = st.text_input(
                "AJE Reference", value=next_aje_ref, disabled=True,
                key=worksheet_key("aje_reference"),
            )
            aje_date = st.date_input(
                "Date", value=period_end, key=worksheet_key("aje_date"),
                format=date_format,
            )

        with form_cols[1]:
            aje_desc = st.text_input(
                "Description", placeholder="Describe the adjusting entry...",
                key=worksheet_key("aje_description"),
            )

        with form_cols[2]:
            aje_source = st.text_input(
                "Source Reference", placeholder="W/P Reference...",
                key=worksheet_key("aje_source"),
            )

        st.markdown("**Entry Lines**")

        # Pre-fill first account if specified
        prefill_account = st.session_state.get('aje_prefill_account')

        line_cols = st.columns([3, 2, 2, 2])
        with line_cols[0]:
            st.markdown("**Account**")
        with line_cols[1]:
            st.markdown("**Debit**")
        with line_cols[2]:
            st.markdown("**Credit**")
        with line_cols[3]:
            st.markdown("**Memo**")

        # Line 1
        line1_cols = st.columns([3, 2, 2, 2])
        with line1_cols[0]:
            default_idx = list(account_options.keys()).index(prefill_account) if prefill_account and prefill_account in account_options else 0
            line1_account = st.selectbox("Account 1", options=list(account_options.keys()),
                                          format_func=lambda x: account_options[x],
                                          index=default_idx, key=worksheet_key("line1_acct"), label_visibility="collapsed")
        with line1_cols[1]:
            line1_debit = st.number_input("Debit 1", min_value=0.0, step=0.01, key=worksheet_key("line1_dr"), label_visibility="collapsed")
        with line1_cols[2]:
            line1_credit = st.number_input("Credit 1", min_value=0.0, step=0.01, key=worksheet_key("line1_cr"), label_visibility="collapsed")
        with line1_cols[3]:
            line1_memo = st.text_input("Memo 1", key=worksheet_key("line1_memo"), label_visibility="collapsed")

        # Line 2
        line2_cols = st.columns([3, 2, 2, 2])
        with line2_cols[0]:
            line2_account = st.selectbox("Account 2", options=list(account_options.keys()),
                                          format_func=lambda x: account_options[x],
                                          index=0, key=worksheet_key("line2_acct"), label_visibility="collapsed")
        with line2_cols[1]:
            line2_debit = st.number_input("Debit 2", min_value=0.0, step=0.01, key=worksheet_key("line2_dr"), label_visibility="collapsed")
        with line2_cols[2]:
            line2_credit = st.number_input("Credit 2", min_value=0.0, step=0.01, key=worksheet_key("line2_cr"), label_visibility="collapsed")
        with line2_cols[3]:
            line2_memo = st.text_input("Memo 2", key=worksheet_key("line2_memo"), label_visibility="collapsed")

        # Line 3 (optional)
        line3_cols = st.columns([3, 2, 2, 2])
        with line3_cols[0]:
            line3_account = st.selectbox("Account 3", options=[None] + list(account_options.keys()),
                                          format_func=lambda x: account_options[x] if x else "(Optional)",
                                          index=0, key=worksheet_key("line3_acct"), label_visibility="collapsed")
        with line3_cols[1]:
            line3_debit = st.number_input("Debit 3", min_value=0.0, step=0.01, key=worksheet_key("line3_dr"), label_visibility="collapsed")
        with line3_cols[2]:
            line3_credit = st.number_input("Credit 3", min_value=0.0, step=0.01, key=worksheet_key("line3_cr"), label_visibility="collapsed")
        with line3_cols[3]:
            line3_memo = st.text_input("Memo 3", key=worksheet_key("line3_memo"), label_visibility="collapsed")

        # Line 4 (optional)
        line4_cols = st.columns([3, 2, 2, 2])
        with line4_cols[0]:
            line4_account = st.selectbox("Account 4", options=[None] + list(account_options.keys()),
                                          format_func=lambda x: account_options[x] if x else "(Optional)",
                                          index=0, key=worksheet_key("line4_acct"), label_visibility="collapsed")
        with line4_cols[1]:
            line4_debit = st.number_input("Debit 4", min_value=0.0, step=0.01, key=worksheet_key("line4_dr"), label_visibility="collapsed")
        with line4_cols[2]:
            line4_credit = st.number_input("Credit 4", min_value=0.0, step=0.01, key=worksheet_key("line4_cr"), label_visibility="collapsed")
        with line4_cols[3]:
            line4_memo = st.text_input("Memo 4", key=worksheet_key("line4_memo"), label_visibility="collapsed")

        submit_cols = st.columns([1, 1, 4])

        with submit_cols[0]:
            submitted = st.form_submit_button("Save AJE", type="primary", disabled=dbconn.READ_ONLY)

        with submit_cols[1]:
            if st.form_submit_button("Cancel"):
                st.session_state.show_aje_form = False
                st.session_state.aje_prefill_account = None
                st.rerun()

        if submitted:
            # Build journal entry
            lines = []

            if line1_account and (line1_debit > 0 or line1_credit > 0):
                lines.append(JournalEntryLine(
                    account_id=line1_account,
                    debit=line1_debit,
                    credit=line1_credit,
                    memo=line1_memo if line1_memo else None
                ))

            if line2_account and (line2_debit > 0 or line2_credit > 0):
                lines.append(JournalEntryLine(
                    account_id=line2_account,
                    debit=line2_debit,
                    credit=line2_credit,
                    memo=line2_memo if line2_memo else None
                ))

            if line3_account and (line3_debit > 0 or line3_credit > 0):
                lines.append(JournalEntryLine(
                    account_id=line3_account,
                    debit=line3_debit,
                    credit=line3_credit,
                    memo=line3_memo if line3_memo else None
                ))

            if line4_account and (line4_debit > 0 or line4_credit > 0):
                lines.append(JournalEntryLine(
                    account_id=line4_account,
                    debit=line4_debit,
                    credit=line4_credit,
                    memo=line4_memo if line4_memo else None
                ))

            entry = JournalEntry(
                client_id=client_id,
                entry_date=aje_date,
                description=aje_desc,
                source_reference=aje_source if aje_source else None,
                entry_type='Adjusting',
                aje_reference=next_aje_ref,
                lines=lines
            )

            try:
                entry.save()
                st.success(f"AJE {next_aje_ref} saved successfully!")
                st.session_state.show_aje_form = False
                st.session_state.aje_prefill_account = None
                st.rerun()
            except Exception as e:
                st.error(save_error_message(e))
