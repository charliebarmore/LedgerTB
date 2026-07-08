import streamlit as st
import sys
from pathlib import Path
from datetime import date

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.account import Account
from models.client import Client
from models.journal_entry import JournalEntry
from services.csv_import import CSVImporter
from services.categorization import CategorizationService
from services.pattern_learning import PatternLearner
from services.posting import post_transaction
from database import init_database
from utils.client_selector import render_client_selector
from utils.import_review import ensure_row_ids, row_key, classify_review_rows

# Initialize database
init_database()

st.set_page_config(page_title="Import Transactions", page_icon="📥", layout="wide")

# Client selector in sidebar
client_id = render_client_selector()

st.title("📥 Import Transactions")

if not client_id:
    st.warning("Please create a client first in the Clients page.")
    st.page_link("pages/0_Clients.py", label="Go to Clients →")
    st.stop()

# Get client info
client = Client.get_by_id(client_id)
st.caption(f"Viewing: **{client.name}**")

# Initialize services
categorization_service = CategorizationService()
importer = CSVImporter()

# Initialize session state
if 'imported_data' not in st.session_state:
    st.session_state.imported_data = None
if 'column_mapping' not in st.session_state:
    st.session_state.column_mapping = {}
if 'transactions_to_review' not in st.session_state:
    st.session_state.transactions_to_review = []

# Get accounts - include credit cards and other liability accounts for imports
accounts = Account.get_all(client_id, active_only=True)
cash_accounts = [a for a in accounts if a.subtype == 'Cash']
credit_card_accounts = [a for a in accounts if a.subtype == 'Payable' and 'credit card' in a.name.lower()]
# Combine cash and credit card accounts for bank account selection
importable_accounts = cash_accounts + credit_card_accounts
expense_accounts = [a for a in accounts if a.type == 'Expense']
revenue_accounts = [a for a in accounts if a.type == 'Revenue']

# Sign convention options
SIGN_CONVENTIONS = {
    "bank": "Bank Account (negative = expense, positive = deposit)",
    "credit_card": "Credit Card (positive = expense, negative = payment/credit)",
    "flip": "Flip All Signs (reverse the default interpretation)"
}

# Track active tab in session state for programmatic switching
if 'import_active_tab' not in st.session_state:
    st.session_state.import_active_tab = "Upload CSV"

# Navigation using radio buttons (allows programmatic control)
tab_options = ["Upload CSV", "Review & Categorize", "Learned Patterns"]
selected_tab = st.radio(
    "Navigation",
    options=tab_options,
    index=tab_options.index(st.session_state.import_active_tab),
    horizontal=True,
    label_visibility="collapsed"
)
st.session_state.import_active_tab = selected_tab

st.divider()

if selected_tab == "Upload CSV":
    st.subheader("Upload Bank/Credit Card CSV File")

    # Help section
    with st.expander("ℹ️ CSV Import Help & Tips"):
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
        3. Map each account name to a ProBooks account
        """)

        # Sample template download
        st.markdown("### Download Sample Template")
        sample_csv = """Date,Description,Amount
