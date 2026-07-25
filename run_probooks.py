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
import sqlcipher3      # noqa: F401  (encrypted database driver)
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
            "anthropic", "pydantic", "pydantic_core", "dotenv", "platformdirs",
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


def main() -> int:
    if os.environ.get("PROBOOKS_MODE") == "selfcheck":
        return _selfcheck()
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
        # Force the native macOS backend (Cocoa/WebKit) so the build can safely
        # exclude the Qt toolkits from the bundle.
        webview.start(gui="cocoa")
        return 0
    finally:
        _stop(proc)


if __name__ == "__main__":
    raise SystemExit(main())
