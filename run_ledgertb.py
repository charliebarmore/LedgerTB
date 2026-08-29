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
import threading
import time
import urllib.request
from pathlib import Path

# LedgerTB's own modules are bundled as source files because Streamlit needs to
# execute and scan them from disk. A frozen app must never write __pycache__
# files beside that source: doing so mutates the sealed .app after signing and
# makes Gatekeeper reject it on a later launch.
if getattr(sys, "frozen", False):
    sys.dont_write_bytecode = True

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
import portalocker     # noqa: F401  (cross-process live-book coordination)
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
WINDOWS_APP_MUTEX = "LedgerLabs.LedgerTB.8EE4B706-D4BD-4A9E-97DB-219152E5C235"
WINDOWS_CLOSE_GRACE_SECONDS = 8.0
_windows_app_mutex_handle = None


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
    parent_pid = _app_env("PARENT_PID")
    if parent_pid:
        try:
            expected_parent = int(parent_pid)
        except ValueError:
            expected_parent = 0
        if expected_parent > 0:
            threading.Thread(
                target=_exit_when_parent_dies,
                args=(expected_parent,),
                name="ledgertb-parent-watch",
                daemon=True,
            ).start()
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


def _parent_is_alive(expected_pid: int) -> bool:
    """Whether the desktop process that launched this server still exists.

    A pywebview/backend crash can bypass the parent's ``finally`` block. The
    server must not then remain alive invisibly, holding the current book's
    one-writer lock. On POSIX, re-parenting gives a reliable answer. Windows
    keeps the original parent id, so query the process handle instead.
    """
    if os.name == "posix":
        return os.getppid() == expected_pid
    if os.name == "nt":
        try:
            import ctypes

            synchronize = 0x00100000
            wait_timeout = 0x00000102
            handle = ctypes.windll.kernel32.OpenProcess(
                synchronize, False, expected_pid
            )
            if not handle:
                return False
            try:
                return (
                    ctypes.windll.kernel32.WaitForSingleObject(handle, 0)
                    == wait_timeout
                )
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return True  # Cannot prove it died; do not kill a healthy server.
    return True


def _exit_when_parent_dies(expected_pid: int) -> None:
    """Release this server's book lock and exit if its window process dies."""
    while _parent_is_alive(expected_pid):
        time.sleep(1)
    try:
        from database import connection as dbconn
        from utils import book_lock

        book_lock.release(dbconn.DATABASE_PATH)
    except Exception:
        pass
    os._exit(0)


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
        return
    except Exception:
        pass
    try:
        proc.kill()
        # Reap the process after the hard-stop fallback instead of treating a
        # kill request as proof that shutdown has actually completed.
        proc.wait(timeout=2)
    except Exception:
        pass


def _create_windows_app_mutex() -> None:
    """Advertise a running desktop instance to the Windows installer.

    Inno Setup checks this named kernel object before replacing application
    files. Keep the handle for the lifetime of the process; Windows releases
    it automatically even after an abnormal exit.
    """
    global _windows_app_mutex_handle

    if os.name != "nt" or _windows_app_mutex_handle is not None:
        return
    try:
        import ctypes
        from ctypes import wintypes

        create_mutex = ctypes.windll.kernel32.CreateMutexW
        create_mutex.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        create_mutex.restype = wintypes.HANDLE
        handle = create_mutex(None, False, WINDOWS_APP_MUTEX)
        if handle:
            _windows_app_mutex_handle = handle
    except Exception:
        # This guard improves upgrades but is not required to run a book. The
        # window-close lifecycle below remains the primary shutdown path.
        pass


def _force_exit_if_window_loop_stalls(
    window_closed: threading.Event,
    gui_returned: threading.Event,
    timeout: float = WINDOWS_CLOSE_GRACE_SECONDS,
) -> None:
    """Bound a pywebview backend that remains alive after its window closes."""
    window_closed.wait()
    if not gui_returned.wait(timeout):
        os._exit(0)


def _register_windows_close_handler(
    window,
    stop_child,
    gui_returned: threading.Event,
) -> None:
    """Stop the server at native-window close, independent of GUI teardown.

    pywebview normally returns from ``start`` after its final window closes.
    WebView2/WinForms teardown can occasionally stall, however, which used to
    leave both LedgerTB.exe processes visible in Task Manager. The native
    ``closed`` event starts child cleanup immediately; a daemon watchdog gives
    the GUI loop time to unwind and then ends the otherwise-empty parent.
    """
    if os.name != "nt":
        return

    window_closed = threading.Event()

    def on_closed() -> None:
        window_closed.set()
        stop_child()

    window.events.closed += on_closed
    threading.Thread(
        target=_force_exit_if_window_loop_stalls,
        args=(window_closed, gui_returned),
        name="ledgertb-window-close-watch",
        daemon=True,
    ).start()


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
            "portalocker",
            "mcp", "mcp.server", "mcp.server.stdio",
            "config", "constants", "money", "models.journal_entry", "models.reconciliation",
            "models.recurring_entry", "services.recurring_entries",
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
    # Keep the environment modes used by the release scripts, but make the
    # diagnostic safe when a human naturally writes ``LedgerTB --selfcheck``.
    # Without this, that command launches a second full app and can leave the
    # current book looking as though another user has it open.
    if len(sys.argv) == 2 and sys.argv[1] == "--selfcheck":
        mode = "selfcheck"
    if mode == "selfcheck":
        return _selfcheck()
    # Every long-lived process holds the same installer mutex. If the desktop
    # parent dies first, its server child (or an MCP process) still prevents an
    # upgrade from replacing the executable underneath it.
    _create_windows_app_mutex()
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
               LEDGERTB_UI_TOKEN=ui_token, LEDGERTB_PARENT_PID=str(os.getpid()))
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
    stop_lock = threading.Lock()

    def stop_child() -> None:
        # The pywebview event runs on its own thread and can race the outer
        # finally block. Serialize the stop so one close request cannot wait
        # on or kill an already-reaped process from another thread.
        with stop_lock:
            _stop(proc)

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
            window = webview.create_window(
                WINDOW_TITLE, window_url, text_select=True, **geom
            )
            gui_returned = threading.Event()
            _register_windows_close_handler(window, stop_child, gui_returned)
            # Pin the native backend per platform (macOS WebKit, Windows
            # WebView2) so the build can safely exclude the Qt toolkits.
            try:
                webview.start(
                    _place_window, (window, win_x, win_y),
                    gui="cocoa" if sys.platform == "darwin" else "edgechromium",
                )
            finally:
                gui_returned.set()
        except Exception as exc:
            if os.name == "nt" and _looks_like_clr_failure(exc):
                _show_windows_message(_BLOCKED_MESSAGE)
                return 1
            raise
        return 0
    finally:
        stop_child()
        if server_log is not None:
            server_log.close()


if __name__ == "__main__":
    raise SystemExit(main())
