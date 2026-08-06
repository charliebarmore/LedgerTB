# ProBooks

Double-entry bookkeeping you can read, run, and change. A desktop app built by a practicing CPA to keep real books: multiple clients, journal entries with debit-credit validation, bank imports, AI-assisted categorization, standard reports, and a close workflow that ends in a branded PDF package.

A **Ledger Labs LLC** product — the software studio of [Charlie Barmore, CPA](https://cbarmorecpa.com), who built this with Claude Code. Shared with the [AI Lab for Accountants](https://ailabforaccountants.com) community as a working example of what one accountant can build.

## What it does

- **Clients**: separate books per client, with chart-of-accounts templates by entity type (S corp, partnership, nonprofit, and more) and industry.
- **Journal entries**: classic double-entry with validation. If it doesn't balance, it doesn't post.
- **Bank imports**: bring in transactions from CSV with saved per-bank formats, duplicate detection, and an import verification step (including row-continuity checks — a balanced trial balance is *not* proof an import was complete). New accounts can be created right in the category dropdown. **Or skip the format question entirely**: hand any statement — CSV, PDF, a pasted table — to your assistant, which normalizes and stages it into the same review flow (see Assistant access below).
- **AI categorization**: Claude suggests the account for each imported transaction and learns your patterns over time. Suggestions only: you review, you post. The audit trail records what happened either way.
- **Book Review**: a deterministic integrity sweep (unbalanced entries, unposted imports, broken links, date problems, quiet accounts) plus an AI category-consistency review governed by your own per-client policy notes, and an analytical memo.
- **Reports & the close**: trial balance, income statement, balance sheet, general ledger (whole-book view), trial balance worksheet, bank reconciliation — and an exportable **close package** (PDF + Excel) carrying your firm's branding from Firm Settings.
- **Assistant access (MCP)**: opt-in MCP server so Claude Desktop or Claude Code can query your books — trial balance, ledgers, entry search, the integrity sweep — file **draft entries**, **stage imports from any statement format**, and — only if you turn the dial up — post balanced entries itself. **You choose the access level** (read / propose / post) on Data Safety, the setting lives outside the assistant's reach, and every level is enforced by the database engine, not by promises: even at full access the assistant is append-only and can never edit or delete anything. See `docs/MCP.md`.
- **Firm mode**: book files can live on a shared drive, ProSystem-style — the app installs locally, each book has its own passphrase, and an in-use lock keeps two writers out of one book. See `docs/FIRM-MODE.md`.
- **Audit trail**: every change is logged, with the OS account name as the actor. Bookkeeping without an audit trail is just a spreadsheet with opinions.
- **Data safety**: the database is encrypted at rest behind a launch passphrase (SQLCipher), verified backups are built in, and a production-readiness checklist gates real use. If SQLCipher isn't installed, the app still runs, unencrypted, and says so on every page.

## Download

Grab the latest from the [Releases page](../../releases/latest).

**Windows** — download `ProBooks-windows-x64-setup.exe` and run it. Because the
build is not yet code-signed, Windows shows a SmartScreen warning ("Windows
protected your PC"): click **More info**, then the button to run it anyway
(Windows labels it *Run anyway* or *Open anyway* depending on version). It
installs for you alone, under your own user profile, and never asks for an
administrator password — so it works on a locked-down firm laptop. You get a
Start Menu entry and a normal entry in Add/Remove Programs.

A `ProBooks-windows-x64.zip` is also attached for anyone who needs to deploy
without an installer. **Read this before using it:** when Windows extracts a
downloaded zip it marks every file as coming from the internet, and that stops
part of ProBooks loading — the app will refuse to start and tell you so. To use
the zip, right-click it → **Properties** → tick **Unblock** → **OK**, *then*
extract. The installer has none of this friction and is the supported path.

**macOS** — no packaged Mac download yet; the release pipeline currently builds
Windows only. Build it locally with `./scripts/build_release.sh` (see
`DESKTOP.md`), or run from source as below.

The app is self-contained — no Python and no dependencies to install. Your books
live in an encrypted database under your user profile, never inside the app
folder, so upgrading keeps your data and uninstalling does not delete it.

## Quickstart (from source)

Requires Python 3.12 (what it's tested on).

```bash
git clone <this repo>
cd ProBooks
pip install -r requirements.txt
streamlit run app.py
```

Verified on a clean macOS install (Python 3.12.7, fresh venv, nothing preinstalled): `pip install -r requirements.txt` pulls a prebuilt `sqlcipher3` wheel and needs no Homebrew step. The same is true on Windows x64.

If your platform has no wheel and the `sqlcipher3` build fails, you need the SQLCipher system library (macOS: `brew install sqlcipher`, Debian/Ubuntu: `libsqlcipher-dev`) — or drop that line from `requirements.txt` and run anyway. The app falls back to an unencrypted database and says so on every page. Fine for evaluating with sample data; put SQLCipher back before keeping real books.

The app runs fully without any API key. To turn on AI categorization, either set `ANTHROPIC_API_KEY` in a `.env` file or save a key on the **Firm Settings** page (stored in your system credential vault, not in a file).

## Desktop builds

- **macOS**: a signed standalone `ProBooks.app` — build with `./scripts/build_release.sh` (see `DESKTOP.md`; signing is configured via a local `scripts/signing.env`). Not yet automated in CI, so no macOS release asset is published.
- **Windows**: an Inno Setup installer built by CI (`.github/workflows/release.yml`, tag-triggered) from `scripts/probooks.iss`. The release pipeline refuses to ship a build whose encryption is unavailable, installs the pinned set in `requirements-windows.lock`, and will not publish a build that cannot serve a page (`scripts/smoke_serve.ps1`).

## The posture

This tool follows the same rule we teach in the Lab: AI drafts, the professional decides. Categorization suggestions are never auto-posted, everything is reviewable, and the audit trail keeps the record. Assistant access via MCP is off by default and permissioned at **read / propose / post**; the default is propose, while direct posting requires an explicit warning and confirmation. The database always blocks assistant edits and deletes. ProBooks itself makes an outbound call only when you enable Anthropic categorization. If you enable MCP, your MCP client may also send returned book data to its configured AI provider—vet that provider and your firm's data policy before using client data. The books remain in the local encrypted database.

## Tests

```bash
python -m pytest
```

More than 300 tests cover the ledger math, posting rules, imports, reports, the MCP tools, firm-mode locking, and export hardening. They pass on macOS and Windows, and on both pandas 2.2 and pandas 3.0.

## More documentation

- `DESKTOP.md` — desktop builds, packaging, notarization
- `docs/MCP.md` — assistant access setup and security model
- `docs/LEDGERPDF-PAIRING.md` — books-to-binder workflow with LedgerPDF
- `docs/FIRM-MODE.md` — shared-drive book files and the in-use lock
- `docs/WINDOWS-TESTING.md` — the Windows smoke-test checklist
- `PERFORMANCE.md` — performance baselines

## License

MIT. See `LICENSE`.
