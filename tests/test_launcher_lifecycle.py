import os
import sys

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
