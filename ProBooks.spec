# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for a standalone ProBooks.app (no Python required).

Build:   pyinstaller ProBooks.spec --noconfirm
Result:  dist/ProBooks.app

The app's own source (app.py, pages/, models/, services/, database/, utils/,
config/constants/money, .streamlit/) is bundled as DATA and loaded from disk at
runtime -- Streamlit runs app.py and scans pages/ from the bundle dir, and the
app inserts that dir on sys.path to import its own packages. The third-party
deps those modules use are frozen normally (imported in run_probooks.py so the
analysis sees them).
"""

import os
import sys

# Spec files execute in PyInstaller's build namespace, which does not guarantee
# that the project root is importable. Read the tiny metadata module directly.
version_meta = {}
with open("version.py", encoding="utf-8") as version_file:
    exec(version_file.read(), version_meta)
APP_VERSION = version_meta["APP_VERSION"]

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

# Set PROBOOKS_CODESIGN_ID to a Developer ID Application identity to have
# PyInstaller sign the bundle (inside-out, hardened runtime) during the build;
# leave unset for a normal ad-hoc-signed local build.
CODESIGN_ID = os.environ.get("PROBOOKS_CODESIGN_ID") or None

# Modules the app never uses but that get dragged in from a large Anaconda env.
# Trimming these takes the bundle from ~1.2 GB to a fraction of that.
EXCLUDES = [
    "tkinter", "pytest", "_pytest",
    # heavy scientific / ML stack (unused)
    "scipy", "sklearn", "skimage", "statsmodels", "sympy",
    "numba", "llvmlite", "h5py", "tables", "netCDF4",
    "matplotlib", "seaborn", "bokeh", "plotly",
    # notebook / IPython stack (unused)
    "IPython", "ipykernel", "jupyter", "jupyter_client", "jupyter_core",
    "notebook", "nbconvert", "nbformat", "qtconsole",
    # Qt GUI toolkits — pywebview uses the native Cocoa backend on macOS
    "PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy",
    # dev tooling
    "mypy", "sphinx", "docutils",
]

# --- app source, bundled as data (loaded from disk at runtime) ---
app_datas = [
    ("app.py", "."),
    ("mcp_server.py", "."),
    ("config.py", "."),
    ("constants.py", "."),
    ("money.py", "."),
    ("version.py", "."),
    ("LICENSE", "."),
    ("THIRD_PARTY_NOTICES.md", "."),
    (".streamlit", ".streamlit"),
    ("assets", "assets"),
    ("pages", "pages"),
    ("models", "models"),
    ("services", "services"),
    ("database", "database"),
    ("utils", "utils"),
]

# --- Streamlit: collect its data files, binaries, and dynamic imports ---
st_datas, st_binaries, st_hiddenimports = collect_all("streamlit")

# --- package metadata (many libs read their version via importlib.metadata) ---
metadatas = []
for pkg in (
    "streamlit", "pandas", "numpy", "pyarrow", "altair", "anthropic",
    "openpyxl", "pillow", "tornado", "click", "rich", "platformdirs",
    "python-dotenv", "gitpython", "packaging", "keyring",
    "pypdfium2", "Pillow", "pyobjc-framework-Quartz",
):
    try:
        metadatas += copy_metadata(pkg)
    except Exception:
        pass

hiddenimports = st_hiddenimports + [
    "pandas", "numpy", "openpyxl", "anthropic", "dotenv", "platformdirs", "altair",
    # Both credential-vault backends: the frozen app must reach the OS vault
    # on each platform (API key + MCP enablement live there).
    "keyring", "keyring.backends.macOS", "keyring.backends.Windows",
    "pypdfium2", "pypdfium2_raw", "PIL", "Quartz", "objc",
    "sqlcipher3", "sqlcipher3.dbapi2",  # encrypted database driver (native ext)
] + collect_submodules("openpyxl") + collect_submodules("reportlab") \
  + collect_submodules("mcp", filter=lambda name: "mcp.cli" not in name)
    # mcp: the server entry (mcp_server.py) is a data file, invisible to the
    # analyzer — same reason openpyxl/reportlab are collected wholesale.
    # mcp.cli is excluded: it imports typer (the optional mcp[cli] extra),
    # which clean environments don't have and the server never uses.
    # Bundle ALL openpyxl and reportlab submodules. Pages/services are bundled as
    # data files, so PyInstaller can't see which submodules they import; collect
    # them all to avoid ModuleNotFound (the openpyxl.utils.dataframe lesson).

a = Analysis(
    ["run_probooks.py"],
    pathex=[],
    binaries=st_binaries,
    datas=app_datas + st_datas + metadatas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure)

# strip needs binutils (absent on Windows runners).
IS_MAC = sys.platform == "darwin"

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ProBooks",
    debug=False,
    bootloader_ignore_signals=False,
    strip=IS_MAC,
    upx=False,
    console=False,          # windowed on every platform
    icon=None if IS_MAC else "ProBooks.ico",   # BUNDLE carries the .icns
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=CODESIGN_ID,
    entitlements_file="scripts/entitlements.plist",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=IS_MAC,
    upx=False,
    name="ProBooks",
)

if not IS_MAC:
    app = None
else:
    app = BUNDLE(
    coll,
    name="ProBooks.app",
    icon="ProBooks.app/Contents/Resources/ProBooks.icns",
    bundle_identifier="com.ledgerlabs.probooks",
    info_plist={
        "CFBundleName": "ProBooks",
        "CFBundleDisplayName": "ProBooks",
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleVersion": APP_VERSION,
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.finance",
        "LSMinimumSystemVersion": "10.13",
    },
)
