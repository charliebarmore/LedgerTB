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
from services.backups import active_book_id
from tests.conftest import post_entry
from utils.assistant_access import credential_names


def _numbers(client_id, accounts):
    cash = Account.get_by_id(accounts["cash"], client_id=client_id)
    revenue = Account.get_by_id(accounts["revenue"], client_id=client_id)
    return cash.account_number, revenue.account_number


BALANCED = lambda cash_no, rev_no: [
    {"account_number": cash_no, "debit": 55.00},
    {"account_number": rev_no, "credit": 55.00},
]


def _authorize_current_book(set_secret, level="propose"):
    names = credential_names(dbconn.DATABASE_PATH)
    set_secret(names.book_id, active_book_id())
    set_secret(names.key, dbconn.get_active_key())
    if level is not None:
        set_secret(names.level, level)
    return names


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

    names = _authorize_current_book(set_secret, level=None)

    # Missing level fails to least privilege.
    assert mcp_server._unlock_from_vault() is True
    assert dbconn.ASSISTANT_ACCESS_LEVEL == "read"

    set_secret(names.level, "post")
    assert mcp_server._unlock_from_vault() is True
    assert dbconn.ASSISTANT_ACCESS_LEVEL == "post"

    set_secret(names.level, "bogus")
    assert mcp_server._unlock_from_vault() is True
    assert dbconn.ASSISTANT_ACCESS_LEVEL == "read"  # unknown -> least privilege

    # The tool gate reads the vault too.
    set_secret(names.level, "read")
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
    names = _authorize_current_book(set_secret, level="read")
    book_id = active_book_id()

    assert mcp_server.list_clients()
    delete_secret(names.key)
    with pytest.raises(PermissionError, match="not enabled"):
        mcp_server.list_clients()
    assert dbconn.ASSISTANT_ACCESS_LEVEL is None
    assert dbconn.has_active_key() is False

    set_secret(names.level, "read")
    set_secret(names.book_id, book_id)
    set_secret(names.key, session_key)
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
    cash_no, rev_no = _numbers(client_id, accounts)
    names = _authorize_current_book(set_secret, level="propose")

    result = mcp_server.propose_entry(
        client_id, "2026-07-31", "Allowed draft", BALANCED(cash_no, rev_no)
    )
    assert result["status"] == "pending"

    set_secret(names.level, "read")
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
    _authorize_current_book(set_secret, level="post")

    assert mcp_server._unlock_from_vault() is True
    assert dbconn.ASSISTANT_ACCESS_LEVEL == "read"
    with pytest.raises(ValueError, match="current level is 'read'"):
        mcp_server._require_level("propose")


def test_authorization_cannot_follow_or_be_copied_to_another_book(
    client_id, monkeypatch, tmp_path
):
    import mcp_server
    from database import init_database
    from utils import books
    from utils.secure_store import set_secret

    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL",
                        dbconn.ASSISTANT_ACCESS_LEVEL)
    original_path = dbconn.DATABASE_PATH
    session_key = dbconn.get_active_key()
    active = [original_path]
    monkeypatch.setattr(books, "active_book", lambda: active[0])
    monkeypatch.setattr(books, "is_local_book", lambda path: True)

    names_a = _authorize_current_book(set_secret, level="propose")
    book_a_id = active_book_id()
    assert mcp_server._unlock_from_vault() is True

    book_b = tmp_path / "book-b.db"
    try:
        dbconn.ASSISTANT_ACCESS_LEVEL = None
        dbconn.DATABASE_PATH = book_b
        dbconn.set_active_key(session_key)
        init_database()
        book_b_id = active_book_id()
        assert book_b_id != book_a_id
        active[0] = book_b

        # Book A's permission does not follow the active-book switch.
        assert mcp_server._unlock_from_vault() is False
        assert dbconn.has_active_key() is False

        # Even copying the key/level to Book B's path scope is insufficient:
        # the encrypted book identity must match the explicit authorization.
        names_b = credential_names(book_b)
        set_secret(names_b.key, session_key)
        set_secret(names_b.level, "propose")
        set_secret(names_b.book_id, book_a_id)
        assert mcp_server._unlock_from_vault() is False
        assert dbconn.has_active_key() is False

        # Explicit consent for Book B makes only Book B available.
        set_secret(names_b.book_id, book_b_id)
        assert mcp_server._unlock_from_vault() is True
        assert dbconn.DATABASE_PATH == book_b
        assert dbconn.ASSISTANT_ACCESS_LEVEL == "propose"
        assert names_a != names_b
    finally:
        dbconn.ASSISTANT_ACCESS_LEVEL = None
        dbconn.DATABASE_PATH = original_path
        dbconn.set_active_key(session_key)


def test_assistant_process_actor_carries_ai_suffix(client_id, accounts, monkeypatch):
    """Assistant work must never display as the person's own in the feed."""
    from utils import actor as actor_mod

    monkeypatch.setattr(actor_mod, "_ASSISTANT", False)
    human = actor_mod.current_actor()
    assert not human.endswith("(AI)")

    actor_mod.mark_as_assistant()
    assert actor_mod.current_actor() == f"{human} (AI)"

    # Rows written under the assistant mark carry the suffix end to end.
    cash_no, rev_no = _numbers(client_id, accounts)
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", "propose")
    from services import mcp_tools as mt
    mt.propose_import(client_id, cash_no,
                      [{"date": "2026-07-03", "description": "AI STAGED",
                        "amount": -5.00}], "attribution test")
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", None)
    from models.transaction import ImportedTransaction
    staged = [t for t in ImportedTransaction.get_by_status(client_id, "Pending")
              if t.description == "AI STAGED"]
    with dbconn.get_cursor() as cursor:
        cursor.execute("SELECT created_by FROM imported_transactions WHERE id = ?",
                       (staged[0].id,))
        assert cursor.fetchone()["created_by"].endswith("(AI)")
