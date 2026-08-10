"""Frozen-app entry point for LedgerTB (PyInstaller / Tier 2).

One binary, two modes:
  * default (parent): pick a free port, launch the *same* binary in server mode
    as a child process, wait for it, then show it in a native pywebview window.
  * server mode (child, LEDGERTB_MODE=server): run the Streamlit server via its
    CLI as the process's main thread -- which avoids the signal/main-thread
    pitfalls of running Streamlit inside a background thread.

This also runs from source (`python run_ledgertb.py`) for testing: the child is
launched as `python run_ledgertb.py` instead of the frozen binary.

Heavy third-party deps are imported here so PyInstaller's analysis bundles them
(the app modules load them at runtime from the bundled source tree).
"""

import os
import secrets
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
import pypdfium2        # noqa: F401  (PDF text extraction and page rendering)
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
WINDOW_TITLE = "LedgerTB"


def _app_env(suffix: str, default=None):
    """Prefer LedgerTB's environment name; accept the ProBooks legacy alias."""
    return (
        os.environ.get(f"LEDGERTB_{suffix}")
        or os.environ.get(f"PROBOOKS_{suffix}")
        or default
    )


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_server() -> int:
    """Child process: run Streamlit as the main thread via its CLI."""
    port = _app_env("PORT", "8501")
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


# --- Windows: mark-of-the-web ------------------------------------------------
# Files extracted from a downloaded zip are tagged Internet-zone, and the .NET
# Framework refuses to load a managed assembly carrying that tag. That stops the
# bundled Python.Runtime.dll loading, which stops pywebview's Windows backend,
# which means no window ever appears. The raw failure is a traceback naming
# clr_loader and a DLL path -- nothing an accountant can act on. Detect it and
# say what to do. The installer never hits this; a zip does.

_BLOCKED_MESSAGE = (
    "Windows has blocked part of LedgerTB because it was downloaded from the "
    "internet, so the app cannot start.\n\n"
    "The simplest fix is to install LedgerTB rather than run it from an "
    "extracted folder. The installer does not have this problem.\n\n"
    "To use this folder anyway:\n"
    "    1. Delete the folder you extracted.\n"
    "    2. Right-click the LedgerTB .zip file you downloaded.\n"
    "    3. Choose Properties, tick Unblock, then click OK.\n"
    "    4. Extract it again and open LedgerTB.\n\n"
    "Your books are not affected."
)


def _show_windows_message(text: str, title: str = "LedgerTB") -> None:
    """Show a native message box. A windowed build has no console, so anything
    printed here would go nowhere the user can see."""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)  # MB_ICONERROR
    except Exception:
        print(text, file=sys.stderr)


def _is_blocked(path: Path) -> bool:
    """True if the file carries mark-of-the-web from a zone .NET will not load
    from: 3 (internet) or 4 (untrusted site).

    Zone 1 is the local intranet -- a firm's shared drive, which is exactly
    where firm mode puts book files -- and must not be mistaken for a download.
    """
    try:
        with open(f"{path}:Zone.Identifier", "r", encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return False
    for line in text.splitlines():
        if line.strip().lower().startswith("zoneid="):
            try:
                return int(line.split("=", 1)[1].strip()) >= 3
            except ValueError:
                return False
    return False


def _webview_blocked_by_windows() -> bool:
    """Is the .NET assembly pywebview depends on tagged as downloaded?"""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return False
    dll = (Path(sys.executable).parent / "_internal" / "pythonnet"
           / "runtime" / "Python.Runtime.dll")
    return dll.exists() and _is_blocked(dll)


def _looks_like_clr_failure(exc: BaseException) -> bool:
    """Does this exception smell like the blocked-assembly failure? Used as a
    backstop when the file check missed it (a different bundle layout, or the
    stream stripped from the DLL but not its dependencies)."""
    text = f"{type(exc).__name__}: {exc}"
    return any(s in text for s in ("Python.Runtime", "clr_loader", "Python.Runtime.Loader"))


def _selfcheck() -> int:
    """Import the app's key runtime deps and report — verifies the (trimmed)
    bundle didn't drop anything the app needs. Run: LEDGERTB_MODE=selfcheck <bin>"""
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
            "services.categorization", "services.document_import", "pypdfium2", "PIL",
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
                (30, 60), "LEDGERTB OCR 123.45", font=font, fill="black"
            )
            encoded = io.BytesIO()
            sample.save(encoded, format="PNG")
            recognized = _vision_ocr(encoded.getvalue()).upper()
            if "LEDGERTB" not in recognized:
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
    mode = _app_env("MODE")
    if mode == "selfcheck":
        return _selfcheck()
    if mode == "server":
        return _run_server()
    if mode == "mcp":
        # Permissioned MCP server over stdio (spawned by Claude Desktop/Code).
        os.chdir(BUNDLE)
        sys.path.insert(0, str(BUNDLE))
        from mcp_server import main as mcp_main
        return mcp_main()

    port = _find_free_port()
    # The token binds the app's own window to this server. Anything else on
    # the machine that finds the port gets refused instead of handed the
    # decrypted books. It travels in the child's environment (not argv, which
    # other users can read via ps) and in the window's URL.
    ui_token = secrets.token_urlsafe(32)
    url = f"http://127.0.0.1:{port}"
    window_url = f"{url}/?t={ui_token}"

    env = dict(os.environ, LEDGERTB_MODE="server", LEDGERTB_PORT=str(port),
               LEDGERTB_UI_TOKEN=ui_token)
    kwargs = {"env": env}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    server_log = None
    if os.name == "nt":
        # In a windowed (no-console) build the child would inherit invalid
        # stdio handles and die on its first write, so give it a log file and
        # keep any transient console from flashing.
        from platformdirs import user_data_dir

        log_dir = Path(user_data_dir("LedgerTB", appauthor=False))
        log_dir.mkdir(parents=True, exist_ok=True)
        server_log = open(log_dir / "server.log", "a", buffering=1, encoding="utf-8")
        kwargs["stdout"] = server_log
        kwargs["stderr"] = subprocess.STDOUT
        kwargs["stdin"] = subprocess.DEVNULL
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(_child_command(port), **kwargs)

    try:
        if not _wait_until_ready(url):
            print("LedgerTB server did not become ready.", file=sys.stderr)
            return 1

        # Checked before touching pywebview: on Windows the backend loads a
        # .NET assembly, and a blocked one fails deep inside clr_loader with a
        # message no user can act on.
        if _webview_blocked_by_windows():
            _show_windows_message(_BLOCKED_MESSAGE)
            return 1

        try:
            import webview
            geom = _window_geometry()
            win_x, win_y = geom.pop("x", None), geom.pop("y", None)
            window = webview.create_window(WINDOW_TITLE, window_url, **geom)
            # Pin the native backend per platform (macOS WebKit, Windows
            # WebView2) so the build can safely exclude the Qt toolkits.
            webview.start(
                _place_window, (window, win_x, win_y),
                gui="cocoa" if sys.platform == "darwin" else "edgechromium",
            )
        except Exception as exc:
            if os.name == "nt" and _looks_like_clr_failure(exc):
                _show_windows_message(_BLOCKED_MESSAGE)
                return 1
            raise
        return 0
    finally:
        _stop(proc)
        if server_log is not None:
            server_log.close()


if __name__ == "__main__":
    raise SystemExit(main())
