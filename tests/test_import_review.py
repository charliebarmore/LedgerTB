"""Tests for the Import review-row identity/key helpers (H1 fix).

The bug: per-row widget state was keyed by list position, so re-sorting the
review list moved a widget's persisted value onto a different transaction. The
fix keys every per-row widget by a stable per-transaction id instead. These
tests lock the identity helper and demonstrate the sort-stability invariant.
"""

from utils.import_review import ensure_row_ids, row_key


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
