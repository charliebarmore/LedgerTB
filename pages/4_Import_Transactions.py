import streamlit as st
import sys
from pathlib import Path
from datetime import date
import pandas as pd

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.account import Account
from models.client import Client
from models.journal_entry import JournalEntry
from models.import_profile import (
    AMOUNT_FORMAT_SEPARATE,
    AMOUNT_FORMAT_SINGLE,
    ImportProfile,
)
from models.transaction import ImportedTransaction
from services.csv_import import (
    CSVImporter, SIGN_CONVENTIONS, apply_sign_convention, default_sign_convention,
    summarize_import_amounts,
)
from services.import_verification import check_row_continuity, verify_against_source
from services.categorization import CategorizationService
from services.pattern_learning import PatternLearner
from services.posting import post_transaction
from services.import_identity import classify_import_duplicates, hash_source
from services.document_import import (
    extract_document, parse_statement_text, parse_statement_with_ai,
)
from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL
from database import init_database
from utils.client_selector import render_client_selector
from utils.unlock import require_unlock
from utils.ui import apply_default_on_change, is_parking_account, view_switcher
from utils import icons
from utils.import_review import ensure_row_ids, row_key, classify_review_rows

# Initialize database

st.set_page_config(page_title="Import Transactions", page_icon=icons.IMPORT, layout="wide")

# Client selector in sidebar
# Gate on the database passphrase before any DB access, then ensure schema.
require_unlock()
init_database()

client_id = render_client_selector()

st.title("Import Transactions")

if not client_id:
    st.warning("Please create a client first in the Clients page.")
    st.page_link("pages/0_Clients.py", label="Go to Clients →")
    st.stop()

# Get client info
client = Client.get_by_id(client_id)
st.caption(f"Viewing: **{client.name}**")
profile_message = st.session_state.pop("import_profile_message", None)
if profile_message:
    st.success(profile_message)

# Initialize services
categorization_service = CategorizationService()
importer = CSVImporter()


def apply_duplicate_checks(transactions):
    """Apply durable import checks, then flag possible manual journal matches."""
    duplicate_count = classify_import_duplicates(transactions, client_id)
    for transaction in transactions:
        if transaction.get("is_duplicate"):
            continue
        duplicates = JournalEntry.find_potential_duplicates(
            client_id=client_id,
            entry_date=transaction["date"],
            amount=transaction["amount"],
            bank_account_id=transaction.get("bank_account_id"),
        )
        if duplicates:
            transaction["is_duplicate"] = True
            transaction["duplicate_kind"] = "journal_match"
            transaction["duplicate_info"] = duplicates[0]
            transaction["duplicate_override"] = False
            transaction["duplicate_override_reason"] = ""
            transaction["include"] = False
            duplicate_count += 1
    return duplicate_count

# Initialize session state
if 'imported_data' not in st.session_state:
    st.session_state.imported_data = None
if 'column_mapping' not in st.session_state:
    st.session_state.column_mapping = {}
if 'transactions_to_review' not in st.session_state:
    st.session_state.transactions_to_review = []
if 'document_extraction' not in st.session_state:
    st.session_state.document_extraction = None
if 'document_transactions' not in st.session_state:
    st.session_state.document_transactions = []

# Get accounts - include credit cards and other liability accounts for imports
accounts = Account.get_all(client_id, active_only=True)
cash_accounts = [a for a in accounts if a.subtype == 'Cash']
credit_card_accounts = [a for a in accounts if a.subtype == 'Payable' and 'credit card' in a.name.lower()]
# Combine cash and credit card accounts for bank account selection
importable_accounts = cash_accounts + credit_card_accounts
expense_accounts = [a for a in accounts if a.type == 'Expense']
revenue_accounts = [a for a in accounts if a.type == 'Revenue']

# Sign convention options live with the importer that applies them.

# Track active tab in session state for programmatic switching
if 'import_active_tab' not in st.session_state:
    st.session_state.import_active_tab = "Upload CSV"

# Navigation (segmented tabs; programmatically controllable via
# st.session_state.import_active_tab, e.g. jumping to Review after an upload).
tab_options = ["Upload CSV", "Upload Statement", "Review & Categorize",
               "Import History", "Learned Patterns"]
selected_tab = view_switcher(tab_options, key="import_active_tab",
                             label="Navigation")

st.divider()

