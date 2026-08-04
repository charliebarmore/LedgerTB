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
import reportlab       # noqa: F401  (close-package PDF export)
try:
    import sqlcipher3  # noqa: F401  (encrypted database driver; the desktop bundle always ships it)
except ImportError:
    pass  # dev-only fallback: the app runs unencrypted via stdlib sqlite3
import anthropic       # noqa: F401
import dotenv          # noqa: F401
import platformdirs    # noqa: F401
import keyring         # noqa: F401
import altair          # noqa: F401  (streamlit dependency used for charts)
import fitz             # noqa: F401  (PDF text extraction and page rendering)
import PIL              # noqa: F401  (image metadata/packaging support)
if sys.platform == "darwin":
    import Quartz       # noqa: F401  (native image decoding for Apple Vision OCR)


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
        # Also set in .streamlit/config.toml; passed here too so the product
        # chrome stays hidden even if that folder goes missing from a bundle
        # (it did once: upload-artifact excludes hidden files by default).
        "--client.toolbarMode=minimal",
        "--client.showSidebarNavigation=false",
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


def _selfcheck() -> int:
    """Import the app's key runtime deps and report — verifies the (trimmed)
    bundle didn't drop anything the app needs. Run: PROBOOKS_MODE=selfcheck <bin>"""
    os.chdir(BUNDLE)
    sys.path.insert(0, str(BUNDLE))
    # Import database.connection before config to mirror app.py's real cold-start
    # path and catch package-level circular imports in the frozen bundle.
    # NB: submodules must be listed explicitly — importing a package does NOT
    # import its submodules, so a bare "openpyxl" here passed while the Excel
    # export crashed on the missing openpyxl.styles / openpyxl.utils.
    mods = ["sqlcipher3", "database.connection", "database.crypto", "streamlit", "pandas",
            "numpy", "pyarrow", "altair", "openpyxl",
            "openpyxl.styles", "openpyxl.utils", "openpyxl.utils.dataframe",
            "reportlab", "reportlab.platypus", "reportlab.lib.pagesizes",
            "reportlab.lib.styles", "reportlab.pdfgen.canvas",
            "anthropic", "pydantic", "pydantic_core", "dotenv", "platformdirs",
            "mcp", "mcp.server", "mcp.server.stdio",
            "config", "constants", "money", "models.journal_entry", "models.reconciliation",
            "services.categorization", "services.document_import", "fitz", "PIL",
            "keyring", "version"]
    failed = []
    for m in mods:
        try:
            __import__(m)
        except Exception as e:
            failed.append(f"{m}: {e}")
    try:
        import keyring
        backend = keyring.get_keyring()
        if sys.platform == "darwin" and type(backend).__module__ != "keyring.backends.macOS":
            failed.append(
                f"keyring backend: unexpected {type(backend).__module__}.{type(backend).__name__}"
            )
    except Exception as e:
        failed.append(f"keyring backend: {e}")
    if sys.platform == "darwin":
        try:
            # Exercise the dynamically-loaded Vision framework, not just the
            # Python imports. PyInstaller cannot discover these Objective-C
            # classes statically, so a real recognition catches a broken OCR
            # bundle before it is installed.
            import io
            from PIL import Image, ImageDraw, ImageFont
            from services.document_import import _vision_ocr

            sample = Image.new("RGB", (900, 180), "white")
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 48)
            ImageDraw.Draw(sample).text(
                (30, 60), "PROBOOKS OCR 123.45", font=font, fill="black"
            )
            encoded = io.BytesIO()
            sample.save(encoded, format="PNG")
            recognized = _vision_ocr(encoded.getvalue()).upper()
            if "PROBOOKS" not in recognized:
                failed.append(f"Apple Vision OCR: unexpected result {recognized!r}")
        except Exception as e:
            failed.append(f"Apple Vision OCR: {e}")
    if failed:
        print("SELFCHECK FAIL:\n  " + "\n  ".join(failed))
        return 1
    print("SELFCHECK OK — all", len(mods), "modules import")
    return 0


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


def main() -> int:
    if os.environ.get("PROBOOKS_MODE") == "selfcheck":
        return _selfcheck()
    if os.environ.get("PROBOOKS_MODE") == "server":
        return _run_server()
    if os.environ.get("PROBOOKS_MODE") == "mcp":
        # Read-only MCP server over stdio (spawned by Claude Desktop/Code).
        os.chdir(BUNDLE)
        sys.path.insert(0, str(BUNDLE))
        from mcp_server import main as mcp_main
        return mcp_main()

    port = _find_free_port()
    url = f"http://127.0.0.1:{port}"

    env = dict(os.environ, PROBOOKS_MODE="server", PROBOOKS_PORT=str(port))
    kwargs = {"env": env}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    server_log = None
    if os.name == "nt":
        # In a windowed (no-console) build the child would inherit invalid
        # stdio handles and die on its first write, so give it a log file and
        # keep any transient console from flashing.
        from platformdirs import user_data_dir

        log_dir = Path(user_data_dir("ProBooks", appauthor=False))
        log_dir.mkdir(parents=True, exist_ok=True)
        server_log = open(log_dir / "server.log", "a", buffering=1, encoding="utf-8")
        kwargs["stdout"] = server_log
        kwargs["stderr"] = subprocess.STDOUT
        kwargs["stdin"] = subprocess.DEVNULL
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(_child_command(port), **kwargs)

    try:
        if not _wait_until_ready(url):
            print("ProBooks server did not become ready.", file=sys.stderr)
            return 1

        import webview
        webview.create_window(WINDOW_TITLE, url, **_window_geometry())
        # Pin the native backend per platform (macOS WebKit, Windows WebView2)
        # so the build can safely exclude the Qt toolkits from the bundle.
        webview.start(gui="cocoa" if sys.platform == "darwin" else "edgechromium")
        return 0
    finally:
        _stop(proc)
        if server_log is not None:
            server_log.close()


if __name__ == "__main__":
    raise SystemExit(main())
