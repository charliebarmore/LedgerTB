import threading
import time

import pytest

import mcp_server
from database import connection as dbconn


def test_tool_invocations_are_serialized_end_to_end(monkeypatch):
    active = threading.Event()
    overlaps = []
    observed = []
    state = [("book-a.db", "read"), ("book-b.db", "post")]
    state_lock = threading.Lock()

    def refresh():
        with state_lock:
            path, level = state.pop(0)
        monkeypatch.setattr(dbconn, "DATABASE_PATH", path)
        monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", level)
        return level

    def slow_tool():
        if active.is_set():
            overlaps.append(True)
        active.set()
        before = (dbconn.DATABASE_PATH, dbconn.ASSISTANT_ACCESS_LEVEL)
        time.sleep(0.002)
        after = (dbconn.DATABASE_PATH, dbconn.ASSISTANT_ACCESS_LEVEL)
        observed.append((before, after))
        active.clear()
        return []

    monkeypatch.setattr(mcp_server, "_refresh_access", refresh)
    monkeypatch.setattr(mcp_server.mcp_tools, "list_clients", slow_tool)

    for _ in range(50):
        state[:] = [("book-a.db", "read"), ("book-b.db", "post")]
        observed.clear()
        threads = [threading.Thread(target=mcp_server.list_clients)
                   for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
        assert all(not thread.is_alive() for thread in threads)
        assert observed == [(("book-a.db", "read"),) * 2,
                            (("book-b.db", "post"),) * 2]

    assert overlaps == []


def test_refresh_precedes_per_book_lock_and_state_reads(monkeypatch):
    events = []

    def refresh():
        events.append("refresh")
        monkeypatch.setattr(dbconn, "DATABASE_PATH", "book-b.db")
        monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", "post")
        return "post"

    class Writer:
        def __enter__(self):
            events.append(("lock", dbconn.DATABASE_PATH))

        def __exit__(self, *exc):
            pass

    monkeypatch.setattr(mcp_server, "_refresh_access", refresh)
    monkeypatch.setattr("utils.maintenance_lock.writer", lambda path: Writer())
    monkeypatch.setattr(
        mcp_server.mcp_tools, "post_entry",
        lambda *args: events.append(
            ("body", dbconn.DATABASE_PATH, dbconn.ASSISTANT_ACCESS_LEVEL)
        ) or {},
    )

    mcp_server.post_entry(1, "2026-01-01", "test", [])

    assert events == ["refresh", ("lock", "book-b.db"),
                      ("body", "book-b.db", "post")]


def test_raising_tool_releases_lock_and_resets_active(monkeypatch):
    calls = []

    monkeypatch.setattr(
        mcp_server, "_refresh_access",
        lambda: dbconn.ASSISTANT_ACCESS_LEVEL,
    )
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", "read")
    monkeypatch.setattr(
        mcp_server.mcp_tools, "list_clients",
        lambda: (_ for _ in ()).throw(RuntimeError("tool failed")),
    )

    with pytest.raises(RuntimeError, match="tool failed"):
        mcp_server.list_clients()

    assert not getattr(mcp_server._tool_state, "active", False)

    monkeypatch.setattr(
        mcp_server.mcp_tools, "list_clients",
        lambda: calls.append("next invocation") or [],
    )
    worker = threading.Thread(target=mcp_server.list_clients)
    worker.start()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert calls == ["next invocation"]


def test_sequential_tool_call_honors_vault_level_change(monkeypatch):
    levels = iter(("post", None))

    def refresh():
        level = next(levels)
        monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", level)
        if level is None:
            raise PermissionError("assistant access disabled")
        return level

    monkeypatch.setattr(mcp_server, "_refresh_access", refresh)
    monkeypatch.setattr(mcp_server.mcp_tools, "list_clients", lambda: [])

    assert mcp_server.list_clients() == []
    with pytest.raises(PermissionError, match="assistant access disabled"):
        mcp_server.list_clients()
