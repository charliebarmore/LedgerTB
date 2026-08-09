import streamlit as st
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.client import Client
from database import init_database
from database.seed_data import ENTITY_TYPES, BUSINESS_TYPES
from utils.client_selector import render_client_selector
from utils.unlock import require_unlock
from utils.ui import view_switcher
from utils import icons

# Initialize database

st.set_page_config(page_title="Clients", page_icon=icons.CLIENTS, layout="wide")

# Same persistent client selector + nav every other page shows, so the
# sidebar doesn't disappear/reappear when landing on or leaving this page.
# Gate on the database passphrase before any DB access, then ensure schema.
require_unlock()
init_database()

render_client_selector()

st.title("Client Management")
st.caption("Each client keeps its own separate set of books.")

# Entity type and business type options
ENTITY_TYPE_OPTIONS = list(ENTITY_TYPES.keys())
BUSINESS_TYPE_OPTIONS = list(BUSINESS_TYPES.keys())

MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June',
               'July', 'August', 'September', 'October', 'November', 'December']

# All add-form widget keys (cleared after a successful add).
ADD_FORM_KEYS = [
    "add_client_name", "add_dba_name", "add_tax_id", "add_entity_type",
    "add_business_type", "add_fiscal_month", "add_address_line1", "add_address_city",
    "add_address_state", "add_address_zip", "add_contact_name", "add_contact_email",
    "add_contact_phone", "add_notes", "add_seed_accounts",
]


def _mask_tax_id(tax_id):
    """Show only the last 4 digits of an EIN/SSN in the UI (per security rules)."""
    if not tax_id:
        return None
    digits = ''.join(ch for ch in str(tax_id) if ch.isdigit())
    return f"••-•••{digits[-4:]}" if len(digits) >= 4 else "••••"


def _format_address(client):
    """One-line address, e.g. '123 Main St, Riverton, GA 30301' (parts optional)."""
    csz = " ".join(p for p in [
        f"{client.address_city}," if client.address_city else "",
        client.address_state or "",
        client.address_zip or "",
    ] if p).strip()
    parts = [p for p in [client.address_line1, csz] if p]
    return ", ".join(parts) if parts else None

# Show success message if client was just added
if 'client_added_message' in st.session_state:
    st.success(st.session_state.pop('client_added_message'))

view = view_switcher(["View Clients", "Add Client"], key="clients_view",
                     label="Client view")

