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
import secrets
import threading
from contextlib import contextmanager
from pathlib import Path

# Writer contexts nest: a guarded tool may call another guarded helper. Only the
# outermost publishes and removes the marker, so an inner one finishing cannot
# retract a claim the outer one still needs.
_local = threading.local()


class MaintenanceBusy(RuntimeError):
    """Raised when the book cannot be taken for maintenance, or written to
    because maintenance is under way."""


def maintenance_path(book) -> Path:
    return Path(str(book) + ".maintenance")


def _writer_path(book, token: str) -> Path:
    """One marker per in-flight invocation, not per process.

    MCP runs tool calls on parallel threads in one process. A per-process marker
    meant the first call to finish removed the claim while the second was still
    writing, which is how a lost write stayed reachable through the lock.
    """
    return Path(f"{book}.writer-{os.getpid()}-{token}")


def _process_is_alive(pid: int) -> bool:
    """Whether a process exists, on both platforms this app ships to.

    POSIX signals 0 to probe and raises ProcessLookupError when there is no
    such process. Windows does not: verified on Windows 11 with Python 3.12,
    os.kill(pid, 0) returns normally for a live process (it does not terminate
    it) and raises OSError WinError 87, "the parameter is incorrect", for a pid
    with no process behind it. Treating only ProcessLookupError as death left
    every stale marker on Windows looking live, which would have blocked
    maintenance permanently after any crash.

    Anything else is ambiguous and counts as alive. Refusing maintenance is
    recoverable; writing over somebody's transaction is not.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                     # exists, owned by another user
    except OSError as exc:
        if getattr(exc, "winerror", None) == 87:
            return False                # Windows: no process with that id
        return True
    return True


def _pid_of(marker: Path):
    """The pid a marker names, or None if it cannot be read as one."""
    parts = marker.name.rsplit("-", 2)
    if len(parts) < 2:
        return None
    try:
        return int(parts[-2])
    except ValueError:
        return None


def _live_writers(book):
    """Writer markers that belong to a process still running.

    A marker left behind by a crash would otherwise block maintenance forever,
    so a marker whose process is gone is removed rather than believed.
    """
    book = Path(book)
    live = []
    for marker in book.parent.glob(book.name + ".writer-*"):
        pid = _pid_of(marker)
        if pid is None:
            # A name we cannot interpret is not evidence that nobody is
            # writing, so it counts as live rather than being cleaned up.
            live.append(marker)
            continue
        # Same-process markers are NOT skipped. A write in flight on another
        # thread of this process is as real as one in the MCP process, and the
        # desktop app runs concurrent session threads. The rotating thread
        # itself never holds a writer marker, so this cannot block on its own
        # claim.
        if not _process_is_alive(pid):
            marker.unlink(missing_ok=True)
            continue
        live.append(marker)
    return live


def _holder_is_gone(lock: Path) -> bool:
    """Whether a maintenance lock's recorded process is no longer running."""
    try:
        pid = int(lock.read_text().strip() or 0)
    except (OSError, ValueError):
        return True                 # unreadable or malformed: not a live claim
    if pid <= 0:
        return True
    if pid == os.getpid():
        # Our own live claim. Reclaiming it would turn a second concurrent
        # rotation in this process into a silent success.
        return False
    return not _process_is_alive(pid)


@contextmanager
def hold(book):
    """Take the book for maintenance, or raise MaintenanceBusy."""
    book = Path(book)
    lock = maintenance_path(book)
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        # A crash mid-maintenance would otherwise block this book forever, so a
        # lock whose holder is gone is reclaimed. Unlinking it first was racy:
        # two processes could both judge it stale and each delete the other's
        # fresh claim. Renaming it aside is the atomic step instead, since
        # exactly one process can rename a given path; the loser sees it gone.
        if not _holder_is_gone(lock):
            raise MaintenanceBusy(
                "Another maintenance operation is already running on this book."
            ) from exc
        aside = Path(f"{lock}.stale-{secrets.token_hex(8)}")
        try:
            os.rename(str(lock), str(aside))
        except FileNotFoundError:
            # Someone else reclaimed it first. Do not race them for it.
            raise MaintenanceBusy(
                "Another maintenance operation is already running on this book."
            ) from exc
        aside.unlink(missing_ok=True)
        # Deliberately no retry loop. If a third process claimed the canonical
        # name in the gap, that claim is live and this attempt refuses; looping
        # here is exactly how two holders could both believe they won.
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise MaintenanceBusy(
                "Another maintenance operation is already running on this book."
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
    maintained. Wrapped around a whole mutating MCP tool invocation.

    Reentrant per thread: nesting increments a depth and only the outermost
    context publishes and removes the marker. Without that, an inner guard
    finishing would retract a claim the outer one still depends on.
    """
    book = Path(book)
    depth = getattr(_local, "depth", 0)
    if depth:
        _local.depth = depth + 1
        try:
            yield
        finally:
            _local.depth -= 1
        return

    token = secrets.token_hex(8)
    marker = _writer_path(book, token)
    try:
        fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except OSError as exc:
        raise MaintenanceBusy(f"Could not register a write on this book: {exc}") from exc
    _local.depth = 1
    try:
        if maintenance_path(book).exists():
            raise MaintenanceBusy(
                "This book is being maintained right now (its passphrase is "
                "being changed). Try again once that finishes."
            )
        yield
    finally:
        _local.depth = 0
        marker.unlink(missing_ok=True)


def writing_now() -> bool:
    """Whether this thread is inside a declared write.

    The enforcement point for writes that never declared themselves: an
    assistant connection opened outside a writer context is opened read-only,
    so a tool that mutates while claiming to be read-only fails loudly instead
    of slipping past the lock.
    """
    return getattr(_local, "depth", 0) > 0


def under_maintenance(book) -> bool:
    return maintenance_path(book).exists()
