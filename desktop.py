"""Desktop launcher for ProBooks (native-window mode).

Starts the Streamlit server headless on a local port and shows it in a native
desktop window (pywebview) instead of a browser tab. Everything stays local --
this only changes how the app is launched and displayed.

    python desktop.py        # run from a terminal
    open ProBooks.app        # or double-click the macOS app

Requires pywebview (see requirements-desktop.txt).
"""

import atexit
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
WINDOW_TITLE = "ProBooks"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_streamlit(port: int) -> subprocess.Popen:
    """Launch `streamlit run app.py` headless on the given port, in its own
    process group so the whole tree can be torn down cleanly on exit."""
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(APP_DIR / "app.py"),
        "--server.headless=true",
        "--server.address=127.0.0.1",
        f"--server.port={port}",
        "--server.runOnSave=false",
        "--browser.gatherUsageStats=false",
    ]
    kwargs = {"cwd": str(APP_DIR)}
    if os.name == "posix":
        kwargs["start_new_session"] = True  # own process group for clean shutdown
    return subprocess.Popen(cmd, **kwargs)


def wait_until_ready(url: str, timeout: float = 40.0) -> bool:
    """Poll the server until it answers or we time out (first run compiles assets)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return True
        except Exception:
            time.sleep(0.3)
    return False


def stop_streamlit(proc: subprocess.Popen) -> None:
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


def _window_geometry(preferred_w=1360, preferred_h=900, min_w=900, min_h=600):
    """Pick a window size that fits the display it opens on.

    A hardcoded 1360x900 overflows a 1280x800 logical desktop (a 1920x1200
    panel at 150% scaling -- a common laptop config), putting the close and
    maximize buttons off-screen. Clamp to the screen, and clamp min_size to
    the resulting size: a minimum larger than the window forces it back up.
    """
    import webview

    try:
        screen = webview.screens[0]
        avail_w, avail_h = int(screen.width), int(screen.height)
    except Exception:
        # Screen probing failed -- keep the old size but a workable minimum.
        return preferred_w, preferred_h, (min_w, min_h)

    # Leave room for the taskbar/menu bar and window chrome.
    width = max(800, min(preferred_w, avail_w - 40))
    height = max(600, min(preferred_h, avail_h - 80))
    return width, height, (min(min_w, width), min(min_h, height))


def main() -> int:
    port = _find_free_port()
    url = f"http://127.0.0.1:{port}"

    proc = start_streamlit(port)
    atexit.register(stop_streamlit, proc)

    if not wait_until_ready(url):
        stop_streamlit(proc)
        print("ProBooks failed to start (Streamlit did not become ready).", file=sys.stderr)
        return 1

    # Imported here (not at module top) so this file loads without a GUI backend
    # present -- lets the server-start logic be smoke-tested headlessly.
    import webview

    win_w, win_h, win_min = _window_geometry()
    webview.create_window(
        WINDOW_TITLE, url,
        width=win_w, height=win_h, min_size=win_min,
    )
    webview.start()  # blocks until the window is closed

    stop_streamlit(proc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
