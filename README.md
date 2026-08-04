# ProBooks

Double-entry bookkeeping you can read, run, and change. A desktop app built by a practicing CPA to keep real books: multiple clients, journal entries with debit-credit validation, bank imports, AI-assisted categorization, standard reports, and a close workflow that ends in a branded PDF package.

Built with Claude Code by [Charlie Barmore, CPA](https://cbarmorecpa.com). Shared with the [AI Lab for Accountants](https://ailabforaccountants.com) community as a working example of what one accountant can build.

## What it does

- **Clients**: separate books per client, with chart-of-accounts templates by entity type (S corp, partnership, nonprofit, and more) and industry.
- **Journal entries**: classic double-entry with validation. If it doesn't balance, it doesn't post.
- **Bank imports**: bring in transactions from CSV with saved per-bank formats, duplicate detection, and an import verification step (including row-continuity checks — a balanced trial balance is *not* proof an import was complete). New accounts can be created right in the category dropdown.
- **AI categorization**: Claude suggests the account for each imported transaction and learns your patterns over time. Suggestions only: you review, you post. The audit trail records what happened either way.
- **Book Review**: a deterministic integrity sweep (unbalanced entries, unposted imports, broken links, date problems, quiet accounts) plus an AI category-consistency review governed by your own per-client policy notes, and an analytical memo.
- **Reports & the close**: trial balance, income statement, balance sheet, general ledger (whole-book view), trial balance worksheet, bank reconciliation — and an exportable **close package** (PDF + Excel) carrying your firm's branding from Firm Settings.
- **Assistant access (MCP)**: opt-in MCP server so Claude Desktop or Claude Code can query your books — trial balance, ledgers, entry search, the integrity sweep — and file **draft entries** that only a human can approve. The ledger itself is unreachable to the assistant, enforced by the database engine, not by promises. See `docs/MCP.md`.
- **Firm mode**: book files can live on a shared drive, ProSystem-style — the app installs locally, each book has its own passphrase, and an in-use lock keeps two writers out of one book. See `docs/FIRM-MODE.md`.
- **Audit trail**: every change is logged, with the OS account name as the actor. Bookkeeping without an audit trail is just a spreadsheet with opinions.
- **Data safety**: the database is encrypted at rest behind a launch passphrase (SQLCipher), verified backups are built in, and a production-readiness checklist gates real use. If SQLCipher isn't installed, the app still runs, unencrypted, and says so on every page.

## Quickstart

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

- **macOS**: a signed standalone `ProBooks.app` — build with `./scripts/build_release.sh` (see `DESKTOP.md`; signing is configured via a local `scripts/signing.env`).
- **Windows**: a standalone `ProBooks.exe` built by CI (`.github/workflows/release.yml`, tag-triggered). The release pipeline refuses to ship a build whose encryption is unavailable.

## The posture

This tool follows the same rule we teach in the Lab: AI drafts, the professional decides. Categorization suggestions are never auto-posted, everything is reviewable, and the audit trail keeps the record. Assistant access via MCP is off by default, opt-in, and read-only at the database level — an assistant can analyze the books but cannot post, edit, or delete. Use the app with your own or sample data first, and never put client data into any tool until you have vetted where that data goes (here, the only outbound call is the optional Anthropic API for categorization; the books themselves stay in a local encrypted database).

## Tests

```bash
python -m pytest
```

299 tests cover the ledger math, posting rules, imports, reports, the MCP tools, firm-mode locking, and export hardening. They pass on macOS and Windows, and on both pandas 2.2 and pandas 3.0.

## More documentation

- `DESKTOP.md` — desktop builds, packaging, notarization
- `docs/MCP.md` — assistant access setup and security model
- `docs/FIRM-MODE.md` — shared-drive book files and the in-use lock
- `docs/WINDOWS-TESTING.md` — the Windows smoke-test checklist
- `PERFORMANCE.md` — performance baselines

## License

MIT. See `LICENSE`.
