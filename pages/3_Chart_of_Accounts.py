import streamlit as st
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from models.account import Account
from models.client import Client
from database import init_database
from utils.client_selector import render_client_selector
from utils import icons
from constants import AccountType
from services.coa_import import parse_coa_csv

# Initialize database
init_database()

st.set_page_config(page_title="Chart of Accounts", page_icon=icons.CHART_OF_ACCOUNTS, layout="wide")

# Client selector in sidebar
client_id = render_client_selector()

st.title("Chart of Accounts")

if not client_id:
    st.warning("Please create a client first in the Clients page.")
    st.page_link("pages/0_Clients.py", label="Go to Clients →")
    st.stop()

# Get client info
client = Client.get_by_id(client_id)
st.caption(f"Viewing: **{client.name}**")

# Tabs for viewing, adding, and importing accounts
tab1, tab2, tab3 = st.tabs(["View Accounts", "Add Account", "Import CSV"])

with tab1:
    # Filter options
    col1, col2 = st.columns([1, 3])
    with col1:
        show_inactive = st.checkbox("Show inactive accounts", value=False)

    # Get accounts
    accounts = Account.get_all(client_id, active_only=not show_inactive)

    if not accounts:
        st.info("No accounts found. Add some accounts to get started.")
    else:
        # Group accounts by type
        account_types = AccountType.ALL

        for account_type in account_types:
            type_accounts = [a for a in accounts if a.type == account_type]

            if type_accounts:
                with st.expander(f"**{account_type}s** ({len(type_accounts)} accounts)", expanded=True):
                    header_cols = st.columns([1, 3, 2, 1])
                    with header_cols[0]:
                        st.markdown("**Acct #**")
                    with header_cols[1]:
                        st.markdown("**Name**")
                    with header_cols[2]:
                        st.markdown("**Subtype**")

                    for account in type_accounts:
                        col1, col2, col3, col4 = st.columns([1, 3, 2, 1])

                        with col1:
                            st.text(account.account_number)

                        with col2:
                            status = "" if account.is_active else " (Inactive)"
                            st.text(f"{account.name}{status}")
                            if account.description:
                                st.caption(account.description)

                        with col3:
                            st.text(account.subtype or "")

                        with col4:
                            # Edit button
                            if st.button("Edit", key=f"edit_{account.id}"):
                                st.session_state['editing_account'] = account.id

        # Edit account modal
        if 'editing_account' in st.session_state:
            account = Account.get_by_id(st.session_state['editing_account'], client_id=client_id)
            if account is None:
                # Unknown or stale id (e.g. left over from before a client switch) -
                # drop it rather than risk editing/deleting another client's account.
                st.session_state.pop('editing_account', None)
            if account:
                st.divider()
                st.subheader(f"Edit Account: {account.display_name()}")

                with st.form("edit_account_form"):
                    new_number = st.text_input("Account Number", value=account.account_number)
                    new_name = st.text_input("Account Name", value=account.name)
                    new_type = st.selectbox(
                        "Account Type",
                        options=AccountType.ALL,
                        index=AccountType.ALL.index(account.type)
                    )
                    new_subtype = st.text_input("Subtype", value=account.subtype or "")
                    new_description = st.text_area(
                        "Description/Memo",
                        value=account.description or "",
                        placeholder="e.g., Chase Business Checking ****1234",
                        help="Optional notes to help identify this account"
                    )
                    new_active = st.checkbox("Active", value=account.is_active)

                    col1, col2, col3 = st.columns(3)
                    with col1:
                        if st.form_submit_button("Save Changes", type="primary"):
                            account.account_number = new_number
                            account.name = new_name
                            account.type = new_type
                            account.subtype = new_subtype if new_subtype else None
                            account.description = new_description if new_description else None
                            account.is_active = new_active

                            try:
                                account.save()
                                st.success("Account updated successfully!")
                                del st.session_state['editing_account']
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error updating account: {e}")

                    with col2:
                        if st.form_submit_button("Cancel"):
                            del st.session_state['editing_account']
                            st.rerun()

                    with col3:
                        blockers = Account.deletion_blockers(account.id)
                        if not blockers:
                            if st.form_submit_button("Delete", type="secondary"):
                                try:
                                    Account.delete(account.id, client_id=client_id)
                                    st.success("Account deleted!")
                                    st.session_state.pop('editing_account', None)
                                    st.rerun()
                                except ValueError as e:
                                    st.error(str(e))
                        else:
                            detail = ", ".join(f"{v} {k}" for k, v in blockers.items())
                            st.caption(f"Cannot delete — referenced by {detail}")

