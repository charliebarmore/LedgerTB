# ProBooks — project instructions

Desktop double-entry bookkeeping for CPA client work. Streamlit UI in a native
window, SQLCipher-encrypted SQLite, local-first: no server, no accounts, no
custody of anyone's data. Built and maintained with Claude Code.

## Architecture

- `pages/` — numbered Streamlit pages (the app). `app.py` is the entry.
- `models/` — active-record dataclasses over SQL (journal entries, accounts,
  clients, audit log, reports, draft entries, assistant review marks).
- `services/` — workflows: csv_import, categorization (Anthropic tool-use),
  book_review, close_package, branding, backups, mcp_tools.
- `database/` — connection (keying, `READ_ONLY` pin), crypto (PBKDF2 →
  SQLCipher raw key), tracked migrations in `database/migrations/`.
- `utils/` — unlock gate (passphrase + book chooser), books/book_lock (firm
  mode), secure_store (OS credential vault), ui (statement/ledger renderers),
  client_selector (sidebar + nav).
- `mcp_server.py` — MCP server (stdio) enforcing the leveled assistant dial
  (read / propose / post — see invariants); `run_probooks.py` — frozen
  entry (`PROBOOKS_MODE`: server / selfcheck / mcp).

## Non-negotiable invariants

- **Money is integer cents in core** (`money.to_cents`/`to_dollars`); dollars
  only at the presentation edge.
- **Unbalanced entries never post.** Validation lives in the model, not the UI.
- **Every mutation writes the audit trail**, stamped with the OS user.
- **The database stays encrypted.** New code paths must work through
  `database.connection` (which keys every connection); never open the file
  directly. The release pipeline refuses to ship if encryption is unavailable.
- **Assistant access is a leveled dial, engine-enforced** —
  `dbconn.ASSISTANT_ACCESS_LEVEL` ("read" / "propose" / "post") scopes an
  authorizer on every connection. read: SELECT + audit_log INSERT.
  propose: + INSERT on the inboxes (`draft_entries`,
  `imported_transactions`) and setup tables (`clients`, `accounts` —
  scaffold, never alter), UPDATE on `draft_entries` only. post: + INSERT
  on `journal_entries`/`journal_entry_lines` — **append-only; UPDATE and
  DELETE are never grantable at any level.** The level and export folder
  live in the OS vault, outside the assistant's reach. Read-only book
  sessions use `dbconn.READ_ONLY` (`PRAGMA query_only`).
- **Assistant work is always attributed and reviewable.** The MCP
  process calls `utils.actor.mark_as_assistant()` so every stamp it
  writes reads "<user> (AI)"; the Assistant Review page queues those
  rows for an append-only, audit-logged human sign-off
  (`models/assistant_review.py`). Never bypass the actor stamp.
- **AUDIT_ACTIONS and the audit_log CHECK must move together.** The
  table's CHECK constraint is frozen at migration time — adding an
  action to `models/audit_log.AUDIT_ACTIONS` without a table-rebuild
  migration makes every write of that action an IntegrityError
  (Book Review's REVIEW events failed silently until migration 015).
- **Staged imports keep full import identity.** Assistant-staged rows
  carry fingerprints/idempotency keys like any CSV row; `posting.py`
  ADOPTS a Pending, entry-less idempotency match (same record goes
  Pending → Posted) and its duplicate check counts only rows that
  actually posted. Preserve both properties when touching posting.
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
- Process-global state needs autouse resets: `utils.actor._ASSISTANT`
  (set by mcp_server's vault unlock) leaked "(AI)" stamps across tests
  until conftest reset it per test. Anything a server-mode entry point
  mutates at module scope needs the same treatment.
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
- **Windows zips are not a distribution channel.** Explorer marks every
  extracted file internet-sourced, .NET then refuses to load the bundled
  `Python.Runtime.dll`, and pywebview dies before the window opens. Ship the
  Inno Setup installer (`scripts/probooks.iss`); it writes files itself and
  nothing gets tagged. Code signing does **not** fix this — different
  mechanism from SmartScreen.
- **CI cannot see mark-of-the-web**: the runner never downloads its own
  artifact. Only a browser download plus Explorer extraction reproduces it.
  Equally, `gh run download` strips it — a scripted download proves nothing
  about what a member sees.
- **Pin the Windows deps** (`requirements-windows.lock`). Open ranges shipped a
  build that 500'd on every request: starlette 1.4.0 made a keyword argument
  required that Streamlit 1.61.0 did not pass (Streamlit 1.61.1 later capped it
  at `starlette<1.4.0`). Same commit, different day, different app.
- **selfcheck proves imports, not behavior.** That starlette break passed
  selfcheck. `scripts/smoke_serve.ps1` starts the built exe and fetches real
  routes **with `Accept-Encoding: gzip`** — the bug was in the gzip responder,
  and it did not fire on every route (`GET /` returned 200 while
  `/_stcore/health` returned 500). Ask for compression on more than one route.
- **ASCII only in `.ps1` files.** Windows PowerShell 5.1 reads a BOM-less file
  as ANSI, so a UTF-8 em dash decodes into bytes including a stray `"` that
  swallows the rest of the script. CI runs pwsh 7 and would never notice.
- Inno Setup lands in different places from chocolatey vs winget — discover
  `ISCC.exe`, don't hardcode its path. And keep install paths short; a deep
  target blows the 260-char limit mid-copy and Inno aborts with exit code 5.

## Writing style for user-facing text

Plain sentences, no jargon the user didn't introduce. Warnings say what will
happen and what to do, not what module failed. An accountant is the reader.
