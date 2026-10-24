# Running LedgerTB as a desktop app

LedgerTB can run in its own native window (no browser tab, no terminal) via
`pywebview`. Everything stays local — this only changes how the app is launched
and displayed.

## v1.8.1 candidate verification (2026-10-10)

The candidate adds Ollama import-review suggestions and fixes dollar amounts in
assistant explanations and Close Map account selection during sequential signoff.
It is not a published release.

- Full suite: **944 passed, 5 Windows-only skips**, including performance checks.
- The locked Apple Silicon build passed the 56-module SQLCipher/import/OCR
  selfcheck and strict ad hoc signature verification.
- Source and frozen-server browser acceptance passed currency rendering,
  consecutive signoffs, provider settings, consent, local HTTP requests,
  response reuse, suggestion acceptance without posting, and error handling.
- The frozen check matched 121 bundled source/migration files. It used a
  separately copied bundle with a fake vault and deterministic Ollama responses;
  this verifies the request workflow, not model accuracy.

Reproduce the checks using [TESTING.md](docs/TESTING.md). Native-window and
installed-upgrade acceptance, Developer ID signing, notarization and Windows
qualification remain separate release steps. Never distribute a bundle modified
with a test vault.

## One-time setup

For Linux, use the pinned setup below. The remaining build instructions describe
the existing macOS and Windows distributions.

Install the desktop dependency into the same Python that runs LedgerTB:

```bash
python -m pip install -r requirements-desktop.txt
```

(If LedgerTB runs under a different Python than the one on your PATH, install
there instead and set `LEDGERTB_PYTHON` — see below.)

## Linux source pilot

Use a checkout of the candidate being tested. This is a source installation,
not a standalone Linux release. Python 3.12 and 3.14 are the pilot targets.
The Python lock is shared across those targets; GTK, WebKitGTK, PyGObject and
Pycairo are supplied by the distribution and must match its system Python.
Do not use a separately downloaded Python with the distribution's bindings.

On an up-to-date Omarchy/Arch installation:

```sh
sudo pacman -S --needed python python-gobject python-cairo gtk3 webkit2gtk-4.1
```

On Ubuntu 24.04:

```sh
sudo apt-get update
sudo apt-get install -y python3-venv python3-dev python3-gi python3-gi-cairo \
  gir1.2-gtk-3.0 gir1.2-webkit2-4.1 libsqlcipher-dev build-essential
```

From the checkout, create a separate pilot environment. System-site access is
needed for the distribution's GTK bindings; the application's Python packages
are explicitly pinned and checked:

```sh
/usr/bin/python3 -m venv --system-site-packages .linux-venv
. .linux-venv/bin/activate
python -m pip install -r requirements-linux.lock
python -m pip check
python scripts/verify_lock.py requirements-linux.lock
python -c 'import gi; gi.require_version("Gtk", "3.0"); gi.require_version("WebKit2", "4.1"); from gi.repository import Gtk, WebKit2; print("GTK/WebKit available")'
python scripts/smoke_linux.py
python desktop.py
```

The smoke check opens and closes a temporary native window, unlocks a synthetic
encrypted book, checks that its client renders, and confirms the server exits.
It uses a test-only in-memory credential store and leaves existing books alone.
The final command launches the normal application.

Saved provider keys and assistant settings use the desktop's credential service.
Check the selected backend without reading any stored values:

```sh
python -c 'import keyring; b = keyring.get_keyring(); print(type(b).__module__, type(b).__name__)'
```

A `keyring.backends.fail` backend means that a supported credential service is
unavailable in this login session. Configure the desktop's Secret Service wallet
before testing saved credentials. Do not substitute a plaintext keyring.

### Pilot acceptance

Use a new synthetic book for the first device run. Record the Git commit, Python
version, distribution version, GTK/WebKit versions and credential backend.

1. Create an encrypted book, close the app, and unlock it again.
2. Import a CSV, accept a category while leaving posting excluded, save the
   review, and reopen it. Explicitly include and post the intended rows once.
3. Create a balanced journal entry. Confirm an unbalanced entry is refused.
4. Prepare and review two Close Map accounts. Confirm both signoffs persist.
5. Export a PDF and spreadsheet through the native save dialog. Open both files.
6. Make and verify a backup, then restore it into a separate test book.
7. Save a provider setting through Firm Settings, close, and reopen. Confirm
   the setting survives through the real credential service.
8. Check keyboard navigation, file pickers and scaling under the normal Wayland
   session. Close the window and confirm no LedgerTB server remains running.

Automated GTK acceptance on a virtual X11 display does not establish these
Wayland, file-dialog or real-vault results. Follow [the workflow testing guide](docs/TESTING.md)
for accounting and recovery evidence. Linux packaging and broader distribution
support remain separate qualification work.

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

> If the window stops appearing after an update: on Apple Silicon, macOS
> remembers how it started this app the first time, and older copies started it
> in Intel compatibility mode. Pulling the fix does not clear that memory, so
> the log still shows an architecture error. Run `touch LedgerTB.app` in the
> project folder once, then open it again. A fresh copy of the project is not
> affected.

## How it works

`desktop.py` starts Streamlit headless on a private `127.0.0.1` port, waits for
it to be ready, then shows that URL in a pywebview window. `LedgerTB.app` is a
thin macOS wrapper that runs `desktop.py` from this folder.

- **Which Python it uses:** `LedgerTB.app` tries `LEDGERTB_PYTHON` first, then
  a repo-local `.macos-venv`, then `python3` on your PATH. Set
  `LEDGERTB_PYTHON` if it picks the wrong one. `python desktop.py` just uses
  whatever `python` is on your PATH.
- **Logs:** double-click launches write output to `Library/Logs/LedgerTB.log` inside your home folder
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
  `Library/Application Support/LedgerTB/` inside your home folder. (Running from source still uses the
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
