import streamlit as st
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.client import Client
from database import init_database
from database.seed_data import ENTITY_TYPES, BUSINESS_TYPES

# Initialize database
init_database()

st.set_page_config(page_title="Clients", page_icon="👥", layout="wide")

st.title("👥 Client Management")

# Entity type and business type options
ENTITY_TYPE_OPTIONS = list(ENTITY_TYPES.keys())
BUSINESS_TYPE_OPTIONS = list(BUSINESS_TYPES.keys())

# Show success message if client was just added
if 'client_added_message' in st.session_state:
    st.success(st.session_state.pop('client_added_message'))

# Tabs for viewing and adding clients
tab1, tab2 = st.tabs(["View Clients", "Add Client"])

with tab1:
    # Filter options
    col1, col2 = st.columns([1, 3])
    with col1:
        show_inactive = st.checkbox("Show inactive clients", value=False)

    # Get clients
    clients = Client.get_all(active_only=not show_inactive)

    # Check if we just added a client
    newly_added_id = st.session_state.pop('newly_added_client_id', None)

    if not clients:
        st.info("No clients found. Add a client to get started.")
    else:
        for client in clients:
            # Expand the newly added client
            is_expanded = (client.id == newly_added_id)
            with st.expander(f"**{client.name}**" + (" (Inactive)" if not client.is_active else ""), expanded=is_expanded):
                col1, col2, col3 = st.columns([2, 2, 1])

                with col1:
                    st.text(f"Entity Type: {client.entity_type or 'Not specified'}")
                    st.text(f"Business Type: {client.business_type or 'Not specified'}")
                    st.text(f"Fiscal Year End: Month {client.fiscal_year_end_month}")

                with col2:
                    if Client.has_transactions(client.id):
                        st.caption("Has transactions recorded")
                    else:
                        st.caption("No transactions yet")

                with col3:
                    if st.button("Edit", key=f"edit_{client.id}"):
                        st.session_state['editing_client'] = client.id

        # Edit client modal
        if 'editing_client' in st.session_state:
            client = Client.get_by_id(st.session_state['editing_client'])
            if client:
                st.divider()
                st.subheader(f"Edit Client: {client.name}")

                with st.form("edit_client_form"):
                    new_name = st.text_input("Client Name", value=client.name)

                    # Entity type
                    entity_index = 0
                    if client.entity_type in ENTITY_TYPE_OPTIONS:
                        entity_index = ENTITY_TYPE_OPTIONS.index(client.entity_type)

                    new_entity_type = st.selectbox(
                        "Entity Type (Legal Structure)",
                        options=ENTITY_TYPE_OPTIONS,
                        index=entity_index,
                        key="edit_entity_type"
                    )
                    st.caption(f"*{ENTITY_TYPES.get(new_entity_type, '')}*")

                    # Business type
                    business_index = 0
                    if client.business_type in BUSINESS_TYPE_OPTIONS:
                        business_index = BUSINESS_TYPE_OPTIONS.index(client.business_type)

                    new_business_type = st.selectbox(
                        "Business Type (Industry)",
                        options=BUSINESS_TYPE_OPTIONS,
                        index=business_index,
                        key="edit_business_type"
                    )
                    st.caption(f"*{BUSINESS_TYPES.get(new_business_type, '')}*")

                    new_fiscal_month = st.selectbox(
                        "Fiscal Year End Month",
                        options=list(range(1, 13)),
                        format_func=lambda x: ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'][x-1],
                        index=client.fiscal_year_end_month - 1
                    )
                    new_active = st.checkbox("Active", value=client.is_active)

                    st.warning("Note: Changing entity or business type will NOT update the existing chart of accounts. You may need to manually add/remove accounts.")

                    col1, col2 = st.columns(2)
                    with col1:
                        if st.form_submit_button("Save Changes", type="primary"):
                            client.name = new_name
                            client.entity_type = new_entity_type
                            client.business_type = new_business_type
                            client.fiscal_year_end_month = new_fiscal_month
                            client.is_active = new_active

                            try:
                                client.save(seed_accounts=False)
                                st.success("Client updated successfully!")
                                del st.session_state['editing_client']
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error updating client: {e}")

                    with col2:
                        if st.form_submit_button("Cancel"):
                            del st.session_state['editing_client']
                            st.rerun()

