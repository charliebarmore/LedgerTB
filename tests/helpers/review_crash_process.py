"""Separate interpreter startup from the deliberately interrupted transaction."""
import os
from pathlib import Path
import subprocess
import sys
import time


def run_worker(fixture, database_path, *, worker='review_crash_worker.py'):
    ready = fixture.with_suffix('.ready')
    ready.unlink(missing_ok=True)
    command = [sys.executable, str(Path(__file__).with_name(worker)), str(fixture)]
    process = subprocess.Popen(command, env=dict(os.environ, LEDGERTB_DB_PATH=str(database_path)),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    # Diagnostic allowance for a heavily contended workstation, not a product
    # startup benchmark. The transaction itself retains its 30-second limit.
    allowance = float(os.environ.get('LEDGERTB_TEST_STARTUP_TIMEOUT', '60'))
    deadline = time.monotonic() + allowance
    try:
        while not ready.exists() and process.poll() is None:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(f'{worker} startup', allowance)
            time.sleep(.05)
        stdout, stderr = process.communicate(timeout=30)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
