"""Helpers for the Import -> Review & Categorize flow.

Streamlit persists each widget's state by its string ``key`` across reruns. If
per-row widgets (category, include, transfer) are keyed by the row's *position*
in the list, re-sorting the review list desynchronizes that state: the widget at
position ``i`` keeps its value while position ``i`` now holds a different
transaction, so a category/include flag silently lands on the wrong transaction.

Keying by a stable per-transaction id instead makes each widget's state follow
its transaction across any re-sort. These helpers are the single source of truth
for that id and the key format, so the two can never drift apart.
"""

import uuid
from collections import namedtuple


# Partition of review rows for a "Create Journal Entries" run.
#   to_post       - included rows that have a chosen account (ready to post)
#   uncategorized - included rows with no account (KEPT, never silently dropped)
#   excluded      - rows the user deselected (an explicit, acknowledged skip)
RowPlan = namedtuple("RowPlan", ["to_post", "uncategorized", "excluded"])


def classify_review_rows(transactions, is_included, get_account_id):
    """Partition review rows ahead of posting.

    ``is_included(t) -> bool`` and ``get_account_id(t) -> int`` (0 == no account)
    are supplied by the caller so this stays free of Streamlit/session state and
    is unit-testable. Returns a :class:`RowPlan`. The key guarantee: an included
    row with no account lands in ``uncategorized`` (to be kept and surfaced to the
    user), never silently discarded.
    """
    to_post, uncategorized, excluded = [], [], []
    for t in transactions:
        if not is_included(t):
            excluded.append(t)
        elif get_account_id(t) == 0:
            uncategorized.append(t)
        else:
            to_post.append(t)
    return RowPlan(to_post, uncategorized, excluded)


def ensure_row_ids(transactions):
    """Assign a stable unique ``uid`` to each transaction dict that lacks one.

    Idempotent: transactions that already have a ``uid`` keep it, so ids stay
    stable across the many Streamlit reruns of the review screen.
    """
    for t in transactions:
        if "uid" not in t:
            t["uid"] = uuid.uuid4().hex
    return transactions


def row_key(prefix, transaction):
    """Stable Streamlit widget key for a per-row control.

    e.g. ``row_key("cat", t) -> "cat_<uid>"``. Callers must use this rather than
    building the key from a list index, so re-sorting the list cannot move a
    widget's persisted state onto a different transaction.
    """
    return f"{prefix}_{transaction['uid']}"


_CLIENT_IMPORT_STATE_KEYS = {
    "imported_data",
    "column_mapping",
    "transactions_to_review",
    "document_extraction",
    "document_transactions",
    "document_skipped",
    "document_amounts_normalized",
    "document_identity",
    "document_name",
    "document_bytes",
    "document_text_editor",
    "csv_content",
    "csv_raw_content",
    "csv_filename",
    "csv_source_id",
    "csv_editor_widget",
    "csv_coa_override",
    "csv_multi_account_mode",
    "csv_bank_account",
    "csv_import_profile_id",
    "csv_sign_convention",
    "csv_date_column",
    "csv_description_column",
    "csv_source_account_column",
    "csv_amount_format",
    "csv_amount_column",
    "csv_debit_column",
    "csv_credit_column",
    "csv_import_profile_name",
    "csv_confirm",
    "_csv_profile_choice_context",
    "_csv_mapping_context",
    "_csv_profile_name_context",
    "account_mapping",
    "source_account_col",
    "document_bank_account",
    "document_sign_convention",
    "statement_ai_consent",
    "document_transaction_editor",
    "document_validation_confirmed",
    "post_result",
    "import_complete",
    "import_complete_msg",
    "confirm_dismiss_staged",
    "ai_categorization_result",
    "bulk_result",
    "bulk_account_select",
    "sort_by",
    "sort_order",
    "history_batch",
    "confirm_profile_delete_id",
    "quick_add_account_msg",
    "quick_add_account_error",
    "multi_assign_sign_convention",
    "quick_add_bank_account_type",
    # Dependency trackers written by utils.ui.apply_default_on_change. Left
    # behind, they stop the new client's derived default from being applied.
    "_csv_sign_convention_depends_on",
    "_multi_assign_sign_convention_depends_on",
    "_document_sign_convention_depends_on",
}

_CLIENT_IMPORT_STATE_PREFIXES = (
    "cat_",
    "include_",
    "xfer_",
    "duplicate_override_",
    "duplicate_reason_",
    "newacct_",
    "map_",
    "batch_reversal_",
    "reverse_import_batch_",
    "verify_",
    "statement_document_upload_",
    "_include_",
)

# Widgets that stay mounted on the Upload CSV view across a client switch.
# Popping a keyed widget's value does not reach the browser, which re-sends the
# old value on the next run; assigning a value before the widget renders does.
# Everything else on the page is either rotated by nonce or unmounted by the
# tab reset below, so Streamlit drops its state.
_CLIENT_IMPORT_WIDGET_RESETS = {
    "csv_multi_account_mode": False,
}


def scope_import_state_to_client(session_state, client_id, book=None):
    """Discard volatile import work when the selected client changes.

    Uploaded files and review rows live only in Streamlit session state until
    they are staged or posted.  Keeping that state under a different selected
    client can make the review screen appear not to switch clients and, more
    importantly, risks presenting one client's rows beside another client's
    accounts.  Durable pending rows are not affected; the review page reloads
    those from the database for the newly selected client.

    ``book`` is the open database.  Client ids restart at 1 in every book, so
    switching books can land on the same client id; the book is part of the
    identity so that switch is treated as a change too.

    Returns ``True`` when a client change was handled.
    """
    marker = "_import_state_client_id"
    identity = (str(book) if book is not None else None, client_id)
    previous = session_state.get(marker)
    if previous is None:
        session_state[marker] = identity
        return False
    if previous == identity:
        return False

    for key in list(session_state):
        if (
            key in _CLIENT_IMPORT_STATE_KEYS
            or key.startswith(_CLIENT_IMPORT_STATE_PREFIXES)
        ):
            session_state.pop(key, None)

    # Rotate the uploader identities so Streamlit cannot hand the prior
    # client's uploaded file back to the new client on the next render. A new
    # key is the only reset the browser honors for a file uploader.
    session_state["csv_uploader_nonce"] = (
        session_state.get("csv_uploader_nonce", 0) + 1
    )
    session_state["statement_uploader_nonce"] = (
        session_state.get("statement_uploader_nonce", 0) + 1
    )
    session_state.update(_CLIENT_IMPORT_WIDGET_RESETS)
    session_state["import_active_tab"] = "Upload CSV"
    session_state[marker] = identity
    return True
