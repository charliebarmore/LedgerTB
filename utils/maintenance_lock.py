"""Cross-process coordination for operations that replace a whole book.

Passphrase rotation prepares a new encrypted database and atomically replaces
the live file.  Every connection that can still touch the old file must be gone
before that replacement happens; checking only when maintenance starts misses
connections that were already open.

The operating system owns this protocol:

* every live LedgerTB database connection holds a shared lock;
* a complete assistant mutation also holds a shared lock, including work before
  and after its individual database connections;
* maintenance holds the exclusive lock.

The coordination file is permanent and contains no state.  Locks belong to
open file handles, so the OS releases them if a process crashes.  There are no
PIDs to probe, stale files to reclaim, or pathnames to rename while another
process may be using them.
"""

import os
import threading
from contextlib import contextmanager
from pathlib import Path

import portalocker


class MaintenanceBusy(RuntimeError):
    """The book is in use and cannot enter the requested lock mode."""


_local = threading.local()


def maintenance_path(book) -> Path:
    """Permanent sidecar whose OS lock coordinates live-book access."""
    return Path(str(book) + ".maintenance.lock")


def _acquire(book, flags, message: str):
    path = maintenance_path(book)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = path.open("a+b")
    except OSError as exc:
        raise MaintenanceBusy(
            f"The coordination file for this book could not be opened: {exc}"
        ) from exc
    try:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass  # Windows has no equivalent mode; the user profile is the boundary.
        portalocker.lock(handle, flags | portalocker.LOCK_NB)
    except portalocker.exceptions.LockException as exc:
        handle.close()
        raise MaintenanceBusy(message) from exc
    except Exception:
        handle.close()
        raise
    return handle


def _release(handle) -> None:
    if handle is None:
        return
    try:
        portalocker.unlock(handle)
    finally:
        handle.close()


def acquire_connection(book):
    """Acquire the shared lease held for one live database connection.

    The maintenance holder is allowed to open the connections it needs for the
    backup, export, verification, and archive conversion.  The exemption is
    thread-local: another Streamlit session in this process still has to acquire
    a shared lock and is refused while rotation holds the exclusive one.
    """
    if holding_now():
        return None
    return _acquire(
        book,
        portalocker.LOCK_SH,
        "This book is being maintained right now (its passphrase is being "
        "changed). Try again once that finishes.",
    )


def release_connection(lease) -> None:
    _release(lease)


@contextmanager
def hold(book):
    """Take the exclusive maintenance lock, or fail without waiting."""
    if holding_now():
        raise MaintenanceBusy(
            "Another maintenance operation is already running on this book."
        )
    lease = _acquire(
        book,
        portalocker.LOCK_EX,
        "This book is still in use. Close other LedgerTB windows and assistant "
        "access, wait for current work to finish, and try again.",
    )
    _local.holding = True
    try:
        yield
    finally:
        _local.holding = False
        _release(lease)


@contextmanager
def writer(book):
    """Hold a shared lock across a complete assistant mutation.

    Database connections also hold shared locks.  This wider guard covers work
    outside those connections, including validation and the audit at the end of
    an export.  It is reentrant per thread because the MCP server wrapper calls
    implementations that carry the same declaration.
    """
    if holding_now():
        raise MaintenanceBusy(
            "This book is being maintained right now; assistant writes are "
            "temporarily unavailable."
        )
    depth = getattr(_local, "writer_depth", 0)
    if depth:
        _local.writer_depth = depth + 1
        try:
            yield
        finally:
            _local.writer_depth -= 1
        return

    lease = acquire_connection(book)
    _local.writer_depth = 1
    try:
        yield
    finally:
        _local.writer_depth = 0
        release_connection(lease)


def holding_now() -> bool:
    """Whether this thread owns the exclusive maintenance lock."""
    return bool(getattr(_local, "holding", False))


def writing_now() -> bool:
    """Whether this thread is inside a declared assistant mutation."""
    return getattr(_local, "writer_depth", 0) > 0


def under_maintenance(book) -> bool:
    """Whether another thread or process currently owns the exclusive lock."""
    if holding_now():
        return True
    try:
        lease = acquire_connection(book)
    except MaintenanceBusy:
        return True
    release_connection(lease)
    return False
