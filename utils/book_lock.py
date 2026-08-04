"""In-use lock for book files.

SQLite's own file locking is unreliable on network shares (SMB/NFS), so a
shared-drive book is coordinated the way desktop accounting always has been:
a visible sidecar lock file naming who has it open, with an explicit
takeover. One writer at a time; a second opener chooses read-only or
takeover. Locks do not expire on their own — a crash leaves a stale lock
that the next opener takes over deliberately (a stale lock from THIS machine
and user with a dead process is reclaimed automatically).
"""
import getpass
import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path


def lock_path(book) -> Path:
    book = Path(book)
    return book.with_name(book.name + ".lock")


def _me() -> dict:
    return {
        "user": getpass.getuser(),
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "opened_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def read_lock(book):
    """The current holder dict, or None."""
    try:
        return json.loads(lock_path(book).read_text())
    except Exception:
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return True  # can't tell -> assume alive


def _is_reclaimable(holder: dict) -> bool:
    """True when the lock is ours already, or was ours and the process died.
    Liveness is only checkable on the same machine; a lock from another host
    is never reclaimed silently."""
    me = _me()
    if holder.get("host") != me["host"]:
        return False
    if holder.get("pid") == me["pid"]:
        return True
    return holder.get("user") == me["user"] and not _pid_alive(int(holder.get("pid", 0)))


def acquire(book) -> dict:
    """Try to take the book for writing.

    Returns {"acquired": True} or {"acquired": False, "holder": {...}}.
    """
    holder = read_lock(book)
    if holder and not _is_reclaimable(holder):
        return {"acquired": False, "holder": holder}
    return takeover(book)


def takeover(book) -> dict:
    """Write our lock regardless of any existing holder (the caller has
    confirmed the takeover)."""
    path = lock_path(book)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_me(), indent=2) + "\n")
    return {"acquired": True}


def release(book) -> None:
    """Remove the lock if it is ours (never someone else's)."""
    holder = read_lock(book)
    me = _me()
    if holder and holder.get("host") == me["host"] and holder.get("pid") == me["pid"]:
        lock_path(book).unlink(missing_ok=True)


def describe(holder: dict) -> str:
    opened = holder.get("opened_at", "")
    return (f"{holder.get('user', '?')} on {holder.get('host', '?')}"
            + (f" since {opened}" if opened else ""))
