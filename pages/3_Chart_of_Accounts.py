import streamlit as st
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.account import Account
from models.client import Client
from database import init_database
from utils.client_selector import render_client_selector

# Initialize database
init_database()

st.set_page_config(page_title="Chart of Accounts", page_icon="📋", layout="wide")

# Client selector in sidebar
client_id = render_client_selector()

st.title("📋 Chart of Accounts")

if not client_id:
    st.warning("Please create a client first in the Clients page.")
    st.page_link("pages/0_Clients.py", label="Go to Clients →")
    st.stop()

# Get client info
client = Client.get_by_id(client_id)
st.caption(f"Viewing: **{client.name}**")

# Tabs for viewing and adding accounts
tab1, tab2 = st.tabs(["View Accounts", "Add Account"])

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
        account_types = ['Asset', 'Liability', 'Equity', 'Revenue', 'Expense']

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
                        options=['Asset', 'Liability', 'Equity', 'Revenue', 'Expense'],
                        index=['Asset', 'Liability', 'Equity', 'Revenue', 'Expense'].index(account.type)
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
                        if not Account.has_transactions(account.id):
                            if st.form_submit_button("Delete", type="secondary"):
                                # Actually delete since no transactions
                                from database.connection import get_connection
                                conn = get_connection()
                                conn.execute("DELETE FROM accounts WHERE id = ?", (account.id,))
                                conn.commit()
                                conn.close()
                                st.success("Account deleted!")
                                del st.session_state['editing_account']
                                st.rerun()
                        else:
                            st.caption("Cannot delete - has transactions")

with tab2:
    st.subheader("Add New Account")

    with st.form("add_account_form", clear_on_submit=True):
        account_number = st.text_input("Account Number", placeholder="e.g., 1000")
        account_name = st.text_input("Account Name", placeholder="e.g., Cash - Operating")
        account_type = st.selectbox(
            "Account Type",
            options=['Asset', 'Liability', 'Equity', 'Revenue', 'Expense']
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
