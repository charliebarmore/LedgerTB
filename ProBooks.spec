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

from PyInstaller.utils.hooks import collect_all, copy_metadata

# --- app source, bundled as data (loaded from disk at runtime) ---
app_datas = [
    ("app.py", "."),
    ("config.py", "."),
    ("constants.py", "."),
    ("money.py", "."),
    (".streamlit", ".streamlit"),
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
    "python-dotenv", "gitpython", "packaging",
):
    try:
        metadatas += copy_metadata(pkg)
    except Exception:
        pass

hiddenimports = st_hiddenimports + [
    "pandas", "numpy", "openpyxl", "anthropic", "dotenv", "platformdirs", "altair",
]

a = Analysis(
    ["run_probooks.py"],
    pathex=[],
    binaries=st_binaries,
    datas=app_datas + st_datas + metadatas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ProBooks",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # windowed (no terminal)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ProBooks",
)

app = BUNDLE(
    coll,
    name="ProBooks.app",
    icon="ProBooks.app/Contents/Resources/ProBooks.icns",
    bundle_identifier="com.ledgerlabs.probooks",
    info_plist={
        "CFBundleName": "ProBooks",
        "CFBundleDisplayName": "ProBooks",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.finance",
        "LSMinimumSystemVersion": "10.13",
    },
)
