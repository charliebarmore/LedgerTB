"""The assistant access dial: what each level can and cannot do.

The permission matrix is the product promise — read stops at reading,
propose stops at the inboxes, and post is APPEND-ONLY: even at the highest
level the engine refuses any edit or delete, ledger included.
"""
from datetime import date

import pytest

from database import connection as dbconn
from database.connection import get_cursor
from models.account import Account
from models.journal_entry import JournalEntry
from services import mcp_tools
from tests.conftest import post_entry


def _numbers(client_id, accounts):
    cash = Account.get_by_id(accounts["cash"], client_id=client_id)
    revenue = Account.get_by_id(accounts["revenue"], client_id=client_id)
    return cash.account_number, revenue.account_number


BALANCED = lambda cash_no, rev_no: [
    {"account_number": cash_no, "debit": 55.00},
    {"account_number": rev_no, "credit": 55.00},
]


def test_read_level_reads_but_proposes_nothing(client_id, accounts, monkeypatch):
    cash_no, rev_no = _numbers(client_id, accounts)
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", "read")

    assert mcp_tools.trial_balance(client_id)["balanced"] is True
    with pytest.raises(Exception):  # inbox INSERT denied by the engine
        mcp_tools.propose_entry(client_id, "2026-07-31", "x",
                                BALANCED(cash_no, rev_no))


def test_propose_level_cannot_post(client_id, accounts, monkeypatch):
    cash_no, rev_no = _numbers(client_id, accounts)
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", "propose")

    assert mcp_tools.propose_entry(
        client_id, "2026-07-31", "draft", BALANCED(cash_no, rev_no)
    )["status"] == "pending"
    with pytest.raises(Exception):  # journal INSERT denied by the engine
        mcp_tools.post_entry(client_id, "2026-07-31", "direct",
                             BALANCED(cash_no, rev_no))


def test_post_level_is_append_only(client_id, accounts, monkeypatch):
    cash_no, rev_no = _numbers(client_id, accounts)
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", "post")

    result = mcp_tools.post_entry(client_id, "2026-07-31", "Assistant posting",
                                  BALANCED(cash_no, rev_no))
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", None)
    entry = JournalEntry.get_by_id(result["entry_id"], client_id=client_id)
    assert entry is not None
    assert entry.source_reference == "Posted by assistant (MCP)"
    assert sum(l.debit for l in entry.lines) == pytest.approx(55.00)

    # Append-only: at the same level, edits and deletes die at the engine.
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", "post")
    for stmt in (
        "UPDATE journal_entries SET description = 'tampered' WHERE id = ?",
        "DELETE FROM journal_entries WHERE id = ?",
        "UPDATE journal_entry_lines SET debit = 999999 WHERE journal_entry_id = ?",
    ):
        with pytest.raises(Exception):
            with get_cursor(commit=True) as cursor:
                cursor.execute(stmt, (result["entry_id"],))

    # Unbalanced never posts — model validation is still in the path.
    with pytest.raises(Exception):
        mcp_tools.post_entry(client_id, "2026-07-31", "bad",
                             [{"account_number": cash_no, "debit": 10.00},
                              {"account_number": rev_no, "credit": 9.00}])


def test_server_reads_level_from_vault(client_id, monkeypatch):
    import mcp_server
    from utils import books
    from utils.secure_store import set_secret

    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL",
                        dbconn.ASSISTANT_ACCESS_LEVEL)
    monkeypatch.setattr(dbconn, "READ_ONLY", dbconn.READ_ONLY)
    test_db = dbconn.DATABASE_PATH
    monkeypatch.setattr(books, "active_book", lambda: test_db)

    session_key = dbconn.get_active_key()
    set_secret(mcp_server.MCP_KEY_SECRET, session_key)

    # No stored level -> the pre-levels default, propose.
    assert mcp_server._unlock_from_vault() is True
    assert dbconn.ASSISTANT_ACCESS_LEVEL == "propose"

    set_secret(mcp_server.MCP_LEVEL_SECRET, "post")
    assert mcp_server._unlock_from_vault() is True
    assert dbconn.ASSISTANT_ACCESS_LEVEL == "post"

    set_secret(mcp_server.MCP_LEVEL_SECRET, "bogus")
    assert mcp_server._unlock_from_vault() is True
    assert dbconn.ASSISTANT_ACCESS_LEVEL == "propose"  # unknown -> safe default

    # The tool gate reads the vault too.
    set_secret(mcp_server.MCP_LEVEL_SECRET, "read")
    with pytest.raises(ValueError, match="access level"):
        mcp_server._require_level("propose")
