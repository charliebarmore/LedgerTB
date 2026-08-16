"""Standalone child used by the cross-process maintenance-lock tests."""

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from utils import maintenance_lock


mode, book = sys.argv[1:]

if mode == "shared":
    lease = maintenance_lock.acquire_connection(book)
    print("ready", flush=True)
    sys.stdin.readline()
    maintenance_lock.release_connection(lease)
elif mode == "exclusive":
    with maintenance_lock.hold(book):
        print("ready", flush=True)
        sys.stdin.readline()
elif mode == "crash":
    context = maintenance_lock.hold(book)
    context.__enter__()
    print("ready", flush=True)
    os._exit(0)
else:
    raise ValueError(f"unknown lock mode: {mode}")
