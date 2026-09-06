# LedgerTB v1.7.2

Release candidate — not yet published.

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

No database migration is added. Existing books and posted entries are preserved.

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

Candidate builds use the pinned Mac and Windows dependencies. The release
checklist includes frozen selfcheck, server startup and shutdown, native PDF
and Excel saves, and new-window report navigation. Results will be recorded
with the candidate before publication.

Thanks to Scott Edwards for the original fixes and MCP serialization work.
