# ProBooks — project instructions

Desktop double-entry bookkeeping for CPA client work. Streamlit UI in a native
window, SQLCipher-encrypted SQLite, local-first: no server, no accounts, no
custody of anyone's data. Built and maintained with Claude Code.

## Architecture

- `pages/` — numbered Streamlit pages (the app). `app.py` is the entry.
- `models/` — active-record dataclasses over SQL (journal entries, accounts,
  clients, audit log, reports).
- `services/` — workflows: csv_import, categorization (Anthropic tool-use),
  book_review, close_package, branding, backups, mcp_tools.
- `database/` — connection (keying, `READ_ONLY` pin), crypto (PBKDF2 →
  SQLCipher raw key), tracked migrations in `database/migrations/`.
- `utils/` — unlock gate (passphrase + book chooser), books/book_lock (firm
  mode), secure_store (OS credential vault), ui (statement/ledger renderers),
  client_selector (sidebar + nav).
- `mcp_server.py` — read-only MCP server (stdio); `run_probooks.py` — frozen
  entry (`PROBOOKS_MODE`: server / selfcheck / mcp).

## Non-negotiable invariants

- **Money is integer cents in core** (`money.to_cents`/`to_dollars`); dollars
  only at the presentation edge.
- **Unbalanced entries never post.** Validation lives in the model, not the UI.
- **Every mutation writes the audit trail**, stamped with the OS user.
- **The database stays encrypted.** New code paths must work through
  `database.connection` (which keys every connection); never open the file
  directly. The release pipeline refuses to ship if encryption is unavailable.
- **MCP stays read-only** — enforced by `PRAGMA query_only` on every
  connection when `dbconn.READ_ONLY` is set, not by tool design alone.
- **Schema changes are new numbered migrations**; never edit an existing one.

## Commands

- Run (browser): `streamlit run app.py` · desktop window: `python desktop.py`
- Tests: `python -m pytest -q -m "not performance"` (full suite ~30s;
  `-m performance` for the volume tripwires)
- Release build + gate (tests → build → selfcheck → sign → verify):
  `./scripts/build_release.sh` (signing configured in gitignored
  `scripts/signing.env`; ad-hoc without it)
- Install locally: `./scripts/install_local.sh`
- Windows build: CI only (`.github/workflows/windows-build.yml` spike,
  `release.yml` on `v*` tags → draft GitHub Release)

## Testing rules

- Tests must **never touch the real OS keychain** — an autouse fake-vault
  fixture covers `utils.secure_store`; opt out only with the `real_vault`
  marker (those tests stub `keyring` directly).
- Tests use a throwaway DB via `tests/conftest.py` fixtures (`client_id`,
  `accounts`, `post_entry`); the fixture keys the process so the unlock gate
  passes transparently.
- Page tests use Streamlit `AppTest`; monkeypatch `render_client_selector`
  and `st.page_link` first. Pages gated by a view switcher need its session
  key set before `.run()`.
- **AppTest has no frontend**: `del st.session_state[key]` bugs (the browser
  re-imposes keyed widget values) pass AppTest and need a real-browser check.
  The only reset the frontend honors is a new widget key (generation nonce —
  see `pages/2_Journal_Entries.py`).
- No real client or vendor data in fixtures — invent names.

## Packaging gotchas (learned the hard way)

- Pages/services are **data files, invisible to PyInstaller's analyzer** —
  third-party libs they import need `collect_submodules` in `ProBooks.spec`
  AND an entry in `run_probooks.py`'s selfcheck module list.
- strftime `%-d`/`%-I` are libc extensions that crash on Windows — use
  `utils/dates.py`.
- `upload-artifact` strips dot-files by default; `.streamlit/` must ship
  (`include-hidden-files: true`, and the release job asserts it).
- A GUI-subsystem exe on Windows: `&` in PowerShell neither waits nor gets an
  exit code — use `Start-Process -Wait -PassThru`. A no-console child dies on
  stdio writes — give it a log file and `CREATE_NO_WINDOW`.
- Sign the finished bundle in **one** `codesign --deep` pass; per-binary
  signing during the build fails intermittently (`errSecInternalComponent`).
- Keychain ACLs key on the code signature: unsigned/ad-hoc builds lose vault
  items on every reinstall. Keep the Developer ID signature stable.

## Writing style for user-facing text

Plain sentences, no jargon the user didn't introduce. Warnings say what will
happen and what to do, not what module failed. An accountant is the reader.
