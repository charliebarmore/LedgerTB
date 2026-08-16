"""The maintenance lock as the real MCP tools actually exercise it.

The previous tests drove a generic get_cursor(commit=True) block, which every
mutation was assumed to pass through. It is not: the model layer commits on raw
connections, so posting an entry never entered the guard and the lost-write race
stayed reachable behind a green suite. These call the tools themselves.
"""

from datetime import date

import pytest

import mcp_server
from database import connection as dbconn
from utils import maintenance_lock


@pytest.fixture
def assistant(client_id, accounts, monkeypatch, tmp_path):
    """Run as the MCP process does, at an access level that may post."""
    import utils.books as books_mod

    monkeypatch.setattr(books_mod, "USER_DATA_DIR", tmp_path)
    monkeypatch.setattr(mcp_server, "_require_level", lambda *a, **k: None)
    dbconn.ASSISTANT_ACCESS_LEVEL = "post"
    yield
    dbconn.ASSISTANT_ACCESS_LEVEL = None


def _markers(book):
    return list(book.parent.glob(book.name + ".writer-*"))


def test_posting_an_entry_registers_a_writer(assistant, client_id, accounts):
    """Charlie's reproduction: instrument the writer, call the real tool, and
    the entry posted without the context ever being entered."""
    book = dbconn.DATABASE_PATH
    seen = []
    real = maintenance_lock.writer

    def spy(path):
        seen.append(str(path))
        return real(path)

    import mcp_server as srv
    srv_writer = maintenance_lock.writer
    maintenance_lock.writer = spy
    try:
        mcp_server.post_entry(
            client_id, date(2026, 3, 1).isoformat(), "assistant entry",
            [{"account_number": "1000", "debit": 25, "credit": 0},
             {"account_number": "4000", "debit": 0, "credit": 25}],
        )
    finally:
        maintenance_lock.writer = srv_writer

    assert seen, "posting an entry must declare itself as a write"


def test_maintenance_refuses_while_a_real_tool_is_mid_write(assistant, client_id, accounts):
    """The race the lock exists for, driven through a real mutation."""
    import threading

    book = dbconn.DATABASE_PATH
    inside = threading.Event()
    release = threading.Event()
    result = {}

    original = mcp_server.mcp_tools.post_entry

    def slow_post(*args, **kwargs):
        inside.set()
        release.wait(timeout=5)
        return original(*args, **kwargs)

    mcp_server.mcp_tools.post_entry = slow_post
    worker = threading.Thread(target=lambda: mcp_server.post_entry(
        client_id, date(2026, 3, 2).isoformat(), "slow entry",
        [{"account_number": "1000", "debit": 10, "credit": 0},
         {"account_number": "4000", "debit": 0, "credit": 10}],
    ))
    try:
        worker.start()
        inside.wait(timeout=5)
        assert len(_markers(book)) == 1, "the in-flight write must be visible"
        with pytest.raises(maintenance_lock.MaintenanceBusy):
            with maintenance_lock.hold(book):
                result["held"] = True
        assert "held" not in result
    finally:
        release.set()
        worker.join(timeout=5)
        mcp_server.mcp_tools.post_entry = original

    assert _markers(book) == [], "the claim must be released when the tool ends"


def test_every_mutating_tool_declares_itself(assistant):
    """A mutating tool added without the decorator would reopen the race."""
    import inspect

    undeclared = []
    for name, fn in vars(mcp_server).items():
        if not callable(fn) or name.startswith("_"):
            continue
        try:
            source = inspect.getsource(fn)
        except (OSError, TypeError):
            continue
        needs_write = ('_require_level("propose")' in source
                       or '_require_level("post")' in source)
        if needs_write and not getattr(fn, "_ledgertb_mutating", False):
            undeclared.append(name)

    assert undeclared == [], f"mutating tools missing the guard: {undeclared}"


def test_a_read_tool_takes_no_claim(assistant, client_id, accounts):
    book = dbconn.DATABASE_PATH

    mcp_server.list_clients()

    assert _markers(book) == [], "a read must not block maintenance"
