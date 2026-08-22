"""Tests for the Import review-row identity/key helpers (H1 fix).

The bug: per-row widget state was keyed by list position, so re-sorting the
review list moved a widget's persisted value onto a different transaction. The
fix keys every per-row widget by a stable per-transaction id instead. These
tests lock the identity helper and demonstrate the sort-stability invariant.
"""

from utils.import_review import (
    classify_review_rows,
    ensure_row_ids,
    row_key,
    scope_import_state_to_client,
)


def test_ensure_row_ids_assigns_unique_ids():
    txns = [{"description": "A"}, {"description": "B"}, {"description": "C"}]
    ensure_row_ids(txns)
    uids = [t["uid"] for t in txns]
    assert all(uids)
    assert len(set(uids)) == 3  # unique


def test_ensure_row_ids_is_idempotent():
    txns = [{"description": "A"}, {"description": "B"}]
    ensure_row_ids(txns)
    first = [t["uid"] for t in txns]
    ensure_row_ids(txns)  # second pass (mimics a Streamlit rerun)
    assert [t["uid"] for t in txns] == first  # existing ids preserved


def test_ensure_row_ids_preserves_preexisting_uid():
    txns = [{"uid": "fixed", "description": "A"}, {"description": "B"}]
    ensure_row_ids(txns)
    assert txns[0]["uid"] == "fixed"
    assert txns[1]["uid"] and txns[1]["uid"] != "fixed"


def test_row_key_is_prefixed_and_stable():
    t = {"uid": "abc123"}
    assert row_key("cat", t) == "cat_abc123"
    assert row_key("include", t) == "include_abc123"
    # Same transaction -> same key regardless of anything else in the list.
    assert row_key("cat", t) == row_key("cat", {"uid": "abc123"})


def test_client_switch_discards_only_volatile_import_state():
    state = {
        "_import_state_client_id": ("firm.db", 1),
        "transactions_to_review": [{"uid": "row-a", "client_id": 1}],
        "cat_row-a": 6000,
        "include_row-a": True,
        "_include_row-a_depends_on": True,
        "_csv_sign_convention_depends_on": (3, None, 0),
        "csv_multi_account_mode": True,
        "multi_assign_sign_convention": "credit",
        "csv_content": "Date,Description,Amount",
        "document_bytes": b"old-client-statement",
        "statement_document_upload_2": object(),
        "csv_uploader_nonce": 4,
        "statement_uploader_nonce": 2,
        "unrelated_setting": "keep",
    }

    assert scope_import_state_to_client(state, 2, book="firm.db") is True

    assert state["_import_state_client_id"] == ("firm.db", 2)
    assert state["import_active_tab"] == "Upload CSV"
    # Uploaders get a new key: the only reset a browser honors for a file.
    assert state["csv_uploader_nonce"] == 5
    assert state["statement_uploader_nonce"] == 3
    # Still-mounted widgets are reset by assignment, not by pop: a popped
    # widget value never reaches the browser, an assigned one does.
    assert state["csv_multi_account_mode"] is False
    assert state["unrelated_setting"] == "keep"
    for gone in (
        "transactions_to_review", "cat_row-a", "include_row-a",
        "_include_row-a_depends_on", "_csv_sign_convention_depends_on",
        "multi_assign_sign_convention", "csv_content", "document_bytes",
        "statement_document_upload_2",
    ):
        assert gone not in state, gone


def test_same_client_keeps_in_progress_import_state():
    state = {
        "_import_state_client_id": ("firm.db", 7),
        "transactions_to_review": [{"uid": "row-a", "client_id": 7}],
        "csv_content": "still working",
    }

    assert scope_import_state_to_client(state, 7, book="firm.db") is False
    assert state["transactions_to_review"][0]["client_id"] == 7
    assert state["csv_content"] == "still working"


def test_book_switch_with_same_client_id_is_a_client_change():
    """Client ids restart at 1 in every book, so the same id in another book
    is a different client and its review work must not carry over."""
    state = {
        "_import_state_client_id": ("training.db", 1),
        "transactions_to_review": [{"uid": "row-a", "client_id": 1}],
        "csv_content": "training rows",
    }

    assert scope_import_state_to_client(state, 1, book="firm.db") is True
    assert state["_import_state_client_id"] == ("firm.db", 1)
    assert "transactions_to_review" not in state
    assert "csv_content" not in state