if selected_tab == "Upload CSV":
    st.subheader("Upload Bank/Credit Card CSV File")

    # Help section
    with st.expander("CSV import help and tips"):
        st.markdown("""
        ### Supported Formats

        The system can handle CSV exports from most banks and credit cards. You don't need a specific template -
        just map your columns after uploading.

        ### Minimum Required Columns

        Your CSV needs at least these fields (column names can vary):

        | Field | Common Column Names | Example |
        |-------|---------------------|---------|
        | **Date** | Date, Transaction Date, Post Date | 01/15/2025 |
        | **Description** | Description, Memo, Payee, Merchant | AMAZON.COM |
        | **Amount** | Amount, Transaction Amount | -45.99 |

        ### Amount Formats

        **Single Amount Column** (most common):
        - Negative numbers = expenses/withdrawals
        - Positive numbers = deposits/credits

        **Separate Debit/Credit Columns** (some banks):
        - Debit column for withdrawals
        - Credit column for deposits

        ### Example CSV

        ```
        Date,Description,Amount
        01/15/2025,AMAZON.COM,-45.99
        01/16/2025,PAYROLL DEPOSIT,2500.00
        01/17/2025,STARBUCKS,-5.75
        ```

        ### Tips for Common Banks

        | Bank | Export Location | Notes |
        |------|-----------------|-------|
        | **Chase** | Activity → Download | Select CSV format |
        | **Bank of America** | Statements → Download | Use "Spreadsheet (CSV)" |
        | **Wells Fargo** | Activity → Export | CSV option available |
        | **American Express** | Statements → Download | Choose Excel/CSV |
        | **Capital One** | Activity → Download | Select date range first |

        ### Sign Convention Reminder

        - **Bank accounts**: Usually negative = expense, positive = deposit
        - **Credit cards**: Usually positive = purchase, negative = payment

        If your transactions appear backwards, use the "Flip All Signs" option.

        ### Multi-Account Imports

        If your CSV contains transactions from multiple accounts (e.g., from Mint or a combined export):
        1. Check "CSV contains transactions from multiple accounts"
        2. Select the column that identifies which account each transaction is from
        3. Map each account name to a LedgerTB account
        """)

        # Sample template download
        st.markdown("### Download Sample Template")
        sample_csv = """Date,Description,Amount
01/15/2025,Sample expense,-100.00
01/16/2025,Sample deposit,500.00
01/17/2025,Another expense,-25.50"""

        st.download_button(
            label="Download sample CSV template",
            data=sample_csv,
            file_name="transaction_import_template.csv",
            mime="text/csv"
        )

    # Check if we have importable accounts
    # Include all asset and liability accounts that could have transactions imported
    all_importable = [a for a in accounts if a.type in ('Asset', 'Liability')]
    bank_account_options = {a.id: a.display_name() for a in all_importable}

    if not bank_account_options:
        st.warning("No bank or credit card accounts found. Please add one in Chart of Accounts first.")
    else:
        # Multi-account toggle
        multi_account_mode = st.checkbox(
            "CSV contains transactions from multiple accounts",
            key="csv_multi_account_mode",
            help="Check this if your CSV has a column identifying which account each transaction is from (e.g., exports from Mint, aggregated bank downloads)"
        )

        # Initialize variables
        selected_bank = None
        selected_account = None  # set by whichever account picker renders below
        sign_convention = "bank"
        source_account_col = None  # Initialize here for use later
        selected_profile = None
        saved_profiles = []

        if multi_account_mode:
            # Multi-account mode - may or may not have a source account column
            st.info("""
            **Multi-Account Mode**: Select a column that identifies the source account, or choose 'none' to assign all transactions to one account.
            """)
        else:
            # Single account mode - show bank account and sign convention
            col1, col2 = st.columns(2)

            with col1:
                if st.session_state.get("csv_bank_account") not in bank_account_options:
                    st.session_state.csv_bank_account = next(iter(bank_account_options))
                selected_bank = st.selectbox(
                    "Select Account",
                    options=list(bank_account_options.keys()),
                    format_func=lambda x: bank_account_options[x],
                    key="csv_bank_account",
                    help="The bank or credit card account these transactions are from"
                )

                # Quick add new account
                with st.expander("+ Add New Account"):
                    with st.form("quick_add_bank_account", clear_on_submit=True):
                        new_acct_number = st.text_input("Account Number", placeholder="e.g., 1010")
                        new_acct_name = st.text_input("Account Name", placeholder="e.g., Chase Checking")
                        new_acct_type = st.selectbox("Type", options=['Asset', 'Liability'])
                        new_acct_subtype = st.text_input("Subtype (optional)", placeholder="e.g., Cash")
                        new_acct_desc = st.text_input("Description (optional)", placeholder="e.g., ****1234")

                        if st.form_submit_button("Add Account", type="primary"):
                            if new_acct_number and new_acct_name:
                                try:
                                    new_account = Account(
                                        client_id=client_id,
                                        account_number=new_acct_number,
                                        name=new_acct_name,
                                        type=new_acct_type,
                                        subtype=new_acct_subtype if new_acct_subtype else None,
                                        description=new_acct_desc if new_acct_desc else None
                                    )
                                    new_account.save()
                                    st.success(f"Added: {new_acct_number} - {new_acct_name}")
                                    st.rerun()
                                except Exception as e:
                                    if "UNIQUE constraint" in str(e):
                                        st.error("Account number already exists.")
                                    else:
                                        st.error(f"Error: {e}")
                            else:
                                st.error("Account number and name required.")

            selected_account = next(
                (a for a in all_importable if a.id == selected_bank), None
            )
            saved_profiles = ImportProfile.list_for_account(client_id, selected_bank)
            with col2:
                if saved_profiles:
                    st.caption(
                        f"**{len(saved_profiles)} saved import "
                        f"format{'s' if len(saved_profiles) != 1 else ''}**"
                    )
                    st.caption(", ".join(profile.name for profile in saved_profiles))
                else:
                    st.caption("No saved import formats for this account yet.")

        # File upload — keyed by a nonce so "Clear File" can force a fresh,
        # empty uploader. Without this the widget keeps returning the old file
        # after a clear, and the new-file branch below re-imports it instantly.
        uploader_key = f"csv_uploader_{st.session_state.get('csv_uploader_nonce', 0)}"
        uploaded_file = st.file_uploader("Upload CSV file", type=['csv'], key=uploader_key)

        # Handle new file upload
        if uploaded_file:
            uploaded_bytes = uploaded_file.getvalue()
            try:
                raw_content = CSVImporter.decode_upload(uploaded_bytes)
            except Exception as exc:
                st.error(f"That file could not be read: {exc}")
                st.stop()
            source_id = hash_source(uploaded_bytes)

            # Banks commonly reuse generic filenames (for example,
            # ``transactions.csv``). Compare the content identity so uploading
            # a newer export with the same name cannot leave stale rows behind.
            if st.session_state.get('csv_source_id') != source_id:
                st.session_state.csv_content = raw_content
                st.session_state.csv_raw_content = raw_content  # Keep original for reset
                st.session_state.csv_filename = uploaded_file.name
                st.session_state.csv_source_id = source_id
                # Reset the editor widget itself. A keyed text_area keeps its own
                # value across reruns and ignores `value=`, so without this the
                # editor (and the import) would show the PREVIOUS file's rows when
                # a second file is uploaded in the same session.
                st.session_state.csv_editor_widget = raw_content

        # Show editor if we have CSV content (persists after file uploader clears)
        if st.session_state.get('csv_content'):
            content = st.session_state.csv_content
            # Initialize the keyed editor before it renders. Passing both a
            # ``value`` and an existing session-state value makes Streamlit
            # emit a warning on every rerun after an upload.
            if 'csv_editor_widget' not in st.session_state:
                st.session_state.csv_editor_widget = content

            # Parse before rendering anything: the row count and the table both
            # need the result, and the raw editor below has to know whether to
            # open itself. Every row is requested, not a sample — a truncated
            # table reads as though the file were short.
            parse_error = None
            try:
                parsed_df, columns = CSVImporter.preview_csv(content, num_rows=None)
            except Exception as exc:
                parse_error = str(exc)

            # Raw text editing is a repair tool, not the main view, so it stays
            # collapsed — except when the file won't parse, which is exactly when
            # it is needed. It must render BEFORE the st.stop() below, or a
            # malformed file would leave no way to fix it.
            def save_csv_edits():
                st.session_state.csv_content = st.session_state.csv_editor_widget

            # NOTE: no "copy the widget value back on every render" here — that
            # was what broke Reset: it re-saved the editor's old text right
            # after the reset wrote the original. The on_change callback is
            # the single save path; the button callbacks below run BEFORE the
            # widgets render on their rerun, which is the only legal moment to
            # overwrite a keyed widget's state.

            def _reset_csv_to_original():
                raw = st.session_state.get('csv_raw_content', '')
                st.session_state.csv_content = raw
                st.session_state.csv_editor_widget = raw

            def _clear_csv_file():
                st.session_state.csv_content = None
                st.session_state.csv_raw_content = None
                st.session_state.csv_filename = None
                st.session_state.csv_source_id = None
                st.session_state.pop('csv_editor_widget', None)
                st.session_state.pop('csv_coa_override', None)
                # Rotate the uploader key so the old file can't re-import itself.
                st.session_state.csv_uploader_nonce = st.session_state.get('csv_uploader_nonce', 0) + 1

            with st.expander("Edit the raw CSV", expanded=parse_error is not None):
                st.caption("Delete extra rows or fix bad data before importing. "
                           "The first row should be column headers.")
                st.text_area(
                    "CSV Content",
                    height=200,
                    key="csv_editor_widget",
                    on_change=save_csv_edits,
                    label_visibility="collapsed"
                )
                edit_col1, edit_col2, _ = st.columns([1, 1, 3])
                with edit_col1:
                    st.button("Reset to Original", on_click=_reset_csv_to_original)
                with edit_col2:
                    st.button("Clear File", on_click=_clear_csv_file)

            if parse_error:
                st.error(f"Could not read this CSV: {parse_error}")
                st.info("Fix the rows in **Edit the raw CSV** above, or clear the file and upload a different one.")
                st.stop()

            # A chart-of-accounts export parses cleanly here, so without this
            # check it reads as "transactions" with the account number guessed
            # as the date column. Catch the shape and point at the right page.
            _headers = {str(c).strip().lower() for c in columns}
            if {"number", "name", "type"} <= _headers:
                st.warning(
                    f"**{st.session_state.get('csv_filename') or 'This file'}** looks like a "
                    "chart of accounts export (number / name / type columns), not bank or "
                    "credit card activity. This page imports transactions — accounts are "
                    "imported on the Chart of Accounts page."
                )
                st.page_link("pages/3_Chart_of_Accounts.py",
                             label="Go to Chart of Accounts → Import CSV",
                             icon=icons.CHART_OF_ACCOUNTS)
                if not st.checkbox("These rows really are transactions — continue anyway",
                                   key="csv_coa_override"):
                    st.stop()

            # The one place the file is shown: every row, scrollable.
            st.subheader(f"{len(parsed_df):,} transactions in {st.session_state.get('csv_filename') or 'this file'}")
            st.dataframe(
                parsed_df,
                width="stretch",
                # Tall enough to read at a glance, capped so the page stays
                # navigable; the table scrolls past the cap.
                height=min(len(parsed_df) * 35 + 45, 440),
            )

            # Auto-detect columns
            detected = CSVImporter.detect_columns(columns)

            if not multi_account_mode:
                matched_profile = ImportProfile.match_for_columns(
                    saved_profiles, columns
                )
                profile_by_id = {profile.id: profile for profile in saved_profiles}
                # Profile ids are positive database integers, so zero is a
                # stable visible sentinel. Streamlit renders a ``None`` option
                # as a blank selectbox even when format_func supplies a label.
                auto_profile_id = 0
                profile_options = [auto_profile_id, *profile_by_id]
                profile_versions = tuple(
                    (profile.id, profile.updated_at.isoformat())
                    for profile in saved_profiles
                )
                profile_choice_context = (
                    selected_bank,
                    hash_source(content.encode("utf-8")),
                    profile_versions,
                )
                if (
                    st.session_state.get("_csv_profile_choice_context")
                    != profile_choice_context
                ):
                    st.session_state._csv_profile_choice_context = (
                        profile_choice_context
                    )
                    st.session_state.csv_import_profile_id = (
                        matched_profile.id if matched_profile else auto_profile_id
                    )
                if st.session_state.get("csv_import_profile_id") not in profile_options:
                    st.session_state.csv_import_profile_id = auto_profile_id

                profile_col, sign_col = st.columns(2)
                with profile_col:
                    selected_profile_id = st.selectbox(
                        "Import Format",
                        options=profile_options,
                        format_func=lambda profile_id: (
                            profile_by_id[profile_id].name
                            if profile_id != auto_profile_id
                            else "Auto-detect (no saved format)"
                        ),
                        key="csv_import_profile_id",
                        help=(
                            "Formats are matched from the complete set and order of "
                            "CSV column headers. Choose one manually to override."
                        ),
                    )
                    selected_profile = profile_by_id.get(selected_profile_id)

                profile_version = (
                    selected_profile.updated_at.isoformat()
                    if selected_profile
                    else None
                )
                apply_default_on_change(
                    "csv_sign_convention",
                    depends_on=(selected_bank, selected_profile_id, profile_version),
                    default_value=(
                        selected_profile.sign_convention
                        if selected_profile
                        else default_sign_convention(
                            selected_account.type if selected_account else None
                        )
                    ),
                )
                with sign_col:
                    sign_convention = st.selectbox(
                        "Sign Convention",
                        options=list(SIGN_CONVENTIONS.keys()),
                        format_func=lambda x: SIGN_CONVENTIONS[x],
                        key="csv_sign_convention",
                        help=(
                            "Loaded from the selected format, or inferred from the "
                            "account type when no format matches."
                        ),
                    )

                if matched_profile and selected_profile_id == matched_profile.id:
                    st.caption(
                        f'Automatically matched saved format **{matched_profile.name}** '
                        "from this file's columns."
                    )
                elif selected_profile:
                    st.caption(f"Using saved format **{selected_profile.name}**.")
                elif saved_profiles:
                    st.info(
                        "No saved format matched these columns. Review the detected "
                        "mapping, select a format manually, or save this as a new format."
                    )

            resolved_mapping = {
                "applied": False,
                "missing": [],
                "date_column": detected["date"] or columns[0],
                "description_column": detected["description"] or columns[0],
                "amount_format": (
                    AMOUNT_FORMAT_SINGLE
                    if detected["amount"]
                    else AMOUNT_FORMAT_SEPARATE
                ),
                "amount_column": detected["amount"],
                "debit_column": detected["debit"],
                "credit_column": detected["credit"],
            }
            if selected_profile and not multi_account_mode:
                resolved_mapping = selected_profile.resolve_columns(columns, detected)

            # Keyed mapping widgets retain their own value. Reapply defaults only
            # when the file, account, or saved profile changes; ordinary reruns
            # must preserve the user's deliberate edits.
            mapping_context = (
                hash_source(content.encode("utf-8")),
                selected_bank,
                selected_profile.id if selected_profile else None,
                selected_profile.updated_at.isoformat() if selected_profile else None,
            )
            if st.session_state.get("_csv_mapping_context") != mapping_context:
                st.session_state._csv_mapping_context = mapping_context
                st.session_state.csv_date_column = resolved_mapping["date_column"]
                st.session_state.csv_description_column = resolved_mapping[
                    "description_column"
                ]
                st.session_state.csv_amount_format = (
                    "Single Amount Column"
                    if resolved_mapping["amount_format"] == AMOUNT_FORMAT_SINGLE
                    else "Separate Debit/Credit Columns"
                )
                st.session_state.csv_amount_column = (
                    resolved_mapping["amount_column"] or columns[0]
                )
                st.session_state.csv_debit_column = (
                    resolved_mapping["debit_column"] or "(none)"
                )
                st.session_state.csv_credit_column = (
                    resolved_mapping["credit_column"] or "(none)"
                )
                st.session_state.csv_source_account_column = (
                    "(none - assign all to one account)"
                )

            # A single amount column plus date and description is the common
            # case and needs no decision from the user — summarize it and keep
            # the controls available but out of the way. Separate debit/credit
            # columns still require choosing the radio below, so that counts as
            # unresolved and stays visible.
            mapping_is_clear = bool(
                resolved_mapping["date_column"]
                and resolved_mapping["description_column"]
                and (
                    resolved_mapping["amount_column"]
                    if resolved_mapping["amount_format"] == AMOUNT_FORMAT_SINGLE
                    else resolved_mapping["debit_column"]
                    or resolved_mapping["credit_column"]
                )
            )

            if mapping_is_clear:
                if resolved_mapping["applied"]:
                    amount_summary = (
                        resolved_mapping["amount_column"]
                        if resolved_mapping["amount_format"] == AMOUNT_FORMAT_SINGLE
                        else "/".join(
                            column
                            for column in (
                                resolved_mapping["debit_column"],
                                resolved_mapping["credit_column"],
                            )
                            if column
                        )
                    )
                    st.caption(
                        "Saved mapping applied — "
                        f"date: **{resolved_mapping['date_column']}**, "
                        f"description: **{resolved_mapping['description_column']}**, "
                        f"amount: **{amount_summary}**"
                    )
                else:
                    st.caption(
                        f"Detected columns — date: **{resolved_mapping['date_column']}**, "
                        f"description: **{resolved_mapping['description_column']}**, "
                        f"amount: **{resolved_mapping['amount_column'] or 'debit/credit'}**"
                    )
                mapping_area = st.expander("Change column mapping", expanded=False)
            else:
                st.subheader("Column Mapping")
                st.caption("Some columns could not be detected — map them to the required fields.")
                mapping_area = st.container()

            if selected_profile and resolved_mapping["missing"]:
                missing_columns = ", ".join(resolved_mapping["missing"])
                st.warning(
                    "The saved column mapping was not applied because this file is missing: "
                    f"{missing_columns}. The saved sign setting remains active; review the "
                    "detected columns, then update the profile if this is the bank's new format."
                )

            with mapping_area:
                col1, col2 = st.columns(2)

                with col1:
                    date_col = st.selectbox(
                        "Date Column",
                        options=columns,
                        key="csv_date_column",
                    )

                    desc_col = st.selectbox(
                        "Description Column",
                        options=columns,
                        key="csv_description_column",
                    )

                    # Source account column for multi-account mode
                    if multi_account_mode:
                        source_account_col_selection = st.selectbox(
                            "Source Account Column",
                            options=["(none - assign all to one account)"] + columns,
                            index=0,
                            key="csv_source_account_column",
                            help="Column that identifies which account each transaction is from. Select 'none' if your CSV doesn't have this."
                        )
                        if source_account_col_selection == "(none - assign all to one account)":
                            source_account_col = None
                        else:
                            source_account_col = source_account_col_selection

                with col2:
                    amount_type = st.radio(
                        "Amount Format",
                        options=["Single Amount Column", "Separate Debit/Credit Columns"],
                        key="csv_amount_format",
                    )

                    if amount_type == "Single Amount Column":
                        amount_col = st.selectbox(
                            "Amount Column",
                            options=columns,
                            key="csv_amount_column",
                        )
                        debit_col = None
                        credit_col = None
                    else:
                        amount_col = None
                        debit_col = st.selectbox(
                            "Debit/Withdrawal Column",
                            options=['(none)'] + columns,
                            key="csv_debit_column",
                        )
                        credit_col = st.selectbox(
                            "Credit/Deposit Column",
                            options=['(none)'] + columns,
                            key="csv_credit_column",
                        )
                        if debit_col == '(none)':
                            debit_col = None
                        if credit_col == '(none)':
                            credit_col = None

            if not multi_account_mode:
                st.markdown("#### Saved Import Format")
                suggested_name = (
                    Path(st.session_state.get("csv_filename") or "CSV export")
                    .stem.replace("_", " ").replace("-", " ").strip()
                    or "CSV export"
                )
                name_context = (
                    selected_bank,
                    hash_source(content.encode("utf-8")),
                    selected_profile.id if selected_profile else None,
                    selected_profile.name if selected_profile else None,
                )
                if st.session_state.get("_csv_profile_name_context") != name_context:
                    st.session_state._csv_profile_name_context = name_context
                    st.session_state.csv_import_profile_name = (
                        selected_profile.name if selected_profile else suggested_name[:80]
                    )

                profile_name = st.text_input(
                    "Format Name",
                    key="csv_import_profile_name",
                    max_chars=80,
                    placeholder="e.g., Bank website download",
                    help=(
                        "Use a name that distinguishes where this export came from. "
                        "Names must be unique within this account."
                    ),
                )

                def build_import_profile(profile_id=None):
                    return ImportProfile(
                        id=profile_id,
                        client_id=client_id,
                        bank_account_id=selected_bank,
                        name=profile_name,
                        date_column=date_col,
                        description_column=desc_col,
                        amount_format=(
                            AMOUNT_FORMAT_SINGLE
                            if amount_type == "Single Amount Column"
                            else AMOUNT_FORMAT_SEPARATE
                        ),
                        amount_column=amount_col,
                        debit_column=debit_col,
                        credit_column=credit_col,
                        sign_convention=sign_convention,
                        header_signature=ImportProfile.signature_for_columns(columns),
                    )

                action_columns = st.columns([1, 1, 1])
                with action_columns[0]:
                    if selected_profile and st.button(
                        "Update selected format", key="update_csv_import_profile"
                    ):
                        try:
                            profile = build_import_profile(selected_profile.id)
                            profile.save()
                            st.session_state.pop("_csv_mapping_context", None)
                            st.session_state.pop("_csv_profile_choice_context", None)
                            st.session_state.import_profile_message = (
                                f'Updated import format "{profile.name}".'
                            )
                            st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))
                with action_columns[1]:
                    if st.button("Save as new format", key="save_csv_import_profile"):
                        try:
                            profile = build_import_profile()
                            profile.save()
                            st.session_state.pop("_csv_mapping_context", None)
                            st.session_state.pop("_csv_profile_choice_context", None)
                            st.session_state.import_profile_message = (
                                f'Saved new import format "{profile.name}".'
                            )
                            st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))
                with action_columns[2]:
                    if selected_profile:
                        if (
                            st.session_state.get("confirm_profile_delete_id")
                            != selected_profile.id
                        ):
                            if st.button(
                                "Remove selected format",
                                key="remove_csv_import_profile",
                            ):
                                st.session_state.confirm_profile_delete_id = (
                                    selected_profile.id
                                )
                                st.rerun()
                        else:
                            st.warning(f'Remove "{selected_profile.name}"?')
                            confirm_col, cancel_col = st.columns(2)
                            with confirm_col:
                                if st.button("Remove", key="confirm_remove_csv_profile"):
                                    ImportProfile.delete(
                                        client_id, selected_profile.id
                                    )
                                    st.session_state.pop(
                                        "confirm_profile_delete_id", None
                                    )
                                    st.session_state.pop("_csv_mapping_context", None)
                                    st.session_state.pop(
                                        "_csv_profile_choice_context", None
                                    )
                                    st.session_state.import_profile_message = (
                                        f'Removed import format "{selected_profile.name}".'
                                    )
                                    st.rerun()
                            with cancel_col:
                                if st.button("Cancel", key="cancel_remove_csv_profile"):
                                    st.session_state.pop(
                                        "confirm_profile_delete_id", None
                                    )
                                    st.rerun()
                st.caption(
                    "Formats are private to this client and account. They store the "
                    "CSV header, column mapping, and sign setting—not transaction data."
                )

            # When multi-account mode is on but no source column selected, show single account selector
            if multi_account_mode and source_account_col is None:
                st.subheader("Account Assignment")
                st.caption("All transactions will be assigned to this account")

                col1, col2 = st.columns(2)
                with col1:
                    selected_bank = st.selectbox(
                        "Assign All to Account",
                        options=list(bank_account_options.keys()),
                        format_func=lambda x: bank_account_options[x],
                        help="The bank or credit card account these transactions are from"
                    )
                # Same account-follows-convention behaviour as single-account mode.
                selected_account = next(
                    (a for a in all_importable if a.id == selected_bank), None
                )
                apply_default_on_change(
                    "multi_assign_sign_convention",
                    depends_on=selected_bank,
                    default_value=default_sign_convention(
                        selected_account.type if selected_account else None
                    ),
                )
                with col2:
                    sign_convention = st.selectbox(
                        "Sign Convention",
                        options=list(SIGN_CONVENTIONS.keys()),
                        format_func=lambda x: SIGN_CONVENTIONS[x],
                        key="multi_assign_sign_convention",
                        help="Set from the account type — change it if this statement is reversed.",
                    )

                st.caption("""
                **Sign Convention Guide:**
                - **Bank Account**: Most banks show expenses as negative, deposits as positive
                - **Credit Card**: Most credit cards show purchases as positive, payments as negative
                - **Flip All Signs**: Use if your statement is the opposite of the above
                """)

            # Account mapping for multi-account mode with source column
            if multi_account_mode and source_account_col:
                st.subheader("Account Mapping")
                st.caption("Map account names from your CSV to accounts in LedgerTB")

                # Get unique account values from the CSV
                import pandas as pd
                from io import StringIO
                temp_df = pd.read_csv(StringIO(content))
                unique_accounts = temp_df[source_account_col].dropna().unique().tolist()

                account_mapping = {}
                for csv_account in unique_accounts:
                    col_a, col_b = st.columns([2, 2])
                    with col_a:
                        st.text(str(csv_account))
                    with col_b:
                        mapped = st.selectbox(
                            f"Map to",
                            options=[0] + list(bank_account_options.keys()),
                            format_func=lambda x: "-- Skip --" if x == 0 else bank_account_options[x],
                            key=f"map_{csv_account}",
                            label_visibility="collapsed"
                        )
                        account_mapping[str(csv_account)] = mapped

                # Store mapping in session state for use during parsing
                st.session_state['account_mapping'] = account_mapping
                st.session_state['source_account_col'] = source_account_col

            # Confirmation section
            st.divider()
            st.subheader("Confirm Import")

            # Summary figures only. The rows themselves are already shown in
            # full and scrollable further up, so repeating a first-3/last-3
            # sample here added a third view of the same data. Reuses the
            # dataframe parsed above rather than re-reading the file.
            total_rows = len(parsed_df)

            # What went out, what came in, and the net — in the language of the
            # account. A min-to-max range said nothing useful ("79.00 to 79.00"
            # for a file of identical charges) and could not be reconciled
            # against a statement. The detected date column is already reported
            # by the column-mapping summary above, so it is not repeated here.
            summary = None
            if amount_col and amount_col in parsed_df.columns:
                try:
                    amounts = (parsed_df[amount_col].astype(str)
                               .str.replace(',', '').str.replace('$', '')
                               .str.replace('(', '-').str.replace(')', ''))
                    amounts = pd.to_numeric(amounts, errors='coerce').dropna()
                    summary = summarize_import_amounts(
                        amounts.tolist(),
                        sign_convention,
                        selected_account.type if selected_account else None,
                    )
                except Exception:
                    summary = None

            if summary:
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Total Rows", total_rows)
                col2.metric(summary["outflow_label"], f"${summary['outflow']:,.2f}")
                col3.metric(summary["inflow_label"], f"${summary['inflow']:,.2f}")
                col4.metric("Net change", f"${summary['net']:,.2f}")
                st.caption(
                    "Totals reflect the sign convention above — compare them "
                    "against the statement before importing."
                )
            else:
                st.metric("Total Rows", total_rows)

            # Confirmation checkbox
            confirmed = st.checkbox(
                "I have reviewed the CSV data and column mappings above and confirm they are correct",
                key="csv_confirm"
            )

            if not confirmed:
                st.info("Please review the data above and check the confirmation box to proceed.")
            else:
                if st.button("Parse Transactions", type="primary"):
                    try:
                        # Clear any previously parsed transactions to avoid duplicates
                        st.session_state.transactions_to_review = []
                        # Retire the previous batch's "What's next?" screen. It is
                        # shown by Review & Categorize with an st.stop(), so a
                        # leftover flag hides the rows just parsed behind a stale
                        # success message from the import before this one.
                        st.session_state.import_complete = False
                        st.session_state.import_complete_msg = None

                        # Parse with source account column if in multi-account mode
                        transactions = CSVImporter.parse_csv(
                            content,
                            date_column=date_col,
                            description_column=desc_col,
                            amount_column=amount_col,
                            debit_column=debit_col,
                            credit_column=credit_col,
                            source_account_column=source_account_col if multi_account_mode else None,
                            source_id=hash_source(content.encode("utf-8")),
                            source_filename=st.session_state.get("csv_filename"),
                        )

                        if not transactions:
                            st.error("No valid transactions found in the file.")
                        else:
                            # Apply sign convention and account mapping
                            skipped = 0
                            valid_transactions = []

                            for t in transactions:
                                if multi_account_mode and source_account_col:
                                    # Multi-account mode WITH source column - use account mapping
                                    csv_account = str(t.get('source_account', ''))
                                    account_mapping = st.session_state.get('account_mapping', {})
                                    mapped_account_id = account_mapping.get(csv_account, 0)

                                    if mapped_account_id == 0:
                                        skipped += 1
                                        continue

                                    t['bank_account_id'] = mapped_account_id

                                    # Apply sign convention based on account type
                                    mapped_account = Account.get_by_id(mapped_account_id, client_id=client_id)
                                    if mapped_account and mapped_account.type == 'Liability':
                                        # Credit card: positive = expense, flip to negative
                                        t['amount'] = -t['amount']
                                    # Asset accounts: no change needed (bank convention)
                                else:
                                    # Single account mode OR multi-account without source column
                                    # Both use the selected_bank and sign_convention.
                                    # Same helper the Confirm Import totals use, so
                                    # the preview cannot disagree with what posts.
                                    t['amount'] = apply_sign_convention(t['amount'], sign_convention)

                                valid_transactions.append(t)

                            transactions = valid_transactions

                            if skipped > 0:
                                st.warning(f"Skipped {skipped} transactions from unmapped accounts.")

                            if not transactions:
                                st.error("No valid transactions after applying account mapping.")
                            else:
                                # First check learned patterns; durable duplicate
                                # classification runs after account assignment.
                                for t in transactions:
                                    # Set bank account for single-account mode or multi-account without source column
                                    if not multi_account_mode or (multi_account_mode and source_account_col is None):
                                        t['bank_account_id'] = selected_bank
                                    t['client_id'] = client_id

                                    # Check learned patterns
                                    match = PatternLearner.find_match(client_id, t['description'])
                                    if match:
                                        t['suggested_account_id'] = match['account_id']
                                        t['confidence'] = f"{match['confidence']:.0%}"
                                        t['reason'] = f"Learned pattern: {match['pattern']}"

                                duplicate_count = apply_duplicate_checks(transactions)

                                st.session_state.transactions_to_review = transactions

                                # Assign a stable per-transaction id so per-row widget
                                # state survives re-sorting, then pre-populate selectbox
                                # state with AI suggestions.
                                ensure_row_ids(transactions)
                                for t in transactions:
                                    if 'suggested_account_id' in t and t['suggested_account_id']:
                                        st.session_state[row_key("cat", t)] = t['suggested_account_id']

                                # Show success message with duplicate warning if applicable
                                if duplicate_count > 0:
                                    st.warning(f"Found {duplicate_count} potential duplicate transaction(s) that have been auto-deselected.")
                                st.success(f"Parsed {len(transactions)} transactions!")

                                # Auto-navigate to Review tab
                                st.session_state.import_active_tab = "Review & Categorize"
                                st.rerun()

                    except Exception as e:
                        st.error(f"Error parsing file: {e}")

