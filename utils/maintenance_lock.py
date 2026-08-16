"""Interprocess maintenance lock, for operations that rewrite a whole book.

Changing a passphrase re-encrypts the book and replaces the file. Nothing else
may be writing while that happens, and "nothing else" includes the MCP server,
which is a separate OS process with its own connections and its own copy of the
key. Detecting a concurrent write after the fact is not enough: by then the
write is already lost.

Two sidecar files beside the book, and a publish-then-check on both sides:

- The maintenance holder creates ``<book>.maintenance`` exclusively, then looks
  for writer markers. If it finds any, it releases and refuses.
- A writer creates ``<book>.writer-<pid>`` first, then looks for the
  maintenance file. If it finds one, it removes its marker and refuses.

Publishing before checking is what makes this work. Whichever order the two
processes interleave in, at least one of them sees the other's file and backs
off; the failure mode is both refusing, never both proceeding.

Deliberately not a general mutex. It coordinates one rare, heavy operation
against ordinary work, and it fails closed and loudly rather than waiting.

Scope, stated plainly: this is only sound where an exclusive file create is
atomic, which is local storage. Rotation refuses on non-local books for that
reason among others, so this lock is never the only thing standing between two
machines on a share.
"""

import os
from contextlib import contextmanager
from pathlib import Path


class MaintenanceBusy(RuntimeError):
    """Raised when the book cannot be taken for maintenance, or written to
    because maintenance is under way."""


def maintenance_path(book) -> Path:
    return Path(str(book) + ".maintenance")


def _writer_path(book, pid=None) -> Path:
    return Path(f"{book}.writer-{pid or os.getpid()}")


def _live_writers(book):
    """Writer markers that belong to a process still running.

    A marker left behind by a crash would otherwise block maintenance forever,
    so a marker whose process is gone is removed rather than believed.
    """
    book = Path(book)
    live = []
    for marker in book.parent.glob(book.name + ".writer-*"):
        try:
            pid = int(marker.name.rsplit("-", 1)[1])
        except (IndexError, ValueError):
            marker.unlink(missing_ok=True)
            continue
        if pid == os.getpid():
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            marker.unlink(missing_ok=True)
            continue
        except PermissionError:
            pass                    # alive, owned by someone else
        live.append(marker)
    return live


@contextmanager
def hold(book):
    """Take the book for maintenance, or raise MaintenanceBusy."""
    book = Path(book)
    lock = maintenance_path(book)
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise MaintenanceBusy(
            "Another maintenance operation is already running on this book. If "
            f"nothing else is running, remove {lock.name} and try again."
        ) from exc
    try:
        os.write(fd, f"{os.getpid()}\n".encode())
    finally:
        os.close(fd)

    try:
        writers = _live_writers(book)
        if writers:
            raise MaintenanceBusy(
                "Something else is writing to this book right now, most likely "
                "assistant access. Wait for it to finish, turn it off, and try "
                "again."
            )
        yield
    finally:
        lock.unlink(missing_ok=True)


@contextmanager
def writer(book):
    """Mark a write in progress, or raise MaintenanceBusy if the book is being
    maintained. Used by the MCP process around its own mutations."""
    book = Path(book)
    marker = _writer_path(book)
    try:
        fd = os.open(str(marker), os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
    except OSError as exc:
        raise MaintenanceBusy(f"Could not register a write on this book: {exc}") from exc
    try:
        if maintenance_path(book).exists():
            raise MaintenanceBusy(
                "This book is being maintained right now (its passphrase is "
                "being changed). Try again once that finishes."
            )
        yield
    finally:
        marker.unlink(missing_ok=True)


def under_maintenance(book) -> bool:
    return maintenance_path(book).exists()