def test_first_visit_records_identity_without_clearing():
    state = {"csv_content": "fresh"}
    assert scope_import_state_to_client(state, 1, book="firm.db") is False
    assert state["_import_state_client_id"] == ("firm.db", 1)
    assert state["csv_content"] == "fresh"


def test_widget_state_follows_transaction_across_sort():
    """Invariant behind the fix: a per-row value stored under the transaction's
    uid-key is still read back for THAT transaction after the list is re-sorted."""
    txns = [
        {"description": "STAPLES", "amount": -10.0},
        {"description": "CLIENT PMT", "amount": 500.0},
        {"description": "AWS", "amount": -99.0},
    ]
    ensure_row_ids(txns)

    # User assigns a category to each row -> stored in "session_state" by uid-key.
    session_state = {row_key("cat", t): f"acct-for-{t['description']}" for t in txns}

    # Re-sort by amount (ascending) -- the exact operation that used to desync state.
    txns.sort(key=lambda t: t["amount"])

    # Every transaction still reads back its own category.
    for t in txns:
        assert session_state[row_key("cat", t)] == f"acct-for-{t['description']}"


def test_position_based_keys_would_desync_on_sort():
    """Contrast: the OLD index-based scheme reads the wrong value after a sort.
    This documents the bug the uid-keying fixes (and guards against regressing
    to position-based keys)."""
    txns = [
        {"description": "STAPLES", "amount": -10.0},
        {"description": "CLIENT PMT", "amount": 500.0},
        {"description": "AWS", "amount": -99.0},
    ]
    # Old scheme: key by position.
    session_state = {f"cat_{i}": f"acct-for-{t['description']}" for i, t in enumerate(txns)}

    txns.sort(key=lambda t: t["amount"])

    # After sorting, at least one row reads back a DIFFERENT transaction's value.
    mismatches = [
        t["description"]
        for i, t in enumerate(txns)
        if session_state[f"cat_{i}"] != f"acct-for-{t['description']}"
    ]
    assert mismatches  # the desync the fix eliminates


# ---- classify_review_rows (H3: never silently drop uncategorized rows) ----


def _rows():
    return [
        {"uid": "a", "acct": 6000, "inc": True},   # ready to post
        {"uid": "b", "acct": 0,    "inc": True},   # included but uncategorized
        {"uid": "c", "acct": 4000, "inc": False},  # deselected
        {"uid": "d", "acct": 0,    "inc": False},  # deselected AND uncategorized
    ]


def _plan(rows):
    return classify_review_rows(
        rows,
        is_included=lambda t: t["inc"],
        get_account_id=lambda t: t["acct"],
    )


def test_classify_partitions_rows():
    plan = _plan(_rows())
    assert [t["uid"] for t in plan.to_post] == ["a"]
    assert [t["uid"] for t in plan.uncategorized] == ["b"]
    assert [t["uid"] for t in plan.excluded] == ["c", "d"]


def test_classify_never_loses_a_row():
    rows = _rows()
    plan = _plan(rows)
    accounted = len(plan.to_post) + len(plan.uncategorized) + len(plan.excluded)
    assert accounted == len(rows)  # every row is classified, none dropped


def test_included_uncategorized_row_is_kept_not_dropped():
    """The H3 bug: an included row with no account used to be silently skipped.
    It must now land in `uncategorized` so the page keeps it in the review list."""
    plan = _plan([{"uid": "x", "acct": 0, "inc": True}])
    assert not plan.to_post
    assert [t["uid"] for t in plan.uncategorized] == ["x"]


def test_all_ready_leaves_nothing_to_keep():
    plan = _plan([{"uid": "a", "acct": 6000, "inc": True},
                  {"uid": "b", "acct": 4000, "inc": True}])
    assert len(plan.to_post) == 2
    assert not plan.uncategorized and not plan.excluded


def test_parking_accounts_are_recognized():
    """"Ask My Accountant" is filed, not decided — the review screen flags it
    so a parked row can't read as categorized."""
    from utils.ui import is_parking_account

    assert is_parking_account("6900 - Ask My Accountant (uncategorized)")
    assert is_parking_account("9999 Suspense")
    assert is_parking_account("Uncategorized Expense")
    assert not is_parking_account("6400 - Office Supplies")
    assert not is_parking_account("")
    assert not is_parking_account(None)
