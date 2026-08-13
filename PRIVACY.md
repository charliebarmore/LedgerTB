# LedgerTB Privacy & Data Practices

Effective: August 13, 2026

This notice explains the data practices of LedgerTB's official desktop builds,
the LedgerTB marketing website, and the public LedgerTB repository maintained
by Ledger Labs LLC. It does not govern third-party websites or services.

## The short version

- LedgerTB has no Ledger Labs cloud account or hosted bookkeeping database.
- Official release builds store books in a SQLCipher-encrypted database at a
  location you choose. A source build without SQLCipher warns that it is
  operating unencrypted and should not be used for real books.
- Ledger Labs LLC does not receive your book merely because you use LedgerTB.
- LedgerTB does not include product analytics, advertising trackers, or usage
  telemetry controlled by Ledger Labs LLC.
- Optional AI and MCP features can disclose selected book data to providers you
  configure. Those providers' terms and privacy practices apply.

## Data stored by the desktop application

LedgerTB stores bookkeeping records, client details, attachments, audit history,
branding, settings, and related information in the active book file. You choose
whether that file lives in LedgerTB's local data folder or another location,
including shared storage. Backups and exports are written to locations you
choose and remain under your control.

Official release builds encrypt book files and LedgerTB-created backups at rest
with SQLCipher. The passphrase is not sent to Ledger Labs LLC. If you choose
"Remember on this computer," a derived unlock key is stored in the operating
system credential vault. If you enable MCP assistant access, a book-specific
derived key, book identity, access level, and approved export folder are stored
in that credential vault so the local MCP process can enforce your choices.

Ledger Labs LLC cannot recover a lost passphrase or retrieve a book stored on
your device or storage system.

## Optional AI categorization and document parsing

LedgerTB operates without an AI API key. If you enable Anthropic-powered
features, LedgerTB sends the data described in the consent or explanatory text
shown beside that feature. Depending on the feature, that may include dates,
descriptions, amounts, account names and numbers, transaction details, or text
extracted from a statement. Your Anthropic API key is stored in the operating
system credential vault when saved in the app.

Those requests are made directly from your computer to Anthropic and are
governed by your Anthropic account, agreement, and selected service settings.
Ledger Labs LLC does not receive those requests.

## Optional MCP assistant access

MCP access is off until you enable it separately for a book. LedgerTB's MCP
server uses local standard input/output transport and does not listen on a
network port. However, the MCP client you connect—such as Claude Desktop,
Claude Code, or another client—may send tool definitions, requests, and data
returned by LedgerTB to the AI provider configured in that client.

Before enabling MCP for client data, review and approve the MCP client and AI
provider, including their retention, training, confidentiality, security,
location, and incident-response terms. Changing or disabling access takes
effect on the assistant's next LedgerTB tool call.

## Marketing website and downloads

The LedgerTB marketing site is a static site hosted by Cloudflare Pages. Ledger
Labs LLC does not add analytics scripts, advertising pixels, account systems,
or contact forms to that site. Cloudflare may process ordinary web-request data
such as IP address, browser information, requested URL, timestamps, and security
signals to deliver and protect the site under Cloudflare's own privacy terms.
The site requests fonts from Google Fonts, so the visitor's browser may connect
to Google when loading a page.

Source code and release downloads are hosted by GitHub. When you visit GitHub
or download a release, GitHub's terms and privacy practices apply. Links to
Ledger Labs, AI Lab for Accountants, Anthropic, or other sites take you to
services with their own policies.

## Messages and security reports

If you email Ledger Labs LLC, submit a security report, or otherwise contact us,
we receive the address, message, attachments, and technical or contact details
you choose to provide. We use that information to respond, investigate, secure
LedgerTB, maintain necessary records, and comply with law. Do not include live
credentials or client records in an initial report.

We retain correspondence only as long as reasonably necessary for those
purposes, subject to legal, security, and recordkeeping needs.

## Selling, advertising, and children

Ledger Labs LLC does not sell personal information collected through LedgerTB
or use it for cross-context behavioral advertising. LedgerTB is intended for
business and professional use and is not directed to children.

## Your choices and responsibilities

You control the book location, backups, exports, AI key, MCP enablement, access
level, approved export folder, and connected providers. You can disable MCP and
remove saved credentials from LedgerTB. Removing the app does not remove book
files stored in your user profile or another location.

You are responsible for having authority to process client and personal data,
providing any notices required by your firm or applicable law, configuring
third-party providers appropriately, and responding to access, deletion,
retention, or other rights relating to data you control.

## Changes and contact

This notice may change as LedgerTB changes. Material revisions will update the
effective date in the repository and on the website.

Privacy questions or requests concerning information held by Ledger Labs LLC
may be sent to **info@ledgerlabs.co**. Because Ledger Labs LLC generally does
not receive desktop book data, requests concerning a local book must ordinarily
be handled by the person or firm controlling that book.
