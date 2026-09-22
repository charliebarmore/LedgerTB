# macOS v1.7.2 → v1.8.0 upgrade acceptance

Verified September 22, 2026 on macOS 26.6.2 with the qualified Developer ID
signed, notarized and stapled v1.8.0 bundle. See the
[release review](RELEASE-REVIEW-1.8.0.md) for artifact identities and CI evidence.

## Method and results

The installed v1.7.2 application and a fictional, schema-025 acceptance book
were backed up before replacement. The book's passphrase was already remembered
in the operating system credential vault. A non-fixture book was not opened.

- The installed bundle matched the qualified archive. Signature, notarization
  ticket and Gatekeeper checks passed; the signing identity remained unchanged.
- Two native launches opened the fictional book without a passphrase, keychain
  or Gatekeeper prompt. The installed version was 1.8.0.
- The fictional book migrated from schema 025 to 027. Its dashboard balances
  and audit figures were unchanged. Saved book selection was preserved.
- Server-mode page checks using the installed binary confirmed the remembered
  passphrase setting and an existing provider credential remained available.
  No live provider request was made.
- Credential item metadata was unchanged. Native launcher shutdown released
  the book lock. No credential values or vault inventories are published here.

Native startup was observed through window capture and polling. Pages requiring
navigation were inspected in a browser connected to the installed binary in
server mode. This is not a complete native interaction walkthrough. Rollback
assets were checked, but an uninstall/reinstall rollback rehearsal was not run.

## Repeating the check

Use a fictional book, back up both the previous signed application and its
pre-migration encrypted book, and verify their recoverability before upgrading.
Check version/signature, two launches, remembered credentials, balances and audit
history. Keep credentials and machine-specific evidence outside the repository.
Never restore only an old application against a migrated book without checking
schema compatibility. Upgrades require a verified pre-upgrade backup.

## Follow-up

Stopping a diagnostic server directly can leave a stale book lock; a subsequent
same-machine opener recovers it. Native launcher shutdown released it correctly.
The import-review provider panel and live-provider behavior were not exercised.