elif selected_tab == "Upload Statement":
    st.subheader("Upload PDF or Image Statement")
    st.caption(
        "PDF text extraction and image OCR run locally on this Mac. Uploaded documents are "
        "held only for the current session and are not saved to the LedgerTB database."
    )

    all_importable = [a for a in accounts if a.type in ("Asset", "Liability")]
    bank_account_options = {a.id: a.display_name() for a in all_importable}
    if not bank_account_options:
        st.warning("No bank or credit-card accounts found. Add one in Chart of Accounts first.")
    else:
        setup_col1, setup_col2, setup_col3 = st.columns(3)
        with setup_col1:
            document_bank_id = st.selectbox(
                "Statement account",
                options=list(bank_account_options),
                format_func=lambda account_id: bank_account_options[account_id],
                key="document_bank_account",
            )
        document_account = Account.get_by_id(document_bank_id, client_id=client_id)
        # index= was ignored here: a keyed widget takes its value from session
        # state, so the derived default applied on the first render only and
        # switching to a credit-card account left "Bank Account" in place.
        apply_default_on_change(
            "document_sign_convention",
            depends_on=document_bank_id,
            default_value=default_sign_convention(
                document_account.type if document_account else None
            ),
        )
        with setup_col2:
            document_sign = st.selectbox(
                "Printed amount convention",
                options=list(SIGN_CONVENTIONS),
                format_func=lambda value: SIGN_CONVENTIONS[value],
                key="document_sign_convention",
                help="Set from the account type. Used by the local parser; "
                     "AI-assisted parsing normalizes signs itself.",
            )
        with setup_col3:
            statement_year = st.number_input(
                "Statement year", min_value=2000, max_value=2100,
                value=date.today().year, step=1,
                help="Used when statement rows show month/day without a year.",
            )

        uploaded_document = st.file_uploader(
            "Statement file", type=["pdf", "png", "jpg", "jpeg"],
            key="statement_document_upload",
        )
        if uploaded_document is not None:
            document_bytes = uploaded_document.getvalue()
            document_identity = (uploaded_document.name, hash_source(document_bytes))
            if st.session_state.get("document_identity") != document_identity:
                st.session_state.document_identity = document_identity
                st.session_state.document_name = uploaded_document.name
                st.session_state.document_bytes = document_bytes
                st.session_state.document_extraction = None
                st.session_state.document_transactions = []

        if st.session_state.get("document_bytes"):
            action_col1, action_col2 = st.columns([1, 4])
            with action_col1:
                if st.button("Extract text locally", type="primary"):
                    try:
                        with st.spinner("Reading statement locally…"):
                            extraction = extract_document(
                                st.session_state.document_name,
                                st.session_state.document_bytes,
                            )
                        st.session_state.document_extraction = extraction
                        st.session_state.document_text_editor = extraction.text
                        st.session_state.document_transactions = []
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Statement extraction failed: {exc}")
            with action_col2:
                if st.button("Clear statement"):
                    for key in (
                        "document_identity", "document_name", "document_bytes",
                        "document_extraction", "document_text_editor", "document_transactions",
                    ):
                        st.session_state.pop(key, None)
                    st.rerun()

        extraction = st.session_state.get("document_extraction")
        if extraction:
            st.success(
                f"Extracted {extraction.page_count} page(s): "
                f"{extraction.native_text_pages} text page(s), {extraction.ocr_pages} OCR page(s)."
            )
            for warning in extraction.warnings:
                st.warning(warning)

            st.subheader("Extracted statement text")
            st.caption("Review and correct the text before parsing. This is especially important for OCR amounts.")
            extracted_text = st.text_area(
                "Extracted text",
                value=st.session_state.get("document_text_editor", extraction.text),
                height=300,
                key="document_text_editor",
                label_visibility="collapsed",
            )

            parser_options = ["Local pattern parser"]
            if categorization_service.is_available():
                parser_options.append("AI-assisted table parser")
            parser_mode = st.radio(
                "Statement parser", options=parser_options, horizontal=True,
                help="The local parser is private and deterministic. AI is better for complex debit/credit tables.",
            )

            ai_consent = False
            amount_strategy = "first"
            if parser_mode == "Local pattern parser":
                amount_label = st.selectbox(
                    "When a row contains both transaction amount and running balance",
                    options=["Use first monetary amount", "Use last monetary amount"],
                )
                amount_strategy = "first" if amount_label.startswith("Use first") else "last"
                st.info(
                    "Local parsing works best when every transaction starts with a date and signed amount. "
                    "For statements with separate debit and credit columns, use AI-assisted parsing or export CSV."
                )
            else:
                st.warning(
                    "AI-assisted parsing sends the extracted statement text—including transaction dates, "
                    "descriptions, and amounts—to Anthropic. The PDF/image itself is not uploaded."
                )
                ai_consent = st.checkbox(
                    "I consent to sending this extracted statement text to Anthropic for parsing.",
                    key="statement_ai_consent",
                )

            parse_disabled = parser_mode == "AI-assisted table parser" and not ai_consent
            if st.button("Parse statement transactions", type="primary", disabled=parse_disabled):
                try:
                    with st.spinner("Parsing transaction rows…"):
                        if parser_mode == "AI-assisted table parser":
                            parsed = parse_statement_with_ai(
                                extracted_text, document_account.type,
                                ANTHROPIC_API_KEY, ANTHROPIC_MODEL,
                            )
                            skipped = []
                            amounts_are_normalized = True
                        else:
                            parsed, skipped = parse_statement_text(
                                extracted_text, int(statement_year), amount_strategy,
                            )
                            amounts_are_normalized = False
                    if not parsed:
                        st.error("No transaction rows could be parsed. Review the extracted text or use CSV.")
                    else:
                        st.session_state.document_transactions = parsed
                        st.session_state.document_skipped = skipped
                        st.session_state.document_amounts_normalized = amounts_are_normalized
                        st.rerun()
                except Exception as exc:
                    st.error(f"Statement parsing failed: {exc}")

            parsed_document_rows = st.session_state.get("document_transactions", [])
            if parsed_document_rows:
                st.subheader("Validate parsed transactions")
                st.warning(
                    "OCR and table parsing can misread dates, decimal points, and signs. "
                    "Compare this table to the statement before continuing."
                )
                skipped = st.session_state.get("document_skipped", [])
                if skipped:
                    with st.expander(f"{len(skipped)} date-led row(s) could not be parsed"):
                        for row in skipped[:50]:
                            st.text(row)

                parsed_frame = pd.DataFrame([{
                    "Date": row["date"],
                    "Description": row["description"],
                    "Amount": row["amount"],
                } for row in parsed_document_rows])
                edited_frame = st.data_editor(
                    parsed_frame,
                    hide_index=True,
                    width="stretch",
                    num_rows="dynamic",
                    column_config={
                        "Date": st.column_config.DateColumn("Date", required=True),
                        "Description": st.column_config.TextColumn("Description", required=True),
                        "Amount": st.column_config.NumberColumn("Amount", format="$%.2f", required=True),
                    },
                    key="document_transaction_editor",
                )

                validation_confirmed = st.checkbox(
                    "I compared the parsed dates, descriptions, amounts, and signs to the statement.",
                    key="document_validation_confirmed",
                )
                if st.button(
                    f"Send {len(edited_frame)} transactions to review",
                    type="primary", disabled=not validation_confirmed,
                ):
                    try:
                        if edited_frame.empty:
                            raise ValueError("At least one transaction is required.")
                        batch_id = str(parsed_document_rows[0].get("batch_id", "document"))
                        review_transactions = []
                        for row_position, (_, row) in enumerate(edited_frame.iterrows(), start=1):
                            row_date = row["Date"]
                            if hasattr(row_date, "date") and not isinstance(row_date, date):
                                row_date = row_date.date()
                            if isinstance(row_date, str):
                                row_date = date.fromisoformat(row_date)
                            description = str(row["Description"]).strip()
                            amount = float(row["Amount"])
                            if not description:
                                raise ValueError("Every row needs a description.")
                            if not st.session_state.get("document_amounts_normalized", False):
                                if document_sign in ("credit_card", "flip"):
                                    amount = -amount
                            review_transactions.append({
                                "date": row_date, "description": description[:200],
                                "amount": amount, "batch_id": batch_id,
                                "bank_account_id": document_bank_id,
                                "client_id": client_id,
                                "source_id": hash_source(st.session_state.document_bytes),
                                "source_filename": st.session_state.get("document_name"),
                                "source_row_number": row_position,
                            })

                        duplicate_count = apply_duplicate_checks(review_transactions)
                        for transaction in review_transactions:
                            match = PatternLearner.find_match(client_id, transaction["description"])
                            if match:
                                transaction["suggested_account_id"] = match["account_id"]
                                transaction["confidence"] = f"{match['confidence']:.0%}"
                                transaction["reason"] = f"Learned pattern: {match['pattern']}"

                        ensure_row_ids(review_transactions)
                        for transaction in review_transactions:
                            if transaction.get("suggested_account_id"):
                                st.session_state[row_key("cat", transaction)] = transaction["suggested_account_id"]
                        st.session_state.transactions_to_review = review_transactions
                        st.session_state.import_active_tab = "Review & Categorize"
                        # Same reason as the CSV path: a stale completion flag
                        # would hide these rows behind the previous batch's
                        # "What's next?" screen.
                        st.session_state.import_complete = False
                        st.session_state.import_complete_msg = None
                        if duplicate_count:
                            st.session_state.post_result = {
                                "level": "warning",
                                "text": f"{duplicate_count} potential duplicate(s) were auto-deselected.",
                            }
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not prepare statement transactions: {exc}")

