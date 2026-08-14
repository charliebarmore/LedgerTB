# LedgerTB

Double-entry bookkeeping you can read, run, and change. A desktop app built by a practicing CPA to keep real books: multiple clients, journal entries with debit-credit validation, bank imports, AI-assisted categorization, standard reports, and a close workflow that ends in a branded PDF package.

A **Ledger Labs LLC** product — the software studio of [Charlie Barmore, CPA](https://cbarmorecpa.com), who built this with Claude Code. Shared with the [AI Lab for Accountants](https://ailabforaccountants.com) community as a working example of what one accountant can build.

## What it does

- **Clients**: separate books per client, with chart-of-accounts templates by entity type (S corp, partnership, nonprofit, and more) and industry — and a chart importer that speaks **QuickBooks type names** directly (Bank, Credit Card, A/R, COGS, …), so a QB export comes in whole, with unmappable rows reported rather than silently dropped. Charts with **no account numbers** (QBO's default) get numbers assigned by type range, shown before anything imports; any existing numbering scheme is kept as-is.
- **Journal entries**: classic double-entry with validation. If it doesn't balance, it doesn't post.
- **Bank imports**: bring in transactions from CSV with saved per-bank formats, duplicate detection, and an import verification step (including row-continuity checks — a balanced trial balance is *not* proof an import was complete). New accounts can be created right in the category dropdown. **Or skip the format question entirely**: hand any statement — CSV, PDF, a pasted table — to your assistant, which normalizes and stages it into the same review flow (see Assistant access below).
- **AI categorization**: Claude suggests the account for each imported transaction and learns your patterns over time. Suggestions only: you review, you post. The audit trail records what happened either way.
- **Book Review**: a deterministic integrity sweep (unbalanced entries, unposted imports, broken links, date problems, quiet accounts) plus an AI category-consistency review governed by your own per-client policy notes, and an analytical memo.
- **Close Map**: every year-end balance gets a reusable lead-sheet assignment, supporting references, variance explanation, review notes, and append-only preparer/reviewer signoffs. New fiscal years retain the mapping and show the adjacent prior year's review context, while requiring fresh evidence and signoff. A later ledger, reconciliation, or evidence change automatically reopens only the affected account. The map is included in annual close-package PDF and Excel exports.
- **Reports & the close**: trial balance, income statement, balance sheet, general ledger (whole-book view), trial balance worksheet, bank reconciliation — and an exportable **close package** (PDF + Excel). Client branding leads each package while your firm's logo, letterhead details, and identity remain visible as the preparer.
- **Assistant access (MCP)**: opt-in MCP server so Claude Desktop or Claude Code can work the currently open book — query everything (trial balance, ledgers, entry search, the integrity sweep), **set up a new client and its chart**, file **draft entries**, **stage imports from any statement format**, propose client branding text/colors for human approval, export the close package to your workpaper tool, and — only if you turn the dial up — post balanced entries itself. **Each book is authorized separately**, with its own access level and export folder on Data Safety. The setting lives outside the assistant's reach, and every level is enforced by the database engine, not by promises: even at full access the assistant is append-only and can never edit or delete anything. See `docs/MCP.md`.
- **Assistant Review**: one page gathering everything the assistant has done — proposals waiting on you, plus every AI-attributed action since your last sign-off, with an append-only, audit-logged "reviewed through here" checkpoint. Agent-proposed corrections carry a structured link to the original journal entry and present the original and proposed lines together. Approval retains the original → draft → posted-correction chain; rejection remains in the review history. The sidebar badges what's unreviewed; nothing the assistant does can look pre-approved.
- **Firm mode**: book files can live on a shared drive, ProSystem-style — the app installs locally, each book has its own passphrase, and an in-use lock keeps two writers out of one book. See `docs/FIRM-MODE.md`.
- **Audit trail**: every change is logged, with the OS account name as the actor — and assistant actions stamped **"(AI)"**, so automated work is never presented as yours. Bookkeeping without an audit trail is just a spreadsheet with opinions.
- **Data safety**: the database is encrypted at rest behind a launch passphrase (SQLCipher), verified backups are built in, and a production-readiness checklist gates real use. If SQLCipher isn't installed, the app still runs, unencrypted, and says so on every page.

## Download

Grab the latest from the [Releases page](../../releases/latest).

**Windows** — download `LedgerTB-windows-x64-setup.exe` and run it. Because the
build is not yet code-signed, Windows shows a SmartScreen warning ("Windows
protected your PC"): click **More info**, then the button to run it anyway
(Windows labels it *Run anyway* or *Open anyway* depending on version). It
installs for you alone, under your own user profile, and never asks for an
administrator password — so it works on a locked-down firm laptop. You get a
Start Menu entry and a normal entry in Add/Remove Programs.

A `LedgerTB-windows-x64.zip` is also attached for anyone who needs to deploy
without an installer. **Read this before using it:** when Windows extracts a
downloaded zip it marks every file as coming from the internet, and that stops
part of LedgerTB loading — the app will refuse to start and tell you so. To use
the zip, right-click it → **Properties** → tick **Unblock** → **OK**, *then*
extract. The installer has none of this friction and is the supported path.

**macOS** — download `LedgerTB-mac.zip`, unzip, and drag LedgerTB to
Applications. It is signed and notarized by Apple, so it opens with no
warnings. (Apple Silicon; the Mac build is produced and notarized locally
rather than by CI — see `DESKTOP.md`.)

The app is self-contained — no Python and no dependencies to install. Your books
live in an encrypted database under your user profile, never inside the app
folder, so upgrading keeps your data and uninstalling does not delete it.

### Updates and feedback

The in-app **Help & Updates** page shows the installed version and provides
safe upgrade instructions plus browser links to the latest release, guided bug
report and feature request forms, and private security-report instructions. It
does not call GitHub, check for updates in the background, or send telemetry;
GitHub opens only after the user chooses a link.

- [Latest release](../../releases/latest)
- [Report a bug](../../issues/new?template=bug_report.yml)
- [Request a feature](../../issues/new?template=feature_request.yml)
- [Security policy](../../security/policy)

### If you ran a pre-release ProBooks build

ProBooks was LedgerTB's pre-release name; it never shipped publicly. If you
tested one of those builds, everything keeps working: existing `.probooks`
book files, saved book choices, `probooks-*` backups, credential-vault
entries, and `PROBOOKS_*` environment settings are all still honored, and the
app reuses your existing data folder rather than moving financial data. New
books use the `.ledgertb` extension and new configuration uses `LEDGERTB_*`.

LedgerTB installs as its own program. On Windows, remove the old ProBooks
entry from Add/Remove Programs; on macOS, delete the old
`/Applications/ProBooks.app` once LedgerTB opens your book. Neither the
installer nor the app ever deletes book data. If assistant (MCP) access was
configured, point your assistant's configuration at the LedgerTB binary and
re-approve access from Data Safety.

## Quickstart (from source)

Requires Python 3.12 (what it's tested on).

```bash
git clone https://github.com/charliebarmore/LedgerTB.git
cd LedgerTB
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py --server.address=127.0.0.1
```

The virtual environment matters: installing into your system Python often fails
outright on current installs ("externally managed environment") and mixes
LedgerTB's dependencies into everything else you run.

Verified on a clean macOS install (Python 3.12.7, fresh venv, nothing preinstalled): `pip install -r requirements.txt` pulls a prebuilt `sqlcipher3` wheel and needs no Homebrew step. The same is true on Windows x64.

If your platform has no wheel and the `sqlcipher3` build fails, you need the SQLCipher system library (macOS: `brew install sqlcipher`, Debian/Ubuntu: `libsqlcipher-dev`) — or drop that line from `requirements.txt` and run anyway. The app falls back to an unencrypted database and says so on every page. Fine for evaluating with sample data; put SQLCipher back before keeping real books.

The app runs fully without any API key. To turn on AI categorization, either set `ANTHROPIC_API_KEY` in a `.env` file or save a key on the **Firm Settings** page (stored in your system credential vault, not in a file).

## Desktop builds

- **macOS**: download the signed and notarized Apple Silicon `LedgerTB.app` from the latest release. To build it yourself, create a clean Python 3.12 environment, install `requirements-macos-arm64.lock`, and run `./scripts/build_release.sh`. The build verifies the lock before packaging. Signing is configured via a local `scripts/signing.env`.
- **Windows**: an Inno Setup installer built by CI (`.github/workflows/release.yml`, tag-triggered) from `scripts/ledgertb.iss`. The release pipeline refuses to ship a build whose encryption is unavailable, installs the pinned set in `requirements-windows.lock`, and will not publish a build that cannot serve a page (`scripts/smoke_serve.ps1`).

## The posture

This tool follows the same rule we teach in the Lab: AI drafts, the professional decides. Categorization suggestions are never auto-posted, everything is reviewable, and the audit trail keeps the record. Assistant access via MCP is off by default and permissioned at **read / propose / post**; the default is propose, while direct posting requires an explicit warning and confirmation. The database always blocks assistant edits and deletes. LedgerTB itself makes an outbound call only when you enable Anthropic categorization. If you enable MCP, your MCP client may also send returned book data to its configured AI provider—vet that provider and your firm's data policy before using client data. The books remain in the local encrypted database.

## Tests

```bash
python -m pytest -q -m "not performance"
```

(Dropping the marker filter also runs the slower volume baselines described in
`PERFORMANCE.md`.) More than 430 tests cover the ledger math, posting rules, imports, reports, the MCP tools and access levels, the Close Map and assistant review checkpoints, firm-mode locking, and export hardening. They pass on macOS and Windows, and on both pandas 2.2 and pandas 3.0.

## More documentation

- `CONTRIBUTING.md` — development setup and contribution expectations
- `SUPPORT.md` — support scope and safe bug-reporting guidance
- `CODE_OF_CONDUCT.md` — community participation standards
- `DESKTOP.md` — desktop builds, packaging, notarization
- `docs/MCP.md` — assistant access setup and security model
- `docs/CLOSE-MAP.md` — account support, review, signoff, and stale-change rules
- `docs/LEDGERPDF-PAIRING.md` — books-to-binder workflow with LedgerPDF
- `docs/FIRM-MODE.md` — shared-drive book files and the in-use lock
- `docs/WINDOWS-TESTING.md` — the Windows smoke-test checklist
- `PERFORMANCE.md` — performance baselines
- `SECURITY.md` — private vulnerability reporting and supported versions

## What this is — and isn't

LedgerTB is software, not accounting advice. It gives you double-entry rails,
an audit trail, and review workflows, but every judgment in your books —
categorization, adjustments, what gets posted — is yours, whether you made it
directly or accepted a suggestion from the AI assistant. Nothing it produces
is a substitute for professional judgment on questions that matter. Your books
and their accuracy remain your responsibility, and the software is provided
as-is, without warranty of any kind (see `LICENSE`).

See the full [Disclaimer](DISCLAIMER.md), [Privacy & Data Practices](PRIVACY.md),
and [Website and Distribution Terms](TERMS.md). In particular, optional AI and
MCP features can send selected data to providers you configure; approve those
providers and their data practices before using client information.

## License

MIT. See `LICENSE`. Third-party components retain their own terms; see
`THIRD_PARTY_NOTICES.md`.