if view == "View Clients":
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
                    if client.dba_name:
                        st.text(f"DBA: {client.dba_name}")
                    st.text(f"Entity Type: {client.entity_type or 'Not specified'}")
                    st.text(f"Business Type: {client.business_type or 'Not specified'}")
                    st.text(f"Fiscal Year End: {MONTH_NAMES[client.fiscal_year_end_month - 1]}")
                    if client.tax_id:
                        st.text(f"Tax ID: {_mask_tax_id(client.tax_id)}")

                with col2:
                    contact_bits = [b for b in (client.contact_name, client.contact_email, client.contact_phone) if b]
                    if contact_bits:
                        st.text("Contact: " + " · ".join(contact_bits))
                    addr = _format_address(client)
                    if addr:
                        st.text(addr)
                    if client.notes:
                        st.caption(client.notes)
                    st.caption("Has transactions recorded" if Client.has_transactions(client.id) else "No transactions yet")

                with col3:
                    if st.button("Edit", key=f"edit_{client.id}"):
                        st.session_state['editing_client'] = client.id

        # Edit client modal
        if 'editing_client' in st.session_state:
            client = Client.get_by_id(st.session_state['editing_client'])
            if client:
                st.divider()
                st.subheader(f"Edit Client: {client.name}")

                # Prime the edit widgets to this client's values the first time
                # its modal is shown, so the persistent widget keys don't carry a
                # previous client's selection over. Keys let the entity/industry
                # descriptions update live (a plain container, not st.form).
                if st.session_state.get('_edit_loaded_id') != client.id:
                    st.session_state['_edit_loaded_id'] = client.id
                    st.session_state['edit_name'] = client.name
                    st.session_state['edit_entity_type'] = (
                        client.entity_type if client.entity_type in ENTITY_TYPE_OPTIONS else ENTITY_TYPE_OPTIONS[0])
                    st.session_state['edit_business_type'] = (
                        client.business_type if client.business_type in BUSINESS_TYPE_OPTIONS else BUSINESS_TYPE_OPTIONS[0])
                    st.session_state['edit_fiscal_month'] = client.fiscal_year_end_month
                    st.session_state['edit_active'] = bool(client.is_active)
                    st.session_state['edit_dba_name'] = client.dba_name or ""
                    st.session_state['edit_tax_id'] = client.tax_id or ""
                    st.session_state['edit_address_line1'] = client.address_line1 or ""
                    st.session_state['edit_address_city'] = client.address_city or ""
                    st.session_state['edit_address_state'] = client.address_state or ""
                    st.session_state['edit_address_zip'] = client.address_zip or ""
                    st.session_state['edit_contact_name'] = client.contact_name or ""
                    st.session_state['edit_contact_email'] = client.contact_email or ""
                    st.session_state['edit_contact_phone'] = client.contact_phone or ""
                    st.session_state['edit_notes'] = client.notes or ""

                with st.container():
                    new_name = st.text_input("Legal Name", key="edit_name")
                    ecol_dba, ecol_tax = st.columns(2)
                    with ecol_dba:
                        new_dba_name = st.text_input("DBA / Trade Name", key="edit_dba_name")
                    with ecol_tax:
                        new_tax_id = st.text_input("Tax ID (EIN / SSN)", key="edit_tax_id")

                    new_entity_type = st.selectbox(
                        "Entity Type (Legal Structure)",
                        options=ENTITY_TYPE_OPTIONS,
                        key="edit_entity_type"
                    )
                    st.caption(f"*{ENTITY_TYPES.get(new_entity_type, '')}*")

                    new_business_type = st.selectbox(
                        "Business Type (Industry)",
                        options=BUSINESS_TYPE_OPTIONS,
                        key="edit_business_type"
                    )
                    st.caption(f"*{BUSINESS_TYPES.get(new_business_type, '')}*")

                    new_fiscal_month = st.selectbox(
                        "Fiscal Year End Month",
                        options=list(range(1, 13)),
                        format_func=lambda x: MONTH_NAMES[x - 1],
                        key="edit_fiscal_month"
                    )
                    new_active = st.checkbox("Active", key="edit_active")

                    st.markdown("**Contact & Address**")
                    new_address_line1 = st.text_input("Street Address", key="edit_address_line1")
                    ec_city, ec_state, ec_zip = st.columns([2, 1, 1])
                    with ec_city:
                        new_address_city = st.text_input("City", key="edit_address_city")
                    with ec_state:
                        new_address_state = st.text_input("State", max_chars=2, key="edit_address_state")
                    with ec_zip:
                        new_address_zip = st.text_input("ZIP", key="edit_address_zip")
                    ec1, ec2, ec3 = st.columns(3)
                    with ec1:
                        new_contact_name = st.text_input("Contact Name", key="edit_contact_name")
                    with ec2:
                        new_contact_email = st.text_input("Contact Email", key="edit_contact_email")
                    with ec3:
                        new_contact_phone = st.text_input("Contact Phone", key="edit_contact_phone")
                    new_notes = st.text_area("Notes", key="edit_notes")

                    st.warning("Note: Changing entity or business type will NOT update the existing chart of accounts. You may need to manually add/remove accounts.")

                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button("Save Changes", type="primary", key="edit_save"):
                            client.name = new_name
                            client.entity_type = new_entity_type
                            client.business_type = new_business_type
                            client.fiscal_year_end_month = new_fiscal_month
                            client.is_active = new_active
                            client.dba_name = new_dba_name or None
                            client.tax_id = new_tax_id or None
                            client.address_line1 = new_address_line1 or None
                            client.address_city = new_address_city or None
                            client.address_state = new_address_state or None
                            client.address_zip = new_address_zip or None
                            client.contact_name = new_contact_name or None
                            client.contact_email = new_contact_email or None
                            client.contact_phone = new_contact_phone or None
                            client.notes = new_notes or None

                            try:
                                client.save(seed_accounts=False)
                                st.success("Client updated successfully!")
                                del st.session_state['editing_client']
                                st.session_state.pop('_edit_loaded_id', None)
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error updating client: {e}")

                    with col2:
                        if st.button("Cancel", key="edit_cancel"):
                            del st.session_state['editing_client']
                            st.session_state.pop('_edit_loaded_id', None)
                            st.rerun()

