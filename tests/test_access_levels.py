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
    monkeypatch.setattr(books, "is_local_book", lambda path: True)

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
    assert dbconn.ASSISTANT_ACCESS_LEVEL == "read"  # unknown -> least privilege

    # The tool gate reads the vault too.
    set_secret(mcp_server.MCP_LEVEL_SECRET, "read")
    with pytest.raises(ValueError, match="access level"):
        mcp_server._require_level("propose")


def test_running_server_honors_disable_and_reenable(client_id, monkeypatch):
    import mcp_server
    from utils import books
    from utils.secure_store import delete_secret, set_secret

    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL",
                        dbconn.ASSISTANT_ACCESS_LEVEL)
    test_db = dbconn.DATABASE_PATH
    monkeypatch.setattr(books, "active_book", lambda: test_db)
    monkeypatch.setattr(books, "is_local_book", lambda path: True)
    session_key = dbconn.get_active_key()
    set_secret(mcp_server.MCP_LEVEL_SECRET, "read")
    set_secret(mcp_server.MCP_KEY_SECRET, session_key)

    assert mcp_server.list_clients()
    delete_secret(mcp_server.MCP_KEY_SECRET)
    with pytest.raises(PermissionError, match="disabled"):
        mcp_server.list_clients()
    assert dbconn.ASSISTANT_ACCESS_LEVEL is None
    assert dbconn.has_active_key() is False

    set_secret(mcp_server.MCP_LEVEL_SECRET, "read")
    set_secret(mcp_server.MCP_KEY_SECRET, session_key)
    assert mcp_server.list_clients()
    assert dbconn.ASSISTANT_ACCESS_LEVEL == "read"


def test_running_server_honors_level_changes(client_id, accounts, monkeypatch):
    import mcp_server
    from utils import books
    from utils.secure_store import set_secret

    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL",
                        dbconn.ASSISTANT_ACCESS_LEVEL)
    test_db = dbconn.DATABASE_PATH
    monkeypatch.setattr(books, "active_book", lambda: test_db)
    monkeypatch.setattr(books, "is_local_book", lambda path: True)
    session_key = dbconn.get_active_key()
    cash_no, rev_no = _numbers(client_id, accounts)
    set_secret(mcp_server.MCP_KEY_SECRET, session_key)
    set_secret(mcp_server.MCP_LEVEL_SECRET, "propose")

    result = mcp_server.propose_entry(
        client_id, "2026-07-31", "Allowed draft", BALANCED(cash_no, rev_no)
    )
    assert result["status"] == "pending"

    set_secret(mcp_server.MCP_LEVEL_SECRET, "read")
    with pytest.raises(ValueError, match="access level 'propose'"):
        mcp_server.propose_entry(
            client_id, "2026-07-31", "Denied draft", BALANCED(cash_no, rev_no)
        )
    assert dbconn.ASSISTANT_ACCESS_LEVEL == "read"


def test_assistant_cannot_update_a_filed_draft(client_id, accounts, monkeypatch):
    cash_no, rev_no = _numbers(client_id, accounts)
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", "propose")
    result = mcp_tools.propose_entry(
        client_id, "2026-07-31", "Original", BALANCED(cash_no, rev_no)
    )

    with pytest.raises(Exception):
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                "UPDATE draft_entries SET description = 'Tampered' WHERE id = ?",
                (result["draft_id"],),
            )


def test_external_book_caps_assistant_at_read(client_id, monkeypatch):
    import mcp_server
    from utils import books
    from utils.secure_store import set_secret

    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL",
                        dbconn.ASSISTANT_ACCESS_LEVEL)
    test_db = dbconn.DATABASE_PATH
    monkeypatch.setattr(books, "active_book", lambda: test_db)
    monkeypatch.setattr(books, "is_local_book", lambda path: False)
    set_secret(mcp_server.MCP_KEY_SECRET, dbconn.get_active_key())
    set_secret(mcp_server.MCP_LEVEL_SECRET, "post")

    assert mcp_server._unlock_from_vault() is True
    assert dbconn.ASSISTANT_ACCESS_LEVEL == "read"
    with pytest.raises(ValueError, match="current level is 'read'"):
        mcp_server._require_level("propose")
