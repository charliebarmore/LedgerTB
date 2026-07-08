"""Frozen-app entry point for ProBooks (PyInstaller / Tier 2).

One binary, two modes:
  * default (parent): pick a free port, launch the *same* binary in server mode
    as a child process, wait for it, then show it in a native pywebview window.
  * server mode (child, PROBOOKS_MODE=server): run the Streamlit server via its
    CLI as the process's main thread -- which avoids the signal/main-thread
    pitfalls of running Streamlit inside a background thread.

This also runs from source (`python run_probooks.py`) for testing: the child is
launched as `python run_probooks.py` instead of the frozen binary.

Heavy third-party deps are imported here so PyInstaller's analysis bundles them
(the app modules load them at runtime from the bundled source tree).
"""

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# --- ensure PyInstaller bundles the app's third-party deps (imported by the
# app's models/services at runtime, which analysis of this file wouldn't see) ---
import pandas          # noqa: F401
import numpy           # noqa: F401
import openpyxl        # noqa: F401
import anthropic       # noqa: F401
import dotenv          # noqa: F401
import platformdirs    # noqa: F401
import altair          # noqa: F401  (streamlit dependency used for charts)


def bundle_dir() -> Path:
    """Directory that holds the bundled app source (app.py, pages/, .streamlit/,
    database/migrations/, models/, ...). Under PyInstaller that's sys._MEIPASS;
    from source it's this file's directory."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


BUNDLE = bundle_dir()
WINDOW_TITLE = "ProBooks"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_server() -> int:
    """Child process: run Streamlit as the main thread via its CLI."""
    port = os.environ.get("PROBOOKS_PORT", "8501")
    os.chdir(BUNDLE)  # so Streamlit finds pages/ and .streamlit/config.toml
    sys.argv = [
        "streamlit", "run", str(BUNDLE / "app.py"),
        "--server.headless=true",
        "--server.address=127.0.0.1",
        f"--server.port={port}",
        "--server.runOnSave=false",
        "--browser.gatherUsageStats=false",
        "--global.developmentMode=false",
    ]
    from streamlit.web import cli as stcli
    return stcli.main()


def _child_command(port: int) -> list:
    env_exe = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, os.path.abspath(__file__)]
    return env_exe


def _wait_until_ready(url: str, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return True
        except Exception:
            time.sleep(0.3)
    return False


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def main() -> int:
    if os.environ.get("PROBOOKS_MODE") == "server":
        return _run_server()

    port = _find_free_port()
    url = f"http://127.0.0.1:{port}"

    env = dict(os.environ, PROBOOKS_MODE="server", PROBOOKS_PORT=str(port))
    kwargs = {"env": env}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(_child_command(port), **kwargs)

    try:
        if not _wait_until_ready(url):
            print("ProBooks server did not become ready.", file=sys.stderr)
            return 1

        import webview
        webview.create_window(WINDOW_TITLE, url, width=1360, height=900, min_size=(1024, 720))
        webview.start()
        return 0
    finally:
        _stop(proc)


if __name__ == "__main__":
    raise SystemExit(main())
