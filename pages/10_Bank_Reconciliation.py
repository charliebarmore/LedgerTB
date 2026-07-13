import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import init_database
from models.account import Account
from models.client import Client
from models.reconciliation import BankReconciliation
from utils import icons
from utils.client_selector import render_client_selector


init_database()
st.set_page_config(page_title="Bank Reconciliation", page_icon=icons.RECONCILIATION, layout="wide")
client_id = render_client_selector()

st.title("Bank Reconciliation")
st.caption("Match the general ledger to a bank or credit-card statement and preserve cleared status.")

if not client_id:
    st.warning("Please create a client first in the Clients page.")
    st.stop()

client = Client.get_by_id(client_id)
accounts = [
    account for account in Account.get_all(client_id, active_only=False)
    if account.type in ("Asset", "Liability")
]
if not accounts:
    st.warning("Add a bank, cash, or credit-card account before reconciling.")
    st.page_link("pages/3_Chart_of_Accounts.py", label="Go to Chart of Accounts", icon=icons.CHART_OF_ACCOUNTS)
    st.stop()

account_by_id = {account.id: account for account in accounts}
selected_account_id = st.selectbox(
    "Account",
    options=list(account_by_id),
    format_func=lambda account_id: account_by_id[account_id].display_name(),
)
account = account_by_id[selected_account_id]
st.caption(f"Viewing: **{client.name}** · Normal balance: {'credit' if account.type == 'Liability' else 'debit'}")

draft = BankReconciliation.get_draft(client_id, selected_account_id)

if draft is None:
    st.subheader("Start a statement reconciliation")
    default_start = BankReconciliation.suggested_start_date(client_id, selected_account_id)
    with st.form("start_reconciliation"):
        start_col, end_col, balance_col = st.columns(3)
        with start_col:
            statement_start = st.date_input("Statement start date", value=default_start)
        with end_col:
            statement_end = st.date_input("Statement end date", value=max(default_start, date.today()))
        with balance_col:
            statement_balance = st.number_input(
                "Statement ending balance", value=0.0, step=0.01, format="%.2f",
                help="For a credit card, enter the positive amount owed shown on the statement.",
            )
        if st.form_submit_button("Start reconciliation", type="primary"):
            try:
                BankReconciliation.create(
                    client_id, selected_account_id, statement_start, statement_end, statement_balance
                )
                st.success("Draft reconciliation created.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
else:
    st.subheader(f"Statement ending {draft.statement_end_date:%B %d, %Y}")
    with st.expander("Edit statement details"):
        with st.form("edit_statement"):
            start_col, end_col, balance_col = st.columns(3)
            with start_col:
                statement_start = st.date_input("Statement start date", value=draft.statement_start_date)
            with end_col:
                statement_end = st.date_input("Statement end date", value=draft.statement_end_date)
            with balance_col:
                statement_balance = st.number_input(
                    "Statement ending balance", value=float(draft.statement_ending_balance),
                    step=0.01, format="%.2f",
                    help="For a credit card, enter the positive amount owed shown on the statement.",
                )
            if st.form_submit_button("Update statement details"):
                try:
                    draft.update_statement(statement_start, statement_end, statement_balance)
                    st.success("Statement details updated.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

    cleared_balance = draft.cleared_balance()
    ledger_balance = draft.ledger_balance()
    difference = draft.difference()
    metric_cols = st.columns(4)
    metric_cols[0].metric("Statement ending balance", f"${draft.statement_ending_balance:,.2f}")
    metric_cols[1].metric("Cleared ledger balance", f"${cleared_balance:,.2f}")
    metric_cols[2].metric("Difference", f"${difference:,.2f}")
    metric_cols[3].metric("Full ledger balance", f"${ledger_balance:,.2f}")

    if abs(difference) < 0.005:
        st.success("Reconciled — the cleared ledger balance matches the statement.")
    else:
        st.warning("Select the entries that cleared this statement until the difference is $0.00.")

    lines = draft.lines()
    st.subheader("Statement activity")
    st.caption(
        "This includes every unreconciled general-ledger line through the statement end date, "
        "including older outstanding items. Manual journal entries are included."
    )
    if lines:
        frame = pd.DataFrame([{
            "Cleared": line.selected,
            "Line ID": line.line_id,
            "Date": line.entry_date,
            "Description": line.description,
            "Amount": line.amount,
            "Journal Entry": line.entry_id,
            "Source": line.source_reference or "",
        } for line in lines])
        edited = st.data_editor(
            frame,
            hide_index=True,
            width="stretch",
            height=min(680, 38 * len(frame) + 40),
            disabled=["Line ID", "Date", "Description", "Amount", "Journal Entry", "Source"],
            column_config={
                "Cleared": st.column_config.CheckboxColumn("Cleared"),
                "Line ID": None,
                "Amount": st.column_config.NumberColumn("Amount", format="$%.2f"),
                "Journal Entry": st.column_config.NumberColumn("JE #", format="%d"),
            },
            key=f"reconciliation_editor_{draft.id}",
        )
        if st.button("Save cleared items", type="primary"):
            try:
                selected_line_ids = edited.loc[edited["Cleared"], "Line ID"].tolist()
                draft.save_selected_lines(selected_line_ids)
                st.success("Cleared items saved.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    else:
        st.info("No unreconciled general-ledger activity exists through this statement date.")

    st.divider()
    confirm = st.checkbox(
        "I confirm the statement ending balance and cleared entries are correct.",
        disabled=abs(difference) >= 0.005,
    )
    if st.button(
        "Complete reconciliation",
        type="primary",
        disabled=not confirm or abs(difference) >= 0.005,
    ):
        try:
            draft.complete()
            st.success("Reconciliation completed and cleared items locked.")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

st.divider()
st.subheader("Reconciliation history")
history = BankReconciliation.get_all(client_id, selected_account_id)
if not history:
    st.info("No reconciliations for this account yet.")
else:
    history_frame = pd.DataFrame([{
        "Statement period": f"{item.statement_start_date} to {item.statement_end_date}",
        "Ending balance": item.statement_ending_balance,
        "Status": item.status,
        "Completed": item.completed_at.strftime("%Y-%m-%d %H:%M") if item.completed_at else "",
    } for item in history])
    st.dataframe(
        history_frame,
        hide_index=True,
        width="stretch",
        column_config={"Ending balance": st.column_config.NumberColumn(format="$%.2f")},
    )

    completed = [item for item in history if item.status == "Completed"]
    if completed:
        reopen_id = st.selectbox(
            "Completed statement",
            options=[item.id for item in completed],
            format_func=lambda item_id: next(
                f"Statement ending {item.statement_end_date} — ${item.statement_ending_balance:,.2f}"
                for item in completed if item.id == item_id
            ),
        )
        reopen_confirmation = st.text_input(
            "Type REOPEN to unlock the selected reconciliation", placeholder="REOPEN"
        )
        if st.button("Reopen selected reconciliation", disabled=reopen_confirmation != "REOPEN"):
            try:
                BankReconciliation.get_by_id(reopen_id, client_id).reopen()
                st.success("Reconciliation reopened. Its cleared selections can now be changed.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