with tab2:
    st.subheader("Add New Account")

    with st.form("add_account_form", clear_on_submit=True):
        account_number = st.text_input("Account Number", placeholder="e.g., 1000")
        account_name = st.text_input("Account Name", placeholder="e.g., Cash - Operating")
        account_type = st.selectbox(
            "Account Type",
            options=AccountType.ALL
        )
        subtype = st.text_input("Subtype (optional)", placeholder="e.g., Cash, Fixed Asset, etc.")
        description = st.text_area(
            "Description/Memo (optional)",
            placeholder="e.g., Chase Business Checking ****1234",
            help="Optional notes to help identify this account"
        )

        if st.form_submit_button("Add Account", type="primary"):
            if not account_number or not account_name:
                st.error("Account number and name are required.")
            else:
                new_account = Account(
                    client_id=client_id,
                    account_number=account_number,
                    name=account_name,
                    type=account_type,
                    subtype=subtype if subtype else None,
                    description=description if description else None
                )

                try:
                    new_account.save()
                    st.success(f"Account '{account_number} - {account_name}' added successfully!")
                except Exception as e:
                    if "UNIQUE constraint failed" in str(e):
                        st.error("An account with this number already exists for this client.")
                    else:
                        st.error(f"Error adding account: {e}")

with tab3:
    st.subheader("Import Chart of Accounts")
    st.caption(
        "Upload a CSV with columns **Account Number**, **Name**, and **Type** "
        "(Asset / Liability / Equity / Revenue / Expense), plus optional **Subtype** "
        "and **Description**. Accounts whose number already exists are skipped."
    )

    _template = (
        "Account Number,Name,Type,Subtype,Description\n"
        "1000,Cash - Operating,Asset,Cash,\n"
        "2000,Accounts Payable,Liability,Payable,\n"
        "3000,Owner's Equity,Equity,,\n"
        "4000,Service Revenue,Revenue,,\n"
        "6000,Office Expense,Expense,,\n"
    )
    st.download_button("Download template CSV", data=_template,
                       file_name="chart_of_accounts_template.csv", mime="text/csv")

    uploaded = st.file_uploader("Chart of accounts CSV", type=["csv"], key="coa_upload")
    if uploaded is not None:
        content = uploaded.getvalue().decode("utf-8-sig", "ignore")
        parsed, errors = parse_coa_csv(content)

        for e in errors[:15]:
            st.error(e)
        if len(errors) > 15:
            st.caption(f"…and {len(errors) - 15} more issue(s).")

        if parsed:
            existing = {a.account_number for a in Account.get_all(client_id, active_only=False)}
            preview = pd.DataFrame([{
                "Acct #": a["number"], "Name": a["name"], "Type": a["type"],
                "Subtype": a["subtype"] or "", "Description": a["description"] or "",
                "Status": "exists — skip" if a["number"] in existing else "new",
            } for a in parsed])
            st.dataframe(preview, use_container_width=True, hide_index=True)

            new_count = sum(1 for a in parsed if a["number"] not in existing)
            skip_count = len(parsed) - new_count
            st.caption(f"{new_count} new account(s); {skip_count} already exist (will be skipped).")

            if st.button(f"Import {new_count} account(s)", type="primary",
                         disabled=(new_count == 0), key="coa_import_btn"):
                created = 0
                failed = []
                for a in parsed:
                    if a["number"] in existing:
                        continue
                    try:
                        Account(client_id=client_id, account_number=a["number"], name=a["name"],
                                type=a["type"], subtype=a["subtype"], description=a["description"]).save()
                        existing.add(a["number"])
                        created += 1
                    except Exception as ex:
                        failed.append(f"#{a['number']}: {ex}")
                msg = f"Imported {created} account(s)."
                if skip_count:
                    msg += f" Skipped {skip_count} that already existed."
                st.success(msg)
                if failed:
                    st.error("Some failed: " + "; ".join(failed[:3]))
                st.rerun()
