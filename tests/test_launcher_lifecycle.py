import os
import sys
import threading

import pytest

import run_ledgertb


def test_command_line_selfcheck_cannot_launch_a_second_app(monkeypatch):
    """A natural diagnostic invocation must not fall through to GUI mode."""
    calls = []
    monkeypatch.delenv("LEDGERTB_MODE", raising=False)
    monkeypatch.delenv("PROBOOKS_MODE", raising=False)
    monkeypatch.setattr(sys, "argv", ["LedgerTB", "--selfcheck"])
    monkeypatch.setattr(run_ledgertb, "_selfcheck", lambda: calls.append(1) or 7)

    assert run_ledgertb.main() == 7
    assert calls == [1]


@pytest.mark.skipif(os.name != "posix", reason="POSIX uses parent re-parenting")
def test_parent_liveness_detects_the_current_parent():
    assert run_ledgertb._parent_is_alive(os.getppid())
    assert not run_ledgertb._parent_is_alive(999_999_999)


def test_parent_watch_releases_the_book_before_exiting(monkeypatch):
    from database import connection as dbconn
    from utils import book_lock

    alive = iter([True, False])
    sleeps = []
    releases = []
    monkeypatch.setattr(run_ledgertb, "_parent_is_alive", lambda _pid: next(alive))
    monkeypatch.setattr(run_ledgertb.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(book_lock, "release", lambda book: releases.append(book))
    monkeypatch.setattr(
        run_ledgertb.os,
        "_exit",
        lambda code: (_ for _ in ()).throw(SystemExit(code)),
    )

    with pytest.raises(SystemExit) as stopped:
        run_ledgertb._exit_when_parent_dies(1234)

    assert stopped.value.code == 0
    assert sleeps == [1]
    assert releases == [dbconn.DATABASE_PATH]


def test_stop_reaps_child_after_hard_stop_fallback(monkeypatch):
    class StubbornProcess:
        def __init__(self):
            self.calls = []

        def poll(self):
            return None

        def terminate(self):
            self.calls.append("terminate")

        def wait(self, timeout):
            self.calls.append(("wait", timeout))
            if timeout == 5:
                raise TimeoutError
            return 0

        def kill(self):
            self.calls.append("kill")

    monkeypatch.setattr(run_ledgertb.os, "name", "nt")
    proc = StubbornProcess()
    run_ledgertb._stop(proc)

    assert proc.calls == ["terminate", ("wait", 5), "kill", ("wait", 2)]


def test_window_close_watchdog_does_not_exit_after_gui_returns(monkeypatch):
    closed = threading.Event()
    returned = threading.Event()
    closed.set()
    returned.set()
    exits = []
    monkeypatch.setattr(run_ledgertb.os, "_exit", exits.append)

    run_ledgertb._force_exit_if_window_loop_stalls(closed, returned, timeout=0)

    assert exits == []


def test_window_close_watchdog_ends_stalled_desktop_parent(monkeypatch):
    closed = threading.Event()
    returned = threading.Event()
    closed.set()
    exits = []
    monkeypatch.setattr(run_ledgertb.os, "_exit", exits.append)

    run_ledgertb._force_exit_if_window_loop_stalls(closed, returned, timeout=0)

    assert exits == [0]


def test_windows_native_close_event_stops_the_server(monkeypatch):
    class EventHook:
        handler = None

        def __iadd__(self, handler):
            self.handler = handler
            return self

    class Events:
        closed = EventHook()

    class Window:
        events = Events()

    monkeypatch.setattr(run_ledgertb.os, "name", "nt")
    gui_returned = threading.Event()
    gui_returned.set()
    stops = []

    run_ledgertb._register_windows_close_handler(
        Window(), lambda: stops.append(1), gui_returned
    )
    Window.events.closed.handler()

    assert stops == [1]
