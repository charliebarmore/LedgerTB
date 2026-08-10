# Running LedgerTB as a desktop app

LedgerTB can run in its own native window (no browser tab, no terminal) via
`pywebview`. Everything stays local — this only changes how the app is launched
and displayed.

## One-time setup

Install the desktop dependency into the same Python that runs LedgerTB:

```bash
python -m pip install -r requirements-desktop.txt
```

(If LedgerTB runs under a different Python than the one on your PATH, install
there instead and set `LEDGERTB_PYTHON` — see below.)

## Launch it

- **Double-click `LedgerTB.app`** in the project folder, or
- Run it from a terminal:

  ```bash
  python desktop.py
  ```

A native LedgerTB window opens with the navy **LT** icon in the Dock. Close the
window to shut the app (and its background server) down cleanly.

> First launch: macOS may say the app is from an unidentified developer (it's an
> unsigned, locally-built app). Right-click `LedgerTB.app` → **Open** once, and
> it will open normally thereafter.

## How it works

`desktop.py` starts Streamlit headless on a private `127.0.0.1` port, waits for
it to be ready, then shows that URL in a pywebview window. `LedgerTB.app` is a
thin macOS wrapper that runs `desktop.py` from this folder.

- **Which Python it uses:** `LedgerTB.app` tries `LEDGERTB_PYTHON` first, then
  a repo-local `.macos-venv`, then `python3` on your PATH. Set
  `LEDGERTB_PYTHON` if it picks the wrong one. `python desktop.py` just uses
  whatever `python` is on your PATH.
- **Logs:** double-click launches write output to `~/Library/Logs/LedgerTB.log`
  — check there if the window doesn't appear.
- **Data location:** unchanged from the normal app. Before storing **real**
  client data, set `LEDGERTB_DB_PATH` to a location your firm's encrypted
  backup covers (see `config.py`).
- **Backup location:** defaults to the app data directory. Override it with
  `LEDGERTB_BACKUP_DIR`; the in-app Data Safety page shows the active database
  and verified-backup status.

Existing ProBooks installations are detected in their previous application-data
folder and used in place; LedgerTB does not move financial data automatically.
The old `PROBOOKS_PYTHON`, `PROBOOKS_DB_PATH`, and `PROBOOKS_BACKUP_DIR`
environment names remain accepted during the transition.

## Regenerating the icon

The Dock icon is `LedgerTB.app/Contents/Resources/LedgerTB.icns`, built from
`scripts/make_icon.py`:

```bash
python scripts/make_icon.py
```

---

# Standalone app (no Python required)

The above `LedgerTB.app` is a thin wrapper that still needs Python + the deps
installed. You can also build a **fully standalone** `LedgerTB.app` that bundles
Python and every dependency — installable on a Mac with no Python at all (e.g.
to hand to a colleague).

## Build

On Apple Silicon, use the committed Python 3.12 lock for a repeatable build:

```bash
python3.12 -m venv .macos-venv
.macos-venv/bin/pip install -r requirements-macos-arm64.lock
source .macos-venv/bin/activate
./scripts/build_release.sh
```

`requirements-macos-arm64.lock` is architecture-specific because the desktop
bundle includes native PyObjC, NumPy, SQLCipher, and PyInstaller components.
The build script verifies the installed versions before running tests and
packaging. Intel Macs need a separately generated lock.

For a tested, repeatable local release, use the wrapper instead:

```bash
./scripts/build_release.sh
./scripts/install_local.sh   # explicit: copies to /Applications
```

After installation, open LedgerTB from Applications, Control-click its Dock
icon, and choose **Options → Keep in Dock**.

Output: `dist/LedgerTB.app` (~530 MB — it bundles Python, Streamlit, pandas,
etc.). Double-click it, or `open dist/LedgerTB.app`.

The spec trims the bundle by excluding heavy libraries the app never uses
(scipy, scikit-learn, Qt, LLVM, matplotlib, the Jupyter stack, …) — see
`EXCLUDES` in `LedgerTB.spec`. `LEDGERTB_MODE=selfcheck dist/LedgerTB.app/Contents/MacOS/LedgerTB`
imports every runtime dependency and reports, to confirm a trim didn't drop
anything.

## How the standalone build works

- `run_ledgertb.py` is the entry point. The one binary runs in two modes: the
  parent shows the pywebview window; it re-launches *itself* in `server` mode
  (a child process) to run Streamlit as that process's main thread.
- `LedgerTB.spec` bundles the app source (`app.py`, `pages/`, `models/`,
  `services/`, `database/` incl. the migration `.sql`, `utils/`, `.streamlit/`)
  as data, plus Streamlit and all deps.
- **Data location (frozen):** because the app bundle is read-only, a standalone
  build keeps its database and saved API key in
  `~/Library/Application Support/LedgerTB/`. (Running from source still uses the
  repo `data/` folder.) Set `LEDGERTB_DB_PATH` to a location your firm's
  encrypted backup covers before storing real client data, as always.
- **First open (unsigned build):** a plain build is ad-hoc signed, so macOS
  Gatekeeper will warn. Right-click `dist/LedgerTB.app` → **Open** once. Fine for
  personal use.

`build/` and `dist/` are gitignored (build artifacts); the spec, entry point,
and signing scripts are the committed source.

## Signing + notarization (distribute without warnings)

To hand the app to others with **no** Gatekeeper warning, sign it with an Apple
**Developer ID** and notarize it. This needs an Apple Developer Program
membership ($99/yr) — everything else is wired up:

- `scripts/entitlements.plist` — the Hardened Runtime entitlements a bundled
  Python needs (JIT / unsigned executable memory / library validation off /
  outbound network).
- `LedgerTB.spec` signs the bundle **during the build** (inside-out, hardened
  runtime) when `LEDGERTB_CODESIGN_ID` is set.
- `scripts/notarize.sh` verifies, submits to Apple's notary service, and staples
  the ticket.

One-time setup and the two commands are documented at the top of
`scripts/notarize.sh`. In short:

```bash
# after a one-time cert + notarytool credential setup:
LEDGERTB_CODESIGN_ID="Developer ID Application: Your Name (TEAMID)" \
  pyinstaller LedgerTB.spec --noconfirm
./scripts/notarize.sh
```
