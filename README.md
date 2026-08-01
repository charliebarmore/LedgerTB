# ProBooks

Double-entry bookkeeping you can read, run, and change. A Streamlit app built by a practicing CPA to keep real books: multiple clients, journal entries with debit-credit validation, bank imports, AI-assisted categorization, and the standard reports.

Built with Claude Code by [Charlie Barmore, CPA](https://cbarmorecpa.com). Shared with the [AI Lab for Accountants](https://ailabforaccountants.com) community as a working example of what one accountant can build.

## What it does

- **Clients**: separate books per client, with chart-of-accounts templates by entity type (S corp, partnership, nonprofit, and more) and industry.
- **Journal entries**: classic double-entry with validation. If it doesn't balance, it doesn't post.
- **Bank imports**: bring in transactions from CSV, with duplicate detection and an import verification step before anything touches the books.
- **AI categorization**: Claude suggests the account for each imported transaction and learns your patterns over time. Suggestions only: you review, you post. The audit trail records what happened either way.
- **Reports**: trial balance, income statement, balance sheet, and general ledger, plus a trial balance worksheet and bank reconciliation.
- **Audit trail**: every change is logged. Bookkeeping without an audit trail is just a spreadsheet with opinions.
- **Data safety**: the database is encrypted at rest behind a launch passphrase (SQLCipher), backups are built in, and there is a dedicated Data Safety page in the app. If SQLCipher isn't installed, the app still runs, unencrypted, and says so on every page.

## Quickstart

Requires Python 3.12 (what it's tested on).

```bash
git clone <this repo>
cd ProBooks
pip install -r requirements.txt
streamlit run app.py
```

Verified on a clean macOS install (Python 3.12.7, fresh venv, nothing preinstalled): `pip install -r requirements.txt` pulls a prebuilt `sqlcipher3` wheel and needs no Homebrew step.

If your platform has no wheel and the `sqlcipher3` build fails, you need the SQLCipher system library (macOS: `brew install sqlcipher`, Debian/Ubuntu: `libsqlcipher-dev`) — or drop that line from `requirements.txt` and run anyway. The app falls back to an unencrypted database and says so on every page. Fine for evaluating with sample data; put SQLCipher back before keeping real books.

The app runs fully without any API key. To turn on AI categorization, either set `ANTHROPIC_API_KEY` in a `.env` file or paste a key on the Import page (stored in your system keychain, not in a file).

There is also a packaged desktop build; see `DESKTOP.md`.

## The posture

This tool follows the same rule we teach in the Lab: AI drafts, the professional decides. Categorization suggestions are never auto-posted, everything is reviewable, and the audit trail keeps the record. Use it with your own or sample data first, and never put client data into any tool until you have vetted where that data goes (here, the only outbound call is the optional Anthropic API for categorization; the books themselves stay in a local encrypted database).

## Tests

```bash
python -m pytest
```

283 tests cover the ledger math, posting rules, imports, reports, and export hardening. They pass on both pandas 2.2 and pandas 3.0.

## License

MIT. See `LICENSE`.
