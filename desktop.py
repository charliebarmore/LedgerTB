"""Desktop launcher for LedgerTB (native-window mode).

Starts the Streamlit server headless on a local port and shows it in a native
desktop window (pywebview) instead of a browser tab. Everything stays local --
this only changes how the app is launched and displayed.

    python desktop.py        # run from a terminal
    open LedgerTB.app        # or double-click the macOS app

Requires pywebview (see requirements-desktop.txt).
"""

import atexit
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
WINDOW_TITLE = "LedgerTB"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_streamlit(port: int, ui_token: str) -> subprocess.Popen:
    """Launch `streamlit run app.py` headless on the given port, in its own
    process group so the whole tree can be torn down cleanly on exit.

    The UI token goes in the environment rather than argv: other users on the
    machine can read another process's command line, but not its environment.
    """
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(APP_DIR / "app.py"),
        "--server.headless=true",
        "--server.address=127.0.0.1",
        f"--server.port={port}",
        "--server.runOnSave=false",
        "--browser.gatherUsageStats=false",
    ]
    kwargs = {"cwd": str(APP_DIR),
              "env": dict(os.environ, LEDGERTB_UI_TOKEN=ui_token)}
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
    """Window kwargs that fit -- and sit fully inside -- the display.

    Two separate problems. A hardcoded 1360x900 overflows a 1280x800 logical
    desktop (a 1920x1200 panel at 150% scaling, a common laptop config). And
    pywebview's default placement puts the window at an offset (168,168 on
    Windows) that pushes it off-screen even once it is small enough to fit.

    So: clamp the size to the screen, clamp min_size to the result (a minimum
    larger than the window forces the window back up), and set x/y explicitly
    instead of trusting the default placement.
    """
    import webview

    try:
        screen = webview.screens[0]
        avail_w, avail_h = int(screen.width), int(screen.height)
    except Exception:
        # Screen probing failed -- keep the old size but a workable minimum.
        return {"width": preferred_w, "height": preferred_h,
                "min_size": (min_w, min_h)}

    # Leave room for the taskbar/menu bar and window chrome.
    width = max(800, min(preferred_w, avail_w - 40))
    height = max(600, min(preferred_h, avail_h - 80))
    # Centred horizontally, biased toward the top: screen.height is full
    # bounds, not the usable work area, so a true vertical centre can still
    # tuck the bottom edge under the taskbar.
    x = max(0, (avail_w - width) // 2)
    y = max(0, (avail_h - height) // 3)
    return {"width": width, "height": height,
            "min_size": (min(min_w, width), min(min_h, height)),
            "x": x, "y": y}


def _place_window(window, wx, wy):
    """Un-minimise and position the window once the GUI loop is running.

    Passing x/y straight to create_window makes the Windows (WinForms /
    EdgeChromium) backend start the window minimised -- it parks off-screen
    at roughly (-10667, -10667) with IsIconic set, so the app appears to
    launch and then never show. Sizing at create time is fine; only the
    placement has to wait until the window actually exists.
    """
    try:
        window.restore()
        if wx is not None and wy is not None:
            window.move(wx, wy)
    except Exception:
        pass


def main() -> int:
    port = _find_free_port()
    ui_token = secrets.token_urlsafe(32)
    url = f"http://127.0.0.1:{port}"
    window_url = f"{url}/?t={ui_token}"

    proc = start_streamlit(port, ui_token)
    atexit.register(stop_streamlit, proc)

    if not wait_until_ready(url):
        stop_streamlit(proc)
        print("LedgerTB failed to start (Streamlit did not become ready).", file=sys.stderr)
        return 1

    # Imported here (not at module top) so this file loads without a GUI backend
    # present -- lets the server-start logic be smoke-tested headlessly.
    import webview

    geom = _window_geometry()
    win_x, win_y = geom.pop("x", None), geom.pop("y", None)
    window = webview.create_window(WINDOW_TITLE, window_url, **geom)
    webview.start(_place_window, (window, win_x, win_y))  # blocks until the window is closed

    stop_streamlit(proc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