elif selected_tab == "Review & Categorize":
    st.subheader("Review & Categorize Transactions")

    # Show the result of a partial "Post Transactions" run (some rows kept).
    if st.session_state.get('post_result'):
        _pr = st.session_state.post_result
        if _pr.get('level') == 'warning':
            st.warning(_pr['text'])
        else:
            st.info(_pr['text'])
        if _pr.get('errors'):
            st.error("Errors: " + '; '.join(_pr['errors']))
        st.session_state.post_result = None

    # Check if import just completed - show "What's next?" prompt
    if st.session_state.get('import_complete'):
        st.success(st.session_state.get('import_complete_msg', 'Import complete!'))

        st.divider()
        st.subheader("What's next?")

        col1, col2, col3 = st.columns([1, 1, 2])
        with col1:
            if st.button("Import More Transactions", type="primary", width="stretch"):
                st.session_state.import_complete = False
                st.session_state.import_complete_msg = None
                st.session_state.csv_content = None
                st.session_state.csv_filename = None
                st.session_state.csv_source_id = None
                st.session_state.import_active_tab = "Upload CSV"
                st.rerun()
        with col2:
            if st.button("Done - Go to Dashboard", type="secondary", width="stretch"):
                st.session_state.import_complete = False
                st.session_state.import_complete_msg = None
                st.switch_page("pages/7_Dashboard.py")

        st.stop()

    # Assistant-staged rows (MCP propose_import) wait in the database until
    # loaded here; they then run through the exact same categorize/post flow
    # as a CSV upload — same duplicate handling, same human posting.
    _in_session = {t.get("staged_id") for t in st.session_state.transactions_to_review}
    _staged = [t for t in ImportedTransaction.get_by_status(client_id, "Pending")
               if t.id not in _in_session]
    if _staged:
        _sc1, _sc2, _sc3 = st.columns([3, 1, 1])
        with _sc1:
            noun = "transaction" if len(_staged) == 1 else "transactions"
            st.info(f"{len(_staged)} {noun} staged by your assistant await "
                    "review.", icon="📥")
        with _sc2:
            if st.button("Load staged", key="load_staged_imports", width="stretch"):
                staged_rows = [{
                    "staged_id": t.id,
                    "batch_id": t.import_batch,
                    "date": t.transaction_date,
                    "description": t.description,
                    "amount": t.amount,
                    "client_id": client_id,
                    "bank_account_id": t.bank_account_id,
                    "source_id": t.source_id,
                    "source_filename": t.source_filename,
                    "source_row_number": t.source_row_number,
                    "row_fingerprint": t.row_fingerprint,
                    "idempotency_key": t.idempotency_key,
                } for t in _staged]
                # Exclude the rows' own records or each would match itself.
                duplicate_count = classify_import_duplicates(
                    staged_rows, client_id,
                    exclude_ids=frozenset(t.id for t in _staged),
                )
                ensure_row_ids(staged_rows)
                for row in staged_rows:
                    row["include"] = not row.get("is_duplicate", False)
                st.session_state.transactions_to_review = (
                    st.session_state.transactions_to_review + staged_rows)
                st.session_state.import_complete = False
                st.session_state.import_complete_msg = None
                if duplicate_count:
                    st.session_state.post_result = {
                        "level": "warning",
                        "text": f"{duplicate_count} potential duplicate(s) "
                                "were auto-deselected.",
                    }
                st.rerun()
        with _sc3:
            if st.button("Dismiss staged", key="dismiss_staged_imports",
                         width="stretch"):
                st.session_state.confirm_dismiss_staged = {
                    "client_id": client_id,
                    "ids": [t.id for t in _staged],
                }
                st.rerun()

    _dismiss_request = st.session_state.get("confirm_dismiss_staged")
    if (_dismiss_request
            and _dismiss_request.get("client_id") != client_id):
        st.session_state.confirm_dismiss_staged = None
        _dismiss_request = None
    _dismiss_ids = _dismiss_request["ids"] if _dismiss_request else []
    if _dismiss_ids:
        st.warning(
            f"Dismiss {len(_dismiss_ids)} staged transaction"
            f"{'' if len(_dismiss_ids) == 1 else 's'}? They will leave the review "
            "queue but remain in history and cannot be staged again."
        )
        _dc1, _dc2, _dc3 = st.columns([1, 1, 3])
        with _dc1:
            if st.button("Confirm dismissal", type="primary",
                         key="confirm_dismiss_staged_button"):
                try:
                    _dismissed = ImportedTransaction.dismiss_pending(
                        client_id, _dismiss_ids)
                    _dismissed_ids = set(_dismiss_ids)
                    st.session_state.transactions_to_review = [
                        row for row in st.session_state.transactions_to_review
                        if row.get("staged_id") not in _dismissed_ids
                    ]
                    st.session_state.confirm_dismiss_staged = None
                    st.session_state.post_result = {
                        "level": "info",
                        "text": f"Dismissed {_dismissed} staged transaction"
                                f"{'' if _dismissed == 1 else 's'}.",
                    }
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not dismiss staged transactions: {exc}")
        with _dc2:
            if st.button("Cancel", key="cancel_dismiss_staged_button"):
                st.session_state.confirm_dismiss_staged = None
                st.rerun()

    if not st.session_state.transactions_to_review:
        st.info("No transactions to review. Upload a CSV file first."
                if not _staged else
                "Load the staged transactions above to review them.")
    else:
        transactions = st.session_state.transactions_to_review

        # Account options for dropdowns. No "-- Select Account --" pseudo-option:
        # its label becomes the search text, so typing an account number appends
        # to it and matches nothing. Unset is selectbox index=None instead.
        all_accounts = Account.get_all(client_id, active_only=True)
        account_options = {a.id: a.display_name() for a in all_accounts}

        # "Add new account" lives inside the dropdown itself: picking it opens
        # an inline form under that row, and the created account is selected in
        # place. -1 can never collide with a real account id.
        ADD_NEW_ACCOUNT = -1
        ADD_NEW_LABEL = "➕ Add new account…"
        category_option_ids = list(account_options.keys()) + [ADD_NEW_ACCOUNT]

        def category_label(account_id):
            return ADD_NEW_LABEL if account_id == ADD_NEW_ACCOUNT else account_options[account_id]

        # Per-target input keys: a shared key would let the browser re-impose a
        # previous row's typed values when the form re-registers elsewhere.
        def _fkey(name, target_key):
            return f"newacct_{name}_{target_key}"

        def _create_quick_account(target_key):
            number = (st.session_state.get(_fkey("number", target_key)) or "").strip()
            name = (st.session_state.get(_fkey("name", target_key)) or "").strip()
            acct_type = st.session_state.get(_fkey("type", target_key)) or "Expense"
            subtype = (st.session_state.get(_fkey("subtype", target_key)) or "").strip()
            if not number or not name:
                st.session_state.quick_add_account_error = "Account number and name are both required."
                return
            try:
                account = Account(
                    client_id=client_id,
                    account_number=number,
                    name=name,
                    type=acct_type,
                    subtype=subtype or None,
                )
                new_id = account.save()
            except Exception as exc:
                if "UNIQUE constraint" in str(exc):
                    st.session_state.quick_add_account_error = (
                        f"Account {number} already exists — pick it from the list instead."
                    )
                else:
                    st.session_state.quick_add_account_error = str(exc)
                return
            # Callbacks run before widgets render, which is the one legal moment
            # to overwrite the dropdown's keyed state with the new account.
            st.session_state[target_key] = new_id
            st.session_state.quick_add_account_msg = (
                f"Added {number} · {name} to the chart of accounts."
            )
            st.session_state.pop("quick_add_account_error", None)

        def _cancel_quick_add(target_key):
            st.session_state[target_key] = None
            st.session_state.pop("quick_add_account_error", None)

        def render_quick_add_form(target_key):
            with st.container(border=True):
                st.markdown("**New account** — added to the chart of accounts and selected here.")
                c1, c2, c3, c4 = st.columns([1, 2, 1, 1])
                with c1:
                    st.text_input("Number", key=_fkey("number", target_key), placeholder="6100")
                with c2:
                    st.text_input("Name", key=_fkey("name", target_key), placeholder="Office Supplies")
                with c3:
                    st.selectbox("Type", options=['Expense', 'Revenue', 'Asset', 'Liability', 'Equity'],
                                 key=_fkey("type", target_key),
                                 help="Most transaction categories are Expense or Revenue")
                with c4:
                    st.text_input("Subtype (optional)", key=_fkey("subtype", target_key))
                if st.session_state.get("quick_add_account_error"):
                    st.error(st.session_state.pop("quick_add_account_error"))
                b1, b2, _ = st.columns([1, 1, 4])
                with b1:
                    st.button("Create account", type="primary",
                              key=f"newacct_save_{target_key}",
                              on_click=_create_quick_account, args=(target_key,))
                with b2:
                    st.button("Cancel", key=f"newacct_cancel_{target_key}",
                              on_click=_cancel_quick_add, args=(target_key,))

        # Ensure a stable per-transaction id (used to key all per-row widgets so
        # their state follows the transaction across re-sorts) and include flags.
        ensure_row_ids(transactions)
        for t in transactions:
            if 'include' not in t:
                t['include'] = True

        # Summary and bulk actions
        included_count = sum(1 for t in transactions if t.get('include', True))
        uncategorized_count = sum(1 for t in transactions if not t.get('selected_account_id') and 'suggested_account_id' not in t)
        duplicate_count = sum(1 for t in transactions if t.get('is_duplicate', False))

        col1, col2, col3, col4, col5 = st.columns([1, 1, 1, 1, 1])
        with col1:
            st.metric("Total", len(transactions))
        with col2:
            st.metric("Selected", included_count)
        with col3:
            parked_count = sum(
                1 for t in transactions
                if is_parking_account(account_options.get(
                    st.session_state.get(row_key("cat", t)), ""))
            )
            if parked_count:
                st.metric("Uncategorized", uncategorized_count,
                          delta=f"{parked_count} parked for review",
                          delta_color="inverse")
            else:
                st.metric("Uncategorized", uncategorized_count)
        with col4:
            if duplicate_count > 0:
                st.metric("Duplicates", duplicate_count, delta="Review", delta_color="inverse")
            else:
                st.metric("Duplicates", 0)
        with col5:
            subcol1, subcol2 = st.columns(2)
            with subcol1:
                if st.button("Select All", key="select_all_top"):
                    for t in transactions:
                        # An overridden duplicate is selectable; no reason needed.
                        # An exact re-import of an already-posted source row never
                        # is — that would double-count.
                        duplicate_allowed = (
                            not t.get("is_duplicate")
                            or (
                                t.get("duplicate_override")
                                and not t.get("duplicate_info", {}).get("exact_retry")
                            )
                        )
                        t['include'] = bool(duplicate_allowed)
                        st.session_state[row_key("include", t)] = bool(duplicate_allowed)
                    st.rerun()
            with subcol2:
                if st.button("Deselect All", key="deselect_all_top"):
                    for t in transactions:
                        t['include'] = False
                        st.session_state[row_key("include", t)] = False
                    st.rerun()

        # Sorting options
        st.divider()
        sort_col1, sort_col2, sort_col3 = st.columns([1, 1, 3])
        with sort_col1:
            sort_by = st.selectbox(
                "Sort by",
                options=["Date", "Description", "Amount"],
                index=0,
                key="sort_by"
            )
        with sort_col2:
            sort_order = st.selectbox(
                "Order",
                options=["Ascending", "Descending"],
                index=0,
                key="sort_order"
            )

        # Apply sorting
        reverse = (sort_order == "Descending")
        if sort_by == "Date":
            transactions.sort(key=lambda x: x.get('date', ''), reverse=reverse)
        elif sort_by == "Description":
            transactions.sort(key=lambda x: x.get('description', '').lower(), reverse=reverse)
        elif sort_by == "Amount":
            transactions.sort(key=lambda x: x.get('amount', 0), reverse=reverse)

        # Update session state with sorted order
        st.session_state.transactions_to_review = transactions

        # AI Categorization section
        st.divider()
        st.markdown("**AI-Powered Categorization**")

        # Show previous categorization result if any
        if st.session_state.get('ai_categorization_result'):
            result = st.session_state.ai_categorization_result
            if result.get('error'):
                st.error(f"AI categorization error: {result['error']}")
            elif result.get('matched', 0) > 0:
                st.success(f"AI categorization complete! Matched {result['matched']} of {result['total']} transactions.")
            else:
                st.warning(f"AI processed {result.get('total', 0)} transactions but none matched your accounts.")
            # Clear the message after showing
            st.session_state.ai_categorization_result = None

        if categorization_service.is_available():
            # Build list of uncategorized transactions.
            # Check session state for current selection, not transaction dict.
            uncategorized = [
                t for t in transactions
                if not st.session_state.get(row_key("cat", t))
            ]

            if uncategorized:
                col1, col2 = st.columns([2, 2])
                with col1:
                    if st.button(f"Categorize {len(uncategorized)} transactions with AI", type="secondary"):
                        with st.spinner("AI is analyzing transactions..."):
                            # Get expense and revenue accounts for suggestions
                            expense_accts = [a for a in all_accounts if a.type == 'Expense']
                            revenue_accts = [a for a in all_accounts if a.type == 'Revenue']
                            categorization_service.categorize_transactions(
                                uncategorized,
                                expense_accts + revenue_accts
                            )

                        # Store result in session state for display after rerun
                        if hasattr(categorization_service, 'last_error') and categorization_service.last_error:
                            st.session_state.ai_categorization_result = {
                                'error': categorization_service.last_error
                            }
                        else:
                            st.session_state.ai_categorization_result = {
                                'matched': getattr(categorization_service, 'last_matched', 0),
                                'total': getattr(categorization_service, 'last_total', 0),
                            }

                        # Update the selectbox session state keys to match AI suggestions.
                        # Only the transactions that were just categorized (uncategorized
                        # holds references to the same dicts, now mutated by the AI call),
                        # so manual selections the user already made are preserved.
                        for t in uncategorized:
                            if 'suggested_account_id' in t and t['suggested_account_id']:
                                st.session_state[row_key("cat", t)] = t['suggested_account_id']

                        # Save updated transactions to session state
                        st.session_state.transactions_to_review = transactions
                        st.rerun()
                with col2:
                    st.caption(
                        "Sends transaction dates, descriptions, amounts, and the "
                        "available account names/numbers to Anthropic. Suggestions "
                        "only; nothing posts automatically."
                    )
            else:
                st.success("All transactions have been categorized!")
        else:
            # Configuration lives on Firm Settings with the rest of the
            # firm-level setup; this workflow page only points there.
            st.caption("AI categorization is off — add your Anthropic API key "
                       "on the Firm Settings page to enable suggestions here.")
            st.page_link("pages/12_Firm_Settings.py",
                         label="Set up AI categorization", icon=icons.FIRM)

        # Bulk categorization section
        st.divider()
        st.markdown("**Bulk Categorization**")
        st.caption("Deselect all, then check the transactions you want to categorize together")

        # Selection controls
        sel_col1, sel_col2, sel_col3, sel_col4 = st.columns([1, 1, 1, 2])
        with sel_col1:
            if st.button("Deselect All", key="deselect_bulk"):
                for t in transactions:
                    t['include'] = False
                    st.session_state[row_key("include", t)] = False
                st.rerun()
        with sel_col2:
            if st.button("Select All", key="select_bulk"):
                for t in transactions:
                    t['include'] = True
                    st.session_state[row_key("include", t)] = True
                st.rerun()
        with sel_col3:
            # Count selected using session state checkbox values
            selected_count = sum(1 for t in transactions if st.session_state.get(row_key("include", t), True))
            st.markdown(f"**{selected_count}** selected")

        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            bulk_account = st.selectbox(
                "Account to apply",
                options=category_option_ids,
                format_func=category_label,
                key="bulk_account_select",
                index=None,
                placeholder="Type an account number or name",
            )
        with col2:
            if st.button("Apply to Selected", type="primary"):
                if not bulk_account or bulk_account == ADD_NEW_ACCOUNT:
                    st.warning("Please select an account first")
                else:
                    applied_count = 0
                    for t in transactions:
                        # Check session state for checkbox value
                        is_selected = st.session_state.get(row_key("include", t), True)
                        if is_selected:
                            t['selected_account_id'] = bulk_account
                            t['suggested_account_id'] = bulk_account
                            # Update the selectbox session state
                            st.session_state[row_key("cat", t)] = bulk_account
                            applied_count += 1
                    # Deselect all checkboxes after applying
                    for t in transactions:
                        st.session_state[row_key("include", t)] = False
                        t['include'] = False
                    # Save changes to session state
                    st.session_state.transactions_to_review = transactions
                    if applied_count > 0:
                        st.session_state.bulk_result = f"Applied to {applied_count} transactions"
                    else:
                        st.session_state.bulk_result = "No transactions selected"
                    st.rerun()
        with col3:
            if st.button("Apply to Uncategorized"):
                if not bulk_account or bulk_account == ADD_NEW_ACCOUNT:
                    st.warning("Please select an account first")
                else:
                    applied_count = 0
                    for t in transactions:
                        is_selected = st.session_state.get(row_key("include", t), True)
                        # None/0/absent all mean uncategorized
                        current_category = st.session_state.get(row_key("cat", t))
                        if is_selected and not current_category:
                            t['selected_account_id'] = bulk_account
                            t['suggested_account_id'] = bulk_account
                            # Update the selectbox session state
                            st.session_state[row_key("cat", t)] = bulk_account
                            applied_count += 1
                    # Deselect all checkboxes after applying
                    for t in transactions:
                        st.session_state[row_key("include", t)] = False
                        t['include'] = False
                    # Save changes to session state
                    st.session_state.transactions_to_review = transactions
                    st.session_state.bulk_result = f"Applied to {applied_count} uncategorized transactions"
                    st.rerun()

        # The bulk dropdown can create an account too.
        if st.session_state.get("bulk_account_select") == ADD_NEW_ACCOUNT:
            render_quick_add_form("bulk_account_select")

        # Show bulk result message if any
        if st.session_state.get('bulk_result'):
            st.info(st.session_state.bulk_result)
            st.session_state.bulk_result = None

        if st.session_state.get('quick_add_account_msg'):
            st.success(st.session_state.pop('quick_add_account_msg'))

        # Review each transaction - header row
        st.divider()

        header_cols = st.columns([0.5, 0.9, 2.2, 1, 0.6, 2])
        with header_cols[0]:
            st.markdown("**Select**")
        with header_cols[1]:
            st.markdown("**Date**")
        with header_cols[2]:
            st.markdown("**Description**")
        with header_cols[3]:
            st.markdown("**Amount**")
        with header_cols[4]:
            st.markdown("**Xfer**")
        with header_cols[5]:
            st.markdown("**Category/Transfer Account**")

        st.divider()

        # Build transfer account options (only Asset/Liability accounts for transfers)
        transfer_accounts = [a for a in all_accounts if a.type in ('Asset', 'Liability')]
        transfer_options = {a.id: a.display_name() for a in transfer_accounts}

        for i, t in enumerate(transactions):
            duplicate_select_disabled = False
            if t.get("is_duplicate"):
                duplicate_kind = t.get("duplicate_kind")
                duplicate_info = t.get("duplicate_info", {})
                if duplicate_kind == "within_upload":
                    duplicate_message = "Duplicate row in this upload"
                    matched_row = duplicate_info.get("source_row_number") or duplicate_info.get("upload_position")
                    duplicate_detail = f"Matches an earlier upload row ({matched_row})."
                elif duplicate_kind == "previous_import":
                    duplicate_message = "Previously imported transaction"
                    duplicate_detail = (
                        f"Matches transaction #{duplicate_info.get('transaction_id', '?')}"
                        f" / journal entry #{duplicate_info.get('entry_id', '?')}."
                    )
                else:
                    duplicate_message = "Possible journal-entry duplicate"
                    duplicate_detail = (
                        f"Matches journal entry #{duplicate_info.get('entry_id', '?')} "
                        f"on {duplicate_info.get('entry_date', '?')}."
                    )

                with st.container(border=True):
                    st.warning(duplicate_message)
                    st.caption(duplicate_detail)
                    if duplicate_info.get("exact_retry"):
                        st.info(
                            "This is the same source row as a prior import. It cannot be posted twice; "
                            "the existing journal entry remains unchanged."
                        )
                        transactions[i]["duplicate_override"] = False
                        transactions[i]["duplicate_override_reason"] = ""
                        duplicate_select_disabled = True
                    else:
                        override = st.checkbox(
                            "Post this transaction anyway",
                            value=bool(t.get("duplicate_override", False)),
                            key=row_key("duplicate_override", t),
                            help="Use only when the statement truly contains a separate, legitimate transaction.",
                        )
                        reason = ""
                        if override:
                            # Optional. The checkbox is the decision and the
                            # OVERRIDE audit event records it either way; a
                            # required reason only blocked importing statements
                            # that legitimately repeat an identical charge.
                            reason = st.text_input(
                                "Reason (optional)",
                                value=t.get("duplicate_override_reason", ""),
                                key=row_key("duplicate_reason", t),
                                placeholder="Example: Two separate purchases for the same amount",
                            ).strip()
                        transactions[i]["duplicate_override"] = override
                        transactions[i]["duplicate_override_reason"] = reason
                        duplicate_select_disabled = not override

                        # Ticking the override has to select the row too. The row
                        # was force-deselected when the duplicate was detected and
                        # nothing else would ever re-select it, so the override
                        # appeared to do nothing: the row still posted as excluded.
                        # Driven off the override's own transitions, so a row
                        # deliberately deselected after overriding stays that way.
                        apply_default_on_change(
                            row_key("include", t),
                            depends_on=override,
                            default_value=override,
                        )

                if duplicate_select_disabled:
                    transactions[i]["include"] = False
                    st.session_state[row_key("include", t)] = False

            col0, col1, col2, col3, col4, col5 = st.columns([0.5, 0.9, 2.2, 1, 0.6, 2])

            include_key = row_key("include", t)
            with col0:
                # Initialize session state for checkbox if not set
                if include_key not in st.session_state:
                    st.session_state[include_key] = t.get('include', True)

                include = st.checkbox(
                    "Select",
                    key=include_key,
                    disabled=duplicate_select_disabled,
                    label_visibility="collapsed"
                )
                # Sync back to transaction data
                transactions[i]['include'] = include

            with col1:
                st.text(str(t['date']))

            with col2:
                # Show the whole description, not the first 35 characters.
                # Truncating here meant a padded description could hide text
                # from the reviewer that the categorization model still read —
                # the reviewer must see exactly what the suggestion was based
                # on. Long ones collapse behind an expander rather than
                # vanishing.
                _desc = str(t['description'])
                if len(_desc) <= 35:
                    st.text(_desc)
                else:
                    st.text(_desc[:35] + "…")
                    with st.expander("Full description"):
                        st.text(_desc)
                # Show source account if from multi-account import
                if t.get('source_account'):
                    source_acct = Account.get_by_id(t.get('bank_account_id'), client_id=client_id)
                    if source_acct:
                        st.caption(f"From: {source_acct.display_name()}")
                if t.get('reason'):
                    st.caption(t['reason'])

            with col3:
                color = "green" if t['amount'] >= 0 else "red"
                st.markdown(f":{color}[${abs(t['amount']):,.2f}]")

            with col4:
                # Transfer toggle
                is_transfer = st.checkbox(
                    "Xfer",
                    value=t.get('is_transfer', False),
                    key=row_key("xfer", t),
                    help="Check if this is a transfer between accounts (e.g., credit card payment)"
                )
                transactions[i]['is_transfer'] = is_transfer

            with col5:
                # Initialize session state for this selectbox if not already set
                cat_key = row_key("cat", t)
                if cat_key not in st.session_state:
                    st.session_state[cat_key] = t.get('suggested_account_id') or None

                if is_transfer:
                    # For transfers, show only bank/liability accounts
                    # Ensure the current value is valid for transfer options
                    if st.session_state[cat_key] not in transfer_options:
                        st.session_state[cat_key] = None
                    selected = st.selectbox(
                        "Transfer To/From",
                        options=list(transfer_options.keys()),
                        format_func=lambda x: transfer_options[x],
                        key=cat_key,
                        index=None,
                        placeholder="Type an account number or name",
                        label_visibility="collapsed"
                    )
                else:
                    # For regular transactions, show all accounts
                    # Ensure the current value is valid for account options
                    if (st.session_state[cat_key] not in account_options
                            and st.session_state[cat_key] != ADD_NEW_ACCOUNT):
                        st.session_state[cat_key] = None
                    selected = st.selectbox(
                        "Account",
                        options=category_option_ids,
                        format_func=category_label,
                        key=cat_key,
                        index=None,
                        placeholder="Type an account number or name",
                        label_visibility="collapsed"
                    )
                # Row dicts keep 0 for "unset" so posting validation is
                # unchanged; the add-new sentinel must never leak into a post.
                transactions[i]['selected_account_id'] = (
                    selected if selected and selected != ADD_NEW_ACCOUNT else 0
                )
                if (selected and selected != ADD_NEW_ACCOUNT
                        and is_parking_account(account_options.get(selected, ""))):
                    st.caption(":red[⚠ Parked — still needs a real category]")

            # Picking "Add new account…" opens the form right under this row;
            # creating selects the account here and in the chart of accounts.
            if selected == ADD_NEW_ACCOUNT and not is_transfer:
                render_quick_add_form(cat_key)

        st.divider()

        col1, col2, col3 = st.columns(3)

        with col1:
            if st.button("Post Transactions", type="primary"):
                plan = classify_review_rows(
                    transactions,
                    is_included=lambda t: st.session_state.get(row_key("include", t), True),
                    get_account_id=lambda t: t.get('selected_account_id', 0),
                )
                created = 0
                errors = []
                failed = []

                for t in plan.to_post:
                    try:
                        # Post the transaction as a balanced journal entry. The
                        # journal entry, import record, and learned pattern all
                        # commit together (or roll back together on failure), so
                        # `created` is only incremented once the post fully succeeds.
                        post_transaction(
                            client_id=client_id,
                            transaction=t,
                            target_account_id=t['selected_account_id'],
                            bank_account_id=t['bank_account_id'],
                            is_transfer=t.get('is_transfer', False),
                            batch_id=t['batch_id'],
                            duplicate_override=t.get('duplicate_override', False),
                            duplicate_override_reason=t.get('duplicate_override_reason'),
                        )
                        created += 1

                    except Exception as e:
                        errors.append(f"{t['description'][:30]}: {e}")
                        failed.append(t)  # keep failed rows so they can be retried

                # Kept in the review list: included-but-uncategorized + failed rows.
                remaining = plan.uncategorized + failed
                uncategorized = len(plan.uncategorized)
                skipped = len(plan.excluded)

                # A run that posted nothing at all is a mistake, not a finished
                # import — every row was deselected or blocked. Discarding the
                # batch there means re-uploading to try again, so keep the rows
                # and say what happened instead.
                if created == 0 and skipped:
                    st.session_state.transactions_to_review = transactions
                    st.session_state.post_result = {
                        'level': 'warning',
                        'text': (
                            f"Nothing was posted — all {skipped} row(s) were excluded. "
                            "A row must be selected in the leftmost column to post; "
                            "duplicates are deselected automatically until you tick "
                            "\"Post this transaction anyway\"."
                        ),
                        'errors': errors[:3],
                    }
                    st.rerun()

                if not remaining:
                    # Everything selected was posted; excluded rows acknowledged.
                    msg = f"Posted {created} transaction{'' if created == 1 else 's'}"
                    if skipped > 0:
                        msg += f" — {skipped} excluded"
                    msg += "."
                    st.session_state.transactions_to_review = []
                    st.session_state.import_complete = True
                    st.session_state.import_complete_msg = msg
                    st.rerun()
                else:
                    # Partial: keep unresolved rows and report exactly what happened.
                    st.session_state.transactions_to_review = remaining
                    parts = []
                    if created:
                        parts.append(f"posted {created}")
                    if uncategorized:
                        parts.append(f"{uncategorized} still need a category")
                    if errors:
                        parts.append(f"{len(errors)} failed")
                    if skipped:
                        parts.append(f"{skipped} excluded")
                    st.session_state.post_result = {
                        'level': 'warning' if created else 'info',
                        'text': "Posting: " + ", ".join(parts)
                                + ". Transactions needing attention are kept below.",
                        'errors': errors[:3],
                    }
                    st.rerun()

        with col2:
            if st.button("Clear review list"):
                st.session_state.transactions_to_review = []
                st.rerun()
            _loaded_staged_ids = [
                t["staged_id"] for t in transactions if t.get("staged_id")
            ]
            if (_loaded_staged_ids
                    and st.button("Dismiss staged rows", key="dismiss_loaded_staged")):
                st.session_state.confirm_dismiss_staged = {
                    "client_id": client_id,
                    "ids": _loaded_staged_ids,
                }
                st.rerun()

        with col3:
            if not categorization_service.is_available():
                st.caption("AI categorization unavailable — set ANTHROPIC_API_KEY")