with tab2:
    st.subheader("Add New Client")

    st.markdown("""
    Add a new client to manage their books separately. Select the correct **entity type** (legal structure)
    and **business type** (industry) to get an appropriate chart of accounts.
    """)

    with st.form("add_client_form", clear_on_submit=True):
        client_name = st.text_input("Client Name", placeholder="e.g., ABC Corporation")

        st.divider()

        # Entity Type Section
        st.markdown("### Entity Type (Legal Structure)")
        st.caption("This determines equity accounts, distributions, and tax-related accounts")

        entity_type = st.selectbox(
            "Entity Type",
            options=ENTITY_TYPE_OPTIONS,
            index=0,
            label_visibility="collapsed"
        )
        st.info(f"**{entity_type}**: {ENTITY_TYPES[entity_type]}")

        st.divider()

        # Business Type Section
        st.markdown("### Business Type (Industry)")
        st.caption("This determines industry-specific accounts like inventory, COGS, or professional fees")

        business_type = st.selectbox(
            "Business Type",
            options=BUSINESS_TYPE_OPTIONS,
            index=0,  # Professional Services first
            label_visibility="collapsed"
        )
        st.info(f"**{business_type}**: {BUSINESS_TYPES[business_type]}")

        # Preview what accounts will be created
        with st.expander("Preview: Key accounts for your selections"):
            st.markdown(f"**Based on: {entity_type} + {business_type}**")

            # Entity-specific accounts
            st.markdown("#### From Entity Type:")
            if entity_type == "S-Corporation":
                st.markdown("- Common Stock, Shareholder Distributions, Officer Compensation")
            elif entity_type == "C-Corporation":
                st.markdown("- Common Stock, Dividends, Income Tax Payable/Expense")
            elif entity_type in ["LLC (Single-Member)", "Sole Proprietorship"]:
                st.markdown("- Owner's Capital, Owner's Draws")
            elif entity_type in ["LLC (Partnership)", "Partnership"]:
                st.markdown("- Member/Partner Capital & Distributions, Guaranteed Payments")
            elif entity_type == "Non-Profit":
                st.markdown("- Net Assets (Unrestricted/Restricted), Contributions, Grants")

            # Business-specific accounts
            st.markdown("#### From Business Type:")
            if business_type == "Professional Services":
                st.markdown("""
                - Professional Fees, Consulting Revenue, Retainer Fees
                - Work in Progress - Unbilled, Client Trust Account
                - Professional Development & CPE, Professional Licenses & Dues
                - **NO** Inventory or Cost of Goods Sold
                """)
            elif business_type in ["Retail", "E-commerce", "Wholesale/Distribution"]:
                st.markdown("""
                - Inventory accounts
                - Cost of Goods Sold, Freight, Shrinkage
                - Sales Tax Payable
                """)
            elif business_type == "Restaurant/Food Service":
                st.markdown("""
                - Food & Beverage Inventory
                - Cost of Food/Beverages Sold
                - Tips Payable, Kitchen Equipment
                """)
            elif business_type == "Real Estate (Rental)":
                st.markdown("""
                - Land, Buildings, Accumulated Depreciation
                - Rental Income, Late Fees
                - Property Taxes, Mortgage Interest, Repairs & Maintenance
                """)
            elif business_type == "Construction/Contractor":
                st.markdown("""
                - Materials Inventory, Retainage Receivable
                - Job Materials, Job Labor, Subcontractor Costs
                - Costs/Billings in Excess accounts
                """)
            elif business_type == "Manufacturing":
                st.markdown("""
                - Raw Materials, WIP, Finished Goods Inventory
                - Direct Labor, Manufacturing Overhead
                - Factory Rent & Utilities
                """)
            elif business_type == "Personal/Individual":
                st.markdown("""
                - Salary & Wages (income), Investment Income
                - Housing, Transportation, Healthcare expenses
                - Personal budget categories
                """)

        st.divider()

        fiscal_month = st.selectbox(
            "Fiscal Year End Month",
            options=list(range(1, 13)),
            format_func=lambda x: ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'][x-1],
            index=11  # Default to December
        )

        seed_accounts = st.checkbox("Create default chart of accounts", value=True,
                                   help="Uncheck if you want to manually create all accounts")

        if st.form_submit_button("Add Client", type="primary"):
            if not client_name:
                st.error("Client name is required.")
            else:
                new_client = Client(
                    name=client_name,
                    entity_type=entity_type,
                    business_type=business_type,
                    fiscal_year_end_month=fiscal_month
                )

                try:
                    new_client.save(seed_accounts=seed_accounts)
                    # Store success info and switch to View tab
                    st.session_state['newly_added_client_id'] = new_client.id
                    msg = f"Client '{client_name}' added successfully!"
                    if seed_accounts:
                        msg += f" Chart of accounts created for {entity_type} / {business_type}."
                    st.session_state['client_added_message'] = msg
                    st.rerun()
                except Exception as e:
                    st.error(f"Error adding client: {e}")
