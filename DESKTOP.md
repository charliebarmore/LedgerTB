# Running ProBooks as a desktop app

ProBooks can run in its own native window (no browser tab, no terminal) via
`pywebview`. Everything stays local — this only changes how the app is launched
and displayed.

## One-time setup

Install the desktop dependency into the same Python that runs ProBooks:

```bash
/opt/anaconda3/bin/python -m pip install -r requirements-desktop.txt
```

(If your Python lives elsewhere, install there instead and set `PROBOOKS_PYTHON`
— see below.)

## Launch it

- **Double-click `ProBooks.app`** in the project folder, or
- Run it from a terminal:

  ```bash
  python desktop.py
  ```

A native ProBooks window opens with the navy **PB** icon in the Dock. Close the
window to shut the app (and its background server) down cleanly.

> First launch: macOS may say the app is from an unidentified developer (it's an
> unsigned, locally-built app). Right-click `ProBooks.app` → **Open** once, and
> it will open normally thereafter.

## How it works

`desktop.py` starts Streamlit headless on a private `127.0.0.1` port, waits for
it to be ready, then shows that URL in a pywebview window. `ProBooks.app` is a
thin macOS wrapper that runs `desktop.py` from this folder.

- **Which Python it uses:** `ProBooks.app` uses `/opt/anaconda3/bin/python` by
  default. Override with the `PROBOOKS_PYTHON` env var if your environment
  differs. `python desktop.py` just uses whatever `python` is on your PATH.
- **Logs:** double-click launches write output to `~/Library/Logs/ProBooks.log`
  — check there if the window doesn't appear.
- **Data location:** unchanged from the normal app. Before storing **real**
  client data, set `PROBOOKS_DB_PATH` to a file under `~/Practice` so it lands in
  the encrypted-backup path (see `config.py`).

## Regenerating the icon

The Dock icon is `ProBooks.app/Contents/Resources/ProBooks.icns`, built from
`scripts/make_icon.py`:

```bash
python scripts/make_icon.py
```

---

# Standalone app (no Python required)

The above `ProBooks.app` is a thin wrapper that still needs Python + the deps
installed. You can also build a **fully standalone** `ProBooks.app` that bundles
Python and every dependency — installable on a Mac with no Python at all (e.g.
to hand to a colleague).

## Build

```bash
pip install -r requirements-desktop.txt      # includes pyinstaller
pyinstaller ProBooks.spec --noconfirm
```

Output: `dist/ProBooks.app` (~1.2 GB — it bundles Python, Streamlit, pandas,
etc.). Double-click it, or `open dist/ProBooks.app`.

## How the standalone build works

- `run_probooks.py` is the entry point. The one binary runs in two modes: the
  parent shows the pywebview window; it re-launches *itself* in `server` mode
  (a child process) to run Streamlit as that process's main thread.
- `ProBooks.spec` bundles the app source (`app.py`, `pages/`, `models/`,
  `services/`, `database/` incl. the migration `.sql`, `utils/`, `.streamlit/`)
  as data, plus Streamlit and all deps.
- **Data location (frozen):** because the app bundle is read-only, a standalone
  build keeps its database and saved API key in
  `~/Library/Application Support/ProBooks/`. (Running from source still uses the
  repo `data/` folder.) Set `PROBOOKS_DB_PATH` to a `~/Practice` file before
  storing real client data, as always.
- **First open:** unsigned/un-notarized, so macOS Gatekeeper will warn. Right-click
  `dist/ProBooks.app` → **Open** once. To distribute without the warning you'd
  sign + notarize with an Apple Developer ID (`codesign` + `notarytool`) — not
  needed for personal use.

`build/` and `dist/` are gitignored (build artifacts); the spec and entry point
are the committed source.