else:
    st.subheader("Add New Client")

    # After a successful add we clear the fields; do it here (before the widgets
    # render) rather than in the submit handler, so we don't mutate widget state
    # during the same run the widgets are instantiated.
    if st.session_state.pop("_clear_add_form", False):
        for _k in ADD_FORM_KEYS:
            st.session_state.pop(_k, None)

    st.markdown("""
    Add a new client to manage their books separately. Select the correct **entity type** (legal structure)
    and **business type** (industry) to get an appropriate chart of accounts.
    """)

    # A plain container (NOT st.form) so the entity/industry descriptions and the
    # account preview update live as the dropdowns change -- inside a form the
    # selectbox values (and everything derived from them) only refresh on submit.
    with st.container():
        client_name = st.text_input("Legal Name", placeholder="e.g., ABC Corporation", key="add_client_name")
        col_dba, col_tax = st.columns(2)
        with col_dba:
            dba_name = st.text_input("DBA / Trade Name (optional)", placeholder="e.g., ABC", key="add_dba_name")
        with col_tax:
            tax_id = st.text_input("Tax ID (EIN / SSN)", placeholder="XX-XXXXXXX", key="add_tax_id",
                                   help="Stored locally on this machine.")

        st.divider()

        # Entity Type Section
        st.markdown("### Entity Type (Legal Structure)")
        st.caption("This determines equity accounts, distributions, and tax-related accounts")

        entity_type = st.selectbox(
            "Entity Type",
            options=ENTITY_TYPE_OPTIONS,
            index=0,
            label_visibility="collapsed",
            key="add_entity_type"
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
            label_visibility="collapsed",
            key="add_business_type"
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
            format_func=lambda x: MONTH_NAMES[x - 1],
            index=11,  # Default to December
            key="add_fiscal_month"
        )

        st.divider()

        # Contact & Address
        st.markdown("### Contact & Address")
        address_line1 = st.text_input("Street Address", key="add_address_line1")
        col_city, col_state, col_zip = st.columns([2, 1, 1])
        with col_city:
            address_city = st.text_input("City", key="add_address_city")
        with col_state:
            address_state = st.text_input("State", max_chars=2, placeholder="GA", key="add_address_state")
        with col_zip:
            address_zip = st.text_input("ZIP", key="add_address_zip")

        c1, c2, c3 = st.columns(3)
        with c1:
            contact_name = st.text_input("Contact Name", key="add_contact_name")
        with c2:
            contact_email = st.text_input("Contact Email", key="add_contact_email")
        with c3:
            contact_phone = st.text_input("Contact Phone", key="add_contact_phone")

        notes = st.text_area("Notes", placeholder="Engagement notes, deadlines, key personnel…", key="add_notes")

        st.divider()

        seed_accounts = st.checkbox("Create default chart of accounts", value=True,
                                   help="Uncheck if you want to manually create all accounts",
                                   key="add_seed_accounts")

        if st.button("Add Client", type="primary", key="add_client_submit"):
            if not client_name.strip():
                st.error("Client name is required.")
            else:
                new_client = Client(
                    name=client_name.strip(),
                    entity_type=entity_type,
                    business_type=business_type,
                    fiscal_year_end_month=fiscal_month,
                    tax_id=tax_id or None,
                    dba_name=dba_name or None,
                    address_line1=address_line1 or None,
                    address_city=address_city or None,
                    address_state=address_state or None,
                    address_zip=address_zip or None,
                    contact_name=contact_name or None,
                    contact_email=contact_email or None,
                    contact_phone=contact_phone or None,
                    notes=notes or None,
                )

                try:
                    new_client.save(seed_accounts=seed_accounts)
                    # Store success info and switch to View tab
                    st.session_state['newly_added_client_id'] = new_client.id
                    msg = f"Client '{client_name}' added successfully!"
                    if seed_accounts:
                        msg += f" Chart of accounts created for {entity_type} / {business_type}."
                    st.session_state['client_added_message'] = msg
                    st.session_state['_clear_add_form'] = True
                    # Return to the list (with the new client expanded) on rerun.
                    st.session_state['clients_view'] = "View Clients"
                    st.rerun()
                except Exception as e:
                    st.error(f"Error adding client: {e}")