elif selected_tab == "Import History":
    st.subheader("Import History")
    st.caption("Every file imported for this client — what landed, and whether it matches the source.")

    batches = ImportedTransaction.get_batch_summaries(client_id)

    if not batches:
        st.info("Nothing imported yet for this client. Upload a CSV or statement to get started.")
    else:
        def _batch_source(batch):
            """How the batch is identified in the picker and the table."""
            if batch["filename_count"] > 1:
                return f"{batch['filename_count']} files"
            return batch["source_filename"] or "(no filename recorded)"

        def _batch_account(batch):
            if batch["account_count"] > 1:
                return f"{batch['account_count']} accounts"
            if batch["account_number"]:
                return f"{batch['account_number']} - {batch['account_name']}"
            return batch["account_name"] or "—"

        def _batch_status(batch):
            if batch["pending_count"] or batch["categorized_count"]:
                unposted = batch["pending_count"] + batch["categorized_count"]
                text = f"{batch['posted_count']} posted, {unposted} not yet posted"
                if batch["dismissed_count"]:
                    text += f", {batch['dismissed_count']} dismissed"
                return text
            if batch["dismissed_count"]:
                return (f"{batch['posted_count']} posted, "
                        f"{batch['dismissed_count']} dismissed")
            return "All posted"

        overview = pd.DataFrame([{
            "Imported": batch["imported_at"] or "—",
            "Source file": _batch_source(batch),
            "Account": _batch_account(batch),
            "Rows": batch["row_count"],
            "Date range": (
                f"{batch['first_date']:%m/%d/%Y} – {batch['last_date']:%m/%d/%Y}"
                if batch["first_date"] and batch["last_date"] else "—"
            ),
            "Net amount": f"{batch['net_amount']:,.2f}",
            "Status": _batch_status(batch),
            "Batch": batch["import_batch"],
        } for batch in batches])

        st.dataframe(overview, width="stretch", hide_index=True)

        st.divider()
        st.markdown("#### Check an import against its source")

        labels = {
            batch["import_batch"]: (
                f"{_batch_source(batch)} → {_batch_account(batch)} "
                f"({batch['row_count']} rows, {batch['net_amount']:,.2f})"
            )
            for batch in batches
        }
        selected_batch = st.selectbox(
            "Import batch",
            options=list(labels.keys()),
            format_func=lambda b: labels[b],
            key="history_batch",
        )

        batch = next(b for b in batches if b["import_batch"] == selected_batch)
        rows = ImportedTransaction.get_by_batch(client_id, selected_batch)

        metric_cols = st.columns(4)
        metric_cols[0].metric("Rows imported", batch["row_count"])
        metric_cols[1].metric("Net amount", f"{batch['net_amount']:,.2f}")
        metric_cols[2].metric("Deposits in", f"{batch['deposits']:,.2f}")
        metric_cols[3].metric("Payments out", f"{batch['withdrawals']:,.2f}")

        # Cheap check that needs no file: a gap in the recorded source line
        # numbers means a row from the middle of the file never landed.
        continuity = check_row_continuity(rows)
        if continuity.missing_rows:
            st.warning(
                f"Gap in the source rows: line(s) "
                f"{', '.join(str(n) for n in continuity.missing_rows)} of "
                f"{batch['source_filename'] or 'the source file'} were not imported. "
                "Re-check the file below to see exactly what is missing."
            )
        elif continuity.present_count:
            st.success(
                f"Source lines {continuity.first_row}–{continuity.last_row} all "
                f"present ({continuity.present_count} rows, no gaps)."
            )

        if batch["pending_count"] or batch["categorized_count"]:
            st.info(
                f"{batch['posted_count']} of {batch['row_count']} rows are posted to the "
                f"ledger. The rest are waiting in **Review & Categorize**."
            )
        if batch["dismissed_count"]:
            st.caption(
                f"{batch['dismissed_count']} row"
                f"{'' if batch['dismissed_count'] == 1 else 's'} dismissed from review; "
                "the source record and audit history were retained."
            )

        # The stronger check: re-supply the file and compare row by row. Only a
        # comparison against the source can catch rows dropped off the end.
        with st.expander("Compare against the original file", expanded=False):
            st.caption(
                "Upload the same file again to check it line by line. Nothing is "
                "imported — this only compares."
            )
            recheck = st.file_uploader(
                "Original CSV", type=["csv"], key=f"verify_{selected_batch}"
            )
            if recheck is not None:
                try:
                    content = recheck.getvalue().decode("utf-8", errors="replace")
                    preview_df, columns = CSVImporter.preview_csv(content)
                    detected = CSVImporter.detect_columns(columns)
                    if not detected.get("date") or not detected.get("description"):
                        st.error(
                            "Could not find date and description columns in that file."
                        )
                    else:
                        source_rows = CSVImporter.parse_csv(
                            content,
                            date_column=detected["date"],
                            description_column=detected["description"],
                            amount_column=detected.get("amount"),
                            debit_column=detected.get("debit"),
                            credit_column=detected.get("credit"),
                            source_filename=recheck.name,
                        )
                        report = verify_against_source(rows, source_rows)

                        check_cols = st.columns(3)
                        check_cols[0].metric("Rows in file", report.source_count)
                        check_cols[1].metric("Rows imported", report.imported_count)
                        # delta_color="off": any non-zero difference is a problem,
                        # so the usual green-up / red-down reading is misleading.
                        check_cols[2].metric(
                            "Difference", f"{report.difference:,.2f}",
                            delta=None if report.difference == 0 else "does not match",
                            delta_color="off",
                        )

                        if report.is_clean:
                            st.success(
                                f"Every one of the {report.source_count} rows in "
                                f"{recheck.name} is in the ledger, and the totals agree "
                                f"({report.source_total:,.2f})."
                            )
                        else:
                            if report.missing_from_import:
                                st.error(
                                    f"{len(report.missing_from_import)} row(s) in the file "
                                    "were never imported:"
                                )
                                st.dataframe(pd.DataFrame([{
                                    "Date": r["date"],
                                    "Description": r["description"],
                                    "Amount": f"{r['amount']:,.2f}",
                                } for r in report.missing_from_import]),
                                    width="stretch", hide_index=True)
                            if report.not_in_source:
                                st.error(
                                    f"{len(report.not_in_source)} imported row(s) are not "
                                    "in this file — they came from somewhere else, or an "
                                    "amount differs:"
                                )
                                st.dataframe(pd.DataFrame([{
                                    "Date": r.transaction_date,
                                    "Description": r.description,
                                    "Amount": f"{r.amount:,.2f}",
                                    "Status": r.status,
                                } for r in report.not_in_source]),
                                    width="stretch", hide_index=True)
                except Exception as exc:
                    st.error(f"Could not read that file: {exc}")

        st.markdown("##### Imported rows")
        st.caption("In source-file order, so this reads alongside the original.")
        st.dataframe(pd.DataFrame([{
            "Line": r.source_row_number if r.source_row_number is not None else "—",
            "Date": r.transaction_date,
            "Description": r.description,
            "Amount": f"{r.amount:,.2f}",
            "Account": r.bank_account_name or "—",
            "Status": r.status,
            "Entry #": r.journal_entry_id if r.journal_entry_id else "—",
        } for r in rows]), width="stretch", hide_index=True)

elif selected_tab == "Learned Patterns":
    st.subheader("Learned Categorization Patterns")
    st.caption("The system learns from your categorizations to suggest accounts for future transactions.")

    rules = PatternLearner.get_all_rules(client_id)

    if not rules:
        st.info("No patterns learned yet. Import and categorize transactions to build patterns.")
    else:
        # Account options for editing
        account_options = {a.id: a.display_name() for a in Account.get_all(client_id, active_only=True)}

        for rule in rules:
            col1, col2, col3, col4 = st.columns([3, 2, 1, 1])

            with col1:
                st.text(rule['pattern'])

            with col2:
                st.text(f"{rule['account_number']} - {rule['account_name']}")

            with col3:
                st.caption(f"Used {rule['times_used']}x")

            with col4:
                if st.button("Delete", key=f"del_rule_{rule['id']}"):
                    PatternLearner.delete_rule(rule['id'], client_id)
                    st.rerun()