01/15/2025,Sample expense,-100.00
01/16/2025,Sample deposit,500.00
01/17/2025,Another expense,-25.50"""

        st.download_button(
            label="📥 Download Sample CSV Template",
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
            help="Check this if your CSV has a column identifying which account each transaction is from (e.g., exports from Mint, aggregated bank downloads)"
        )

        # Initialize variables
        selected_bank = None
        sign_convention = "bank"
        source_account_col = None  # Initialize here for use later

        if multi_account_mode:
            # Multi-account mode - may or may not have a source account column
            st.info("""
            **Multi-Account Mode**: Select a column that identifies the source account, or choose 'none' to assign all transactions to one account.
            """)
        else:
            # Single account mode - show bank account and sign convention
            col1, col2 = st.columns(2)

            with col1:
                selected_bank = st.selectbox(
                    "Select Account",
                    options=list(bank_account_options.keys()),
                    format_func=lambda x: bank_account_options[x],
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

            with col2:
                sign_convention = st.selectbox(
                    "Sign Convention",
                    options=list(SIGN_CONVENTIONS.keys()),
                    format_func=lambda x: SIGN_CONVENTIONS[x],
                    help="How does this statement show expenses vs deposits?"
                )

            st.caption("""
            **Sign Convention Guide:**
            - **Bank Account**: Most banks show expenses as negative, deposits as positive
            - **Credit Card**: Most credit cards show purchases as positive, payments as negative
            - **Flip All Signs**: Use if your statement is the opposite of the above
            """)

        # File upload
        uploaded_file = st.file_uploader("Upload CSV file", type=['csv'])

        # Handle new file upload
        if uploaded_file:
            raw_content = uploaded_file.read().decode('utf-8')

            # Only initialize if this is a new file
            if st.session_state.get('csv_filename') != uploaded_file.name:
                st.session_state.csv_content = raw_content
                st.session_state.csv_raw_content = raw_content  # Keep original for reset
                st.session_state.csv_filename = uploaded_file.name

        # Show editor if we have CSV content (persists after file uploader clears)
        if st.session_state.get('csv_content'):
            # Editable CSV content
            st.subheader("Edit CSV (Optional)")
            st.caption("Delete extra rows, fix data, or make any changes before importing. First row should be column headers.")

            # Use on_change callback to save edits immediately
            def save_csv_edits():
                st.session_state.csv_content = st.session_state.csv_editor_widget

            edited_content = st.text_area(
                "CSV Content",
                value=st.session_state.csv_content,
                height=200,
                key="csv_editor_widget",
                on_change=save_csv_edits,
                label_visibility="collapsed"
            )

            # Also save on every render (belt and suspenders)
            if edited_content != st.session_state.csv_content:
                st.session_state.csv_content = edited_content

            col1, col2, col3 = st.columns([1, 1, 3])
            with col1:
                if st.button("Reset to Original"):
                    st.session_state.csv_content = st.session_state.get('csv_raw_content', '')
                    st.rerun()
            with col2:
                if st.button("Clear File"):
                    st.session_state.csv_content = None
                    st.session_state.csv_raw_content = None
                    st.session_state.csv_filename = None
                    st.rerun()

            content = st.session_state.csv_content

            # Preview parsed data
            st.subheader("Preview")
            try:
                preview_df, columns = CSVImporter.preview_csv(content)
                st.dataframe(preview_df)
            except Exception as e:
                st.error(f"Error parsing CSV: {e}")
                st.stop()

            # Auto-detect columns
            detected = CSVImporter.detect_columns(columns)

            st.subheader("Column Mapping")
            st.caption("Map your CSV columns to the required fields")

            col1, col2 = st.columns(2)

            with col1:
                date_col = st.selectbox(
                    "Date Column",
                    options=columns,
                    index=columns.index(detected['date']) if detected['date'] else 0
                )

                desc_col = st.selectbox(
                    "Description Column",
                    options=columns,
                    index=columns.index(detected['description']) if detected['description'] else 0
                )

                # Source account column for multi-account mode
                if multi_account_mode:
                    source_account_col_selection = st.selectbox(
                        "Source Account Column",
                        options=["(none - assign all to one account)"] + columns,
                        index=0,
                        help="Column that identifies which account each transaction is from. Select 'none' if your CSV doesn't have this."
                    )
                    if source_account_col_selection == "(none - assign all to one account)":
                        source_account_col = None
                    else:
                        source_account_col = source_account_col_selection

            with col2:
                amount_type = st.radio(
                    "Amount Format",
                    options=["Single Amount Column", "Separate Debit/Credit Columns"]
                )

                if amount_type == "Single Amount Column":
                    amount_col = st.selectbox(
                        "Amount Column",
                        options=columns,
                        index=columns.index(detected['amount']) if detected['amount'] else 0
                    )
                    debit_col = None
                    credit_col = None
                else:
                    amount_col = None
                    debit_col = st.selectbox(
                        "Debit/Withdrawal Column",
                        options=['(none)'] + columns,
                        index=columns.index(detected['debit']) + 1 if detected['debit'] else 0
                    )
                    credit_col = st.selectbox(
                        "Credit/Deposit Column",
                        options=['(none)'] + columns,
                        index=columns.index(detected['credit']) + 1 if detected['credit'] else 0
                    )
                    if debit_col == '(none)':
                        debit_col = None
                    if credit_col == '(none)':
                        credit_col = None

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
                with col2:
                    sign_convention = st.selectbox(
                        "Sign Convention",
                        options=list(SIGN_CONVENTIONS.keys()),
                        format_func=lambda x: SIGN_CONVENTIONS[x],
                        help="How does this statement show expenses vs deposits?"
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
                st.caption("Map account names from your CSV to accounts in ProBooks")

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

            # Show summary stats
            import pandas as pd
            from io import StringIO
            try:
                summary_df = pd.read_csv(StringIO(content))
                total_rows = len(summary_df)

                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Total Rows", total_rows)
                with col2:
                    if date_col in summary_df.columns:
                        st.metric("Date Column", date_col)
                with col3:
                    if amount_col and amount_col in summary_df.columns:
                        try:
                            amounts = summary_df[amount_col].astype(str).str.replace(',', '').str.replace('$', '').str.replace('(', '-').str.replace(')', '')
                            amounts = pd.to_numeric(amounts, errors='coerce')
                            st.metric("Amount Range", f"${amounts.min():,.2f} to ${amounts.max():,.2f}")
                        except:
                            pass

                # Show first few and last few rows for confirmation
                st.caption("**First 3 rows:**")
                st.dataframe(summary_df.head(3), use_container_width=True, hide_index=True)

                if total_rows > 6:
                    st.caption("**Last 3 rows:**")
                    st.dataframe(summary_df.tail(3), use_container_width=True, hide_index=True)

            except Exception as e:
                st.error(f"Error reading CSV: {e}")

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

                        # Parse with source account column if in multi-account mode
                        transactions = CSVImporter.parse_csv(
                            content,
                            date_column=date_col,
                            description_column=desc_col,
                            amount_column=amount_col,
                            debit_column=debit_col,
                            credit_column=credit_col,
                            source_account_column=source_account_col if multi_account_mode else None
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
                                    # Both use the selected_bank and sign_convention
                                    if sign_convention == "credit_card":
                                        # Credit card: positive = expense, so flip to negative
                                        t['amount'] = -t['amount']
                                    elif sign_convention == "flip":
                                        # Just flip whatever sign it has
                                        t['amount'] = -t['amount']
                                    # "bank" convention: no change needed (negative = expense)

                                valid_transactions.append(t)

                            transactions = valid_transactions

                            if skipped > 0:
                                st.warning(f"Skipped {skipped} transactions from unmapped accounts.")

                            if not transactions:
                                st.error("No valid transactions after applying account mapping.")
                            else:
                                # First check learned patterns and duplicates
                                duplicate_count = 0
                                for t in transactions:
                                    # Set bank account for single-account mode or multi-account without source column
                                    if not multi_account_mode or (multi_account_mode and source_account_col is None):
                                        t['bank_account_id'] = selected_bank
                                    t['client_id'] = client_id

                                    # Check for potential duplicates
                                    duplicates = JournalEntry.find_potential_duplicates(
                                        client_id=client_id,
                                        entry_date=t['date'],
                                        amount=t['amount'],
                                        bank_account_id=t.get('bank_account_id')
                                    )
                                    if duplicates:
                                        t['is_duplicate'] = True
                                        t['duplicate_info'] = duplicates[0]  # Store first match info
                                        t['include'] = False  # Auto-deselect duplicates
                                        duplicate_count += 1
                                    else:
                                        t['is_duplicate'] = False

                                    # Check learned patterns
                                    match = PatternLearner.find_match(client_id, t['description'])
                                    if match:
                                        t['suggested_account_id'] = match['account_id']
                                        t['confidence'] = f"{match['confidence']:.0%}"
                                        t['reason'] = f"Learned pattern: {match['pattern']}"

                                # Then use Claude API for unmatched
                                unmatched = [t for t in transactions if 'suggested_account_id' not in t]
                                if unmatched and categorization_service.is_available():
                                    with st.spinner(f"Using AI to categorize {len(unmatched)} transactions..."):
                                        categorization_service.categorize_transactions(
                                            unmatched,
                                            expense_accounts + revenue_accounts
                                        )

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

elif selected_tab == "Review & Categorize":
    st.subheader("Review & Categorize Transactions")

    # Show the result of a partial "Create Journal Entries" run (some rows kept).
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
            if st.button("Import More Transactions", type="primary", use_container_width=True):
                st.session_state.import_complete = False
                st.session_state.import_complete_msg = None
                st.session_state.csv_content = None
                st.session_state.csv_filename = None
                st.session_state.import_active_tab = "Upload CSV"
                st.rerun()
        with col2:
            if st.button("Done - Go to Dashboard", type="secondary", use_container_width=True):
                st.session_state.import_complete = False
                st.session_state.import_complete_msg = None
                st.switch_page("pages/7_Dashboard.py")

        st.stop()

    if not st.session_state.transactions_to_review:
        st.info("No transactions to review. Upload a CSV file first.")
    else:
        transactions = st.session_state.transactions_to_review

        # Account options for dropdowns
        all_accounts = Account.get_all(client_id, active_only=True)
        account_options = {0: "-- Select Account --"}
        account_options.update({a.id: a.display_name() for a in all_accounts})

        # Ensure a stable per-transaction id (used to key all per-row widgets so
        # their state follows the transaction across re-sorts) and include flags.
        ensure_row_ids(transactions)
        for t in transactions:
            if 'include' not in t:
                t['include'] = True

        # Summary and bulk actions
        included_count = sum(1 for t in transactions if t.get('include', True))
        uncategorized_count = sum(1 for t in transactions if t.get('selected_account_id', 0) == 0 and 'suggested_account_id' not in t)
        duplicate_count = sum(1 for t in transactions if t.get('is_duplicate', False))

        col1, col2, col3, col4, col5 = st.columns([1, 1, 1, 1, 1])
        with col1:
            st.metric("Total", len(transactions))
        with col2:
            st.metric("Selected", included_count)
        with col3:
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
                        t['include'] = True
                        st.session_state[row_key("include", t)] = True
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
                if st.session_state.get(row_key("cat", t), 0) == 0
            ]

            if uncategorized:
                col1, col2 = st.columns([2, 2])
                with col1:
                    if st.button(f"🤖 Categorize {len(uncategorized)} Transactions with AI", type="secondary"):
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
                    st.caption("AI will suggest accounts based on transaction descriptions")
            else:
                st.success("All transactions have been categorized!")
        else:
            st.warning("AI categorization is not configured.")

            with st.expander("Set up AI Categorization", expanded=True):
                st.markdown("Enter your Anthropic API key to enable AI-powered transaction categorization.")
                api_key_input = st.text_input(
                    "Anthropic API Key",
                    type="password",
                    placeholder="sk-ant-...",
                    help="Get your API key at https://console.anthropic.com/"
                )

                if api_key_input:
                    if st.button("Save & Enable AI", type="primary"):
                        # Save to a local key file in the app's data directory
                        # (writable even when running as a packaged app; data/ is
                        # gitignored so the key is never committed).
                        from config import API_KEY_FILE
                        API_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
                        API_KEY_FILE.write_text(api_key_input.strip() + "\n")
                        st.success("API key saved! Please restart ProBooks for the change to take effect.")

                st.caption(
                    "Your API key is stored locally on this machine. When you run AI "
                    "categorization, transaction descriptions and amounts are sent to "
                    "Anthropic's API to suggest accounts."
                )

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
                options=list(account_options.keys()),
                format_func=lambda x: account_options[x],
                key="bulk_account_select"
            )
        with col2:
            if st.button("Apply to Selected", type="primary"):
                if bulk_account == 0:
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
                if bulk_account == 0:
                    st.warning("Please select an account first")
                else:
                    applied_count = 0
                    for t in transactions:
                        is_selected = st.session_state.get(row_key("include", t), True)
                        # Check session state for current category selection (0 = uncategorized)
                        current_category = st.session_state.get(row_key("cat", t), 0)
                        if is_selected and current_category == 0:
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

        # Show bulk result message if any
        if st.session_state.get('bulk_result'):
            st.info(st.session_state.bulk_result)
            st.session_state.bulk_result = None

        # Quick add new account for categorization
        with st.expander("+ Add New Category Account"):
            with st.form("quick_add_category_account", clear_on_submit=True):
                col_a, col_b = st.columns(2)
                with col_a:
                    cat_acct_number = st.text_input("Account Number", placeholder="e.g., 6100")
                    cat_acct_name = st.text_input("Account Name", placeholder="e.g., Office Supplies")
                with col_b:
                    cat_acct_type = st.selectbox(
                        "Type",
                        options=['Expense', 'Revenue', 'Asset', 'Liability', 'Equity'],
                        help="Most transaction categories are Expense or Revenue"
                    )
                    cat_acct_subtype = st.text_input("Subtype (optional)", placeholder="e.g., Operating")
                cat_acct_desc = st.text_input("Description (optional)", placeholder="Notes to identify this account")

                if st.form_submit_button("Add Account", type="primary"):
                    if cat_acct_number and cat_acct_name:
                        try:
                            new_cat_account = Account(
                                client_id=client_id,
                                account_number=cat_acct_number,
                                name=cat_acct_name,
                                type=cat_acct_type,
                                subtype=cat_acct_subtype if cat_acct_subtype else None,
                                description=cat_acct_desc if cat_acct_desc else None
                            )
                            new_cat_account.save()
                            st.success(f"Added: {cat_acct_number} - {cat_acct_name}")
                            st.rerun()
                        except Exception as e:
                            if "UNIQUE constraint" in str(e):
                                st.error("Account number already exists.")
                            else:
                                st.error(f"Error: {e}")
                    else:
                        st.error("Account number and name required.")

        # Review each transaction - header row
        st.divider()

        header_cols = st.columns([0.5, 0.9, 2.2, 1, 0.6, 1.5, 0.5])
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
        with header_cols[6]:
            if st.button("+ New", key="add_acct_btn", help="Add a new account"):
                st.session_state.show_add_account_form = True

        # Inline add account form (shown when button clicked)
        if st.session_state.get('show_add_account_form'):
            st.markdown("---")
            st.markdown("**Quick Add Account**")
            with st.form("inline_add_account", clear_on_submit=True):
                col_a, col_b, col_c, col_d = st.columns([1, 2, 1, 1])
                with col_a:
                    inline_acct_number = st.text_input("Number", placeholder="6100")
                with col_b:
                    inline_acct_name = st.text_input("Name", placeholder="Office Supplies")
                with col_c:
                    inline_acct_type = st.selectbox("Type", options=['Expense', 'Revenue', 'Asset', 'Liability', 'Equity'])
                with col_d:
                    inline_acct_subtype = st.text_input("Subtype", placeholder="Optional")

                col_submit, col_cancel, col_spacer = st.columns([1, 1, 3])
                with col_submit:
                    if st.form_submit_button("Add", type="primary"):
                        if inline_acct_number and inline_acct_name:
                            try:
                                new_inline_account = Account(
                                    client_id=client_id,
                                    account_number=inline_acct_number,
                                    name=inline_acct_name,
                                    type=inline_acct_type,
                                    subtype=inline_acct_subtype if inline_acct_subtype else None
                                )
                                new_inline_account.save()
                                st.session_state.show_add_account_form = False
                                st.success(f"Added: {inline_acct_number} - {inline_acct_name}")
                                st.rerun()
                            except Exception as e:
                                if "UNIQUE constraint" in str(e):
                                    st.error("Account number already exists.")
                                else:
                                    st.error(f"Error: {e}")
                        else:
                            st.error("Number and name required.")
                with col_cancel:
                    if st.form_submit_button("Cancel"):
                        st.session_state.show_add_account_form = False
                        st.rerun()
            st.markdown("---")

        st.divider()

        # Build transfer account options (only Asset/Liability accounts for transfers)
        transfer_accounts = [a for a in all_accounts if a.type in ('Asset', 'Liability')]
        transfer_options = {0: "-- Select Account --"}
        transfer_options.update({a.id: a.display_name() for a in transfer_accounts})

        for i, t in enumerate(transactions):
            col0, col1, col2, col3, col4, col5 = st.columns([0.5, 0.9, 2.2, 1, 0.6, 2])

            include_key = row_key("include", t)
            with col0:
                # Initialize session state for checkbox if not set
                if include_key not in st.session_state:
                    st.session_state[include_key] = t.get('include', True)

                include = st.checkbox(
                    "Select",
                    key=include_key,
                    label_visibility="collapsed"
                )
                # Sync back to transaction data
                transactions[i]['include'] = include

            with col1:
                st.text(str(t['date']))

            with col2:
                # Show duplicate warning if applicable
                if t.get('is_duplicate'):
                    st.markdown(f":orange[**POSSIBLE DUPLICATE**]")
                    dup_info = t.get('duplicate_info', {})
                    st.caption(f"Matches JE #{dup_info.get('entry_id', '?')} on {dup_info.get('entry_date', '?')}")

                st.text(t['description'][:35])
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
                    # Use suggested_account_id if available, otherwise 0
                    st.session_state[cat_key] = t.get('suggested_account_id', 0)

                if is_transfer:
                    # For transfers, show only bank/liability accounts
                    # Ensure the current value is valid for transfer options
                    if st.session_state[cat_key] not in transfer_options:
                        st.session_state[cat_key] = 0
                    selected = st.selectbox(
                        "Transfer To/From",
                        options=list(transfer_options.keys()),
                        format_func=lambda x: transfer_options[x],
                        key=cat_key,
                        label_visibility="collapsed"
                    )
                else:
                    # For regular transactions, show all accounts
                    # Ensure the current value is valid for account options
                    if st.session_state[cat_key] not in account_options:
                        st.session_state[cat_key] = 0
                    selected = st.selectbox(
                        "Account",
                        options=list(account_options.keys()),
                        format_func=lambda x: account_options[x],
                        key=cat_key,
                        label_visibility="collapsed"
                    )
                transactions[i]['selected_account_id'] = selected

        st.divider()

        col1, col2, col3 = st.columns(3)

        with col1:
            if st.button("Create Journal Entries", type="primary"):
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
                        )
                        created += 1

                    except Exception as e:
                        errors.append(f"{t['description'][:30]}: {e}")
                        failed.append(t)  # keep failed rows so they can be retried

                # Kept in the review list: included-but-uncategorized + failed rows.
                remaining = plan.uncategorized + failed
                uncategorized = len(plan.uncategorized)
                skipped = len(plan.excluded)

                if not remaining:
                    # Everything selected was posted; excluded rows acknowledged.
                    msg = f"Created {created} journal entries!"
                    if skipped > 0:
                        msg += f" ({skipped} excluded)"
                    st.session_state.transactions_to_review = []
                    st.session_state.import_complete = True
                    st.session_state.import_complete_msg = msg
                    st.rerun()
                else:
                    # Partial: keep unresolved rows and report exactly what happened.
                    st.session_state.transactions_to_review = remaining
                    parts = []
                    if created:
                        parts.append(f"created {created}")
                    if uncategorized:
                        parts.append(f"{uncategorized} still need a category")
                    if errors:
                        parts.append(f"{len(errors)} failed")
                    if skipped:
                        parts.append(f"{skipped} excluded")
                    st.session_state.post_result = {
                        'level': 'warning' if created else 'info',
                        'text': "Journal entries: " + ", ".join(parts)
                                + ". Transactions needing attention are kept below.",
                        'errors': errors[:3],
                    }
                    st.rerun()

        with col2:
            if st.button("Clear All"):
                st.session_state.transactions_to_review = []
                st.rerun()

        with col3:
            if not categorization_service.is_available():
                st.caption("⚠️ AI categorization unavailable - set ANTHROPIC_API_KEY")

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
                if st.button("🗑️", key=f"del_rule_{rule['id']}"):
                    PatternLearner.delete_rule(rule['id'])
                    st.rerun()
