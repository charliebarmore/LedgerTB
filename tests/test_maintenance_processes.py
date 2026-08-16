"""Real process-boundary tests for whole-book maintenance coordination.

These launch a standalone Python child the same way on macOS and Windows.
Thread tests cannot prove that another LedgerTB process, or the MCP process,
sees the same operating-system lock.
"""

from pathlib import Path
import subprocess
import sys

import pytest

from database import connection as dbconn
from utils import maintenance_lock


_WORKER = Path(__file__).parent / "helpers" / "maintenance_lock_worker.py"


def _spawn(mode, book):
    process = subprocess.Popen(
        [sys.executable, str(_WORKER), mode, str(book)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ready = process.stdout.readline().strip()
    if ready != "ready":
        _, errors = process.communicate(timeout=10)
        pytest.fail(
            f"child never acquired its {mode} lock "
            f"(exit {process.returncode}): {errors}"
        )
    return process


def _finish(process, release=False):
    if release:
        process.stdin.write("release\n")
        process.stdin.flush()
    try:
        _, errors = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.communicate(timeout=5)
        pytest.fail("child process did not stop")
    assert process.returncode == 0, errors


def test_another_process_shared_lock_blocks_maintenance(tmp_path):
    book = tmp_path / "shared.ledgertb"
    process = _spawn("shared", book)
    try:
        with pytest.raises(maintenance_lock.MaintenanceBusy):
            with maintenance_lock.hold(book):
                pass
    finally:
        _finish(process, release=True)

    with maintenance_lock.hold(book):
        pass


def test_another_process_exclusive_lock_blocks_connections(tmp_path):
    book = tmp_path / "exclusive.ledgertb"
    process = _spawn("exclusive", book)
    try:
        with pytest.raises(maintenance_lock.MaintenanceBusy):
            maintenance_lock.acquire_connection(book)
    finally:
        _finish(process, release=True)

    lease = maintenance_lock.acquire_connection(book)
    maintenance_lock.release_connection(lease)


def test_the_os_releases_an_exclusive_lock_when_a_process_crashes(tmp_path):
    book = tmp_path / "crash.ledgertb"
    process = _spawn("crash", book)
    _finish(process)

    with maintenance_lock.hold(book):
        pass


def test_a_preexisting_database_connection_blocks_maintenance(db):
    """The original lost-write hole: the connection predates maintenance."""
    book = dbconn.DATABASE_PATH
    connection = dbconn.get_connection()
    try:
        with pytest.raises(maintenance_lock.MaintenanceBusy):
            with maintenance_lock.hold(book):
                pass
    finally:
        connection.close()

    with maintenance_lock.hold(book):
        pass


def test_a_preexisting_direct_live_book_connection_blocks_maintenance(db):
    """Integrity checks that bypass the model connection still take a lease."""
    book = dbconn.DATABASE_PATH
    connection = dbconn.open_keyed(book)
    try:
        with pytest.raises(maintenance_lock.MaintenanceBusy):
            with maintenance_lock.hold(book):
                pass
    finally:
        connection.close()

    with maintenance_lock.hold(book):
        pass
