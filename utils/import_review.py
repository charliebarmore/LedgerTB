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
