# LedgerTB v1.8.0

Released September 22, 2026.
[v1.8.0](https://github.com/charliebarmore/LedgerTB/releases/tag/v1.8.0).
Final qualification is recorded in
[the release review](RELEASE-REVIEW-1.8.0.md).

## Import review and optional AI

- Request account suggestions from TypeSafe Jev, Anthropic or OpenAI using the
  provider and model controls. You can request another provider's independent
  opinion without replacing an accepted category.
- Cloud requests are optional and require explicit consent. Selected transaction
  evidence, eligible accounts and relevant business context go to the chosen
  provider. Credentials stay in the operating system's credential store.
- Suggestions require human review. Insufficient information, mixed purchases
  and transfers can remain unresolved for manual review. A confidence score
  from Jev describes distribution concentration, not guaranteed correctness.
- Repeated reruns reuse matching results within the current session. Changed
  evidence, instructions, account choices or ledger invalidate reuse. Failed
  requests need an explicit retry. Offline bookkeeping remains available.
- Import review separates selecting rows for actions from including them for
  posting. Compact controls, paging and wider comparisons make larger imports
  easier to review. Dashboard currency, journal lines and account-grouping
  notices are clearer.

## Keeping review work

- Save an encrypted review for later and resume it with duplicate checks. Save,
  Discard and Cancel protect supported client/book switches and replacement
  imports. A failed replacement import leaves the prior review available.
- Automatic encrypted recovery checkpoints are separate from explicitly saved
  copies. They retain completed server-side edits, not edits still being
  processed when the app closes. Continue using **Save review for later** for
  an intentional checkpoint.
- Normal desktop close/quit warns while a review may be open. Force-quit and
  hardware failure cannot be intercepted. Resuming a saved or recovery copy
  neither calls an AI provider nor creates ledger entries.
- A restored book requires old windows to reload. Changed saved-review revisions
  require an explicit choice before posting or replacing the saved copy.
- Read-only books cannot be migrated or modified. Writable upgrades close their
  connection even on failure so retry and restore remain available.

## Worksheet exports and accounting safeguards

- Unchanged worksheet reruns reuse generated export files within the session;
  changed ledger/report/branding inputs invalidate them.
- Integer-cent amounts, balanced posting, import identity and duplicate checks,
  human audit attribution, and engine-enforced assistant permissions remain in
  force. AI suggestions do not change posting inclusion or post automatically.
- Mac and Windows builds pin the HTTPX2/HTTPCore2 security fixes identified by
  the release dependency audit.

## Upgrade preparation

Create and verify an encrypted backup before upgrading, then close every window
using that book. This version adds migrations 026 (explicit saved reviews) and
027 (automatic recovery and restore detection). Keep the pre-upgrade backup for
rollback; do not assume an older app understands newer review metadata.

Synthetic results do not establish accuracy on client transactions.

## Completed release checks

- Mac functional suite: 898 passed, 5 Windows-only skips. Three performance
  checks passed at the original thresholds. Packaged browser acceptance passed
  all 22 checks.
- Linux tests on the qualified source: 898 passed, 5 skipped. Security checks
  passed, including both lockfile audits.
- Windows qualification run: 901 passed, 2 POSIX-only skips. Encryption, the
  frozen self-check, compressed pages, window shutdown, and the installer
  build passed. The release workflow on this tag passed the same Windows gates
  before the draft was opened.
- The Mac archive is Developer ID signed, notarized (Apple submission
  ce953e33-d251-4290-be94-f3aeb514bb46, Accepted), stapled, and accepted by
  Gatekeeper as Notarized Developer ID.
- Reconciliation and export on the packaged Mac build passed. The app ran in
  server mode and was driven with headless Playwright, with a separate Cocoa
  save-panel check for cancel and save. That was not a complete native-window
  walkthrough. The reconciliation difference reached $0.00, one cancelled save
  left no file, and the saved files opened and reconciled.
- The installed Mac upgrade from 1.7.2 to 1.8.0 passed. Launched twice from
  Applications, the app opened the existing book with no passphrase prompt, no
  keychain prompt, and no Gatekeeper prompt. Help & Updates showed LedgerTB
  1.8.0, and the passphrase stayed remembered on that Mac.
- Windows desktop checks on a GitHub-hosted machine covered install of this
  installer, the mark a browser download leaves on a file, the frozen
  self-check, compressed pages, and a desktop launch.

Windows SmartScreen: the installer was opened from File Explorer on a GitHub-hosted Windows machine, with the mark a browser download leaves on the file. Windows Defender SmartScreen said "Windows protected your PC" and "Microsoft Defender SmartScreen prevented an unrecognized app from starting. Running this app might put your PC at risk." The choices on that screen were "Don't run" and "More info." This was not a download from the release page in a home browser, so it does not show how Microsoft treats the file after people have downloaded it. The installer is not code-signed, so that warning is expected the first time Windows opens it.

## Download verification

The supported Windows file is the setup program. The same bytes are attached
under both names below. The Mac archive is the notarized build, also attached
under both names.

- `LedgerTB-1.8.0-windows-x64-setup.exe` and `LedgerTB-windows-x64-setup.exe`:
  SHA-256 `4374221b0e3bf80d21e3d89b50f4a2e0dec488400b37213336673887334f2bf0`
  (the qualified installer, 79,750,025 bytes)
- `LedgerTB-1.8.0-mac.zip` and `LedgerTB-mac.zip`:
  SHA-256 `7bb1bfe143f057ebdbe80c33970ba3b440917ec7166513743f55148586748ef2`
  (105,079,255 bytes)
- `LedgerTB-1.8.0-windows-x64.zip` and `LedgerTB-windows-x64.zip`:
  SHA-256 `c8368f09d285f655041024802b64b96542339c9a88962ac732d8d2eb77248534`
  (tag-build package, not the qualified installer; 109,712,295 bytes)

Install with the setup program. Files taken out of that zip by File Explorer
are marked as coming from the internet, and LedgerTB will not start until
those marks are cleared.

[Full source comparison](https://github.com/charliebarmore/LedgerTB/compare/v1.7.2...v1.8.0)
