# LedgerTB v1.7.2

Released September 7, 2026.

This update repairs downloads and report navigation in the native application,
and makes concurrent assistant calls use consistent book permissions.

## Fixes

- PDF and Excel download buttons can open the native save dialog in both the
  installed application and the source desktop launcher.
- New-window report links stay inside the native application, keeping the
  window's authorization token out of system-browser history.
- Choosing another Reports tab after a General Ledger drill-down clears the
  stale route. Reload no longer forces that drill-down back onto the screen;
  revisiting the drill URL still restores it.
- MCP tool calls run one at a time within each assistant process. Each call
  refreshes authorization before accessing the book or taking its write lock.
  A permission change during a running call applies to the next call.
- Data Safety explains why a book outside the local data folder cannot enable
  direct assistant posting.
- Close-package PDFs keep the balance-sheet grand total with the equity
  subtotal. Compact statements fit on one page, while larger statements
  continue across pages without dropping accounts.

No database migration is added. Existing books and posted entries are preserved.

Create a verified backup and close LedgerTB before installing. When upgrading
from v1.7.0, the [v1.7.1 recurring-draft recovery instructions](RELEASE-NOTES-1.7.1.md)
still apply to older pending primary drafts.

## Source-build encryption requirement

Source builds without SQLCipher now refuse to open books unless
`LEDGERTB_ALLOW_UNENCRYPTED=1` explicitly enables unencrypted demo use. Existing
source users relying on the automatic SQLite fallback must install SQLCipher
or set this variable to continue evaluating with sample data. The app keeps
showing its unencrypted-mode warning when the override is enabled.

Installed official release builds already include SQLCipher and are unaffected
by this default change. The override does not decrypt existing books or bypass
their passphrases. The release selfcheck still requires SQLCipher, regardless
of the demo override.

## Release verification

Builds use the pinned Mac and Windows dependencies. Regression tests cover
compact balance sheets and statements with large asset or equity sections.
The Cedar PDF was rendered and visually checked after the pagination fix.
The preceding v1.7.2 candidate passed hands-on Mac PDF and Excel save checks;
the saved files reconciled to the fictional book's expected balances.

Final platform test counts, signing, notarization, and startup/shutdown results
are recorded in the GitHub release. Windows native save dialogs and installed
upgrade behavior have not been verified on a physical Windows desktop.

Thanks to Scott Edwards for the original fixes and MCP serialization work.
