# LedgerTB v1.7.2

Unreleased. These notes will be completed when the release candidate is assembled.

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
