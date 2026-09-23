# LedgerTB v1.8.0 release review

Published September 22, 2026:
[LedgerTB v1.8.0](https://github.com/charliebarmore/LedgerTB/releases/tag/v1.8.0).
Tag `982386c40b3a03f2b162809279caae3ec9215550` points at commit
`ae360a66d586ebcde6dacc60b53a24b6369b4558`.

This file records technical evidence and reproducible release checks. Candidate
preparation started from local `8d9b3d2`. v1.8.0 is the public release.

## Published assets

The release page carries these files. The two names in each pair are the same
bytes.

- `LedgerTB-1.8.0-windows-x64-setup.exe` and `LedgerTB-windows-x64-setup.exe`:
  SHA-256 `4374221b0e3bf80d21e3d89b50f4a2e0dec488400b37213336673887334f2bf0`
  (the qualified installer, 79,750,025 bytes)
- `LedgerTB-1.8.0-mac.zip` and `LedgerTB-mac.zip`:
  SHA-256 `7bb1bfe143f057ebdbe80c33970ba3b440917ec7166513743f55148586748ef2`
  (105,079,255 bytes)
- `LedgerTB-1.8.0-windows-x64.zip` and `LedgerTB-windows-x64.zip`:
  SHA-256 `c8368f09d285f655041024802b64b96542339c9a88962ac732d8d2eb77248534`
  (tag-build package, not the qualified installer; 109,712,295 bytes)

The Windows setup program is the supported Windows download.

## Acceptance after qualification

### Mac reconciliation and export — pass

On September 22 the packaged qualified binary ran in server mode and was driven
with headless Playwright, with a separate Cocoa save-panel check for cancel and
save. That was not a complete native-window walkthrough. The reconciliation
difference reached $0.00, one cancelled save left no file, and the saved files
opened and reconciled. Record:
[Mac reconciliation and export walkthrough](mac-reconciliation-export-walkthrough.md).

### Mac credential vault and installed upgrade — pass

The installed 1.7.2 app was replaced with the qualified 1.8.0 bundle. Launched
twice from Applications, the app opened the existing book with no passphrase
prompt, no keychain prompt, and no Gatekeeper prompt. Help & Updates showed
LedgerTB 1.8.0, and the passphrase stayed remembered on that Mac. Record:
[Mac credential and upgrade walkthrough](mac-credential-upgrade-walkthrough.md).

### Windows desktop

Windows desktop checks on a GitHub-hosted machine covered install of the
published installer, the mark a browser download leaves on a file, the frozen
self-check, compressed pages, and a desktop launch.

Windows SmartScreen: the installer was opened from File Explorer on a GitHub-hosted Windows machine, with the mark a browser download leaves on the file. Windows Defender SmartScreen said "Windows protected your PC" and "Microsoft Defender SmartScreen prevented an unrecognized app from starting. Running this app might put your PC at risk." The choices on that screen were "Don't run" and "More info." This was not a download from the release page in a home browser, so it does not show how Microsoft treats the file after people have downloaded it. The installer is not code-signed, so that warning is expected the first time Windows opens it.

That pass covered the installer named above. It did not record remembered
credentials across relaunch, the Start Menu entry, or uninstall.

## Verification and provenance

The qualified application source is `a351c3ebe4ad0f837193c1720e56929beecc3539`.
Subsequent release commits add acceptance workflows and documentation. The
published Mac ZIP and supported Windows installers retain the qualified bytes
listed above; the secondary Windows ZIP came from the tag-triggered build.

| Check | Current evidence |
| --- | --- |
| Accounting crash/retry, normal deadlines | 6 passed in 15.77s on September 20; fresh run supersedes the prior host-timeout attempt |
| Clean full functional suite | Final patched Mac environment: 898 passed, 5 Windows-only skips in 387.80s; Linux: 898 passed, 5 skips in 286.89s; performance tests run separately |
| Default-threshold performance suite | Final patched environment: all 3 passed in 30.33s; no assertion or deadline overrides |
| Migration 23–26, failed upgrade and restore | All populated upgrade/restore and failed-upgrade rollback/retry cases passed in the clean suite |
| Frozen imports, SQLCipher, OCR and source provenance | Patched rebuild succeeded; 132 bundled source/legal/config files match. Imports, SQLCipher and native OCR report no failure; the deliberately disabled vault remains the sole selfcheck failure |
| Browser import/provider/recovery/export/book isolation | Both initial and patched 1.8.0 bundles passed all 22 checks at 1360×768 with simulated providers |
| Native close/cancel/quit, recovery and posting | Actual Cocoa/WebKit source window passed close-Cancel preservation, application quit/reopen, separate saved/recovery copies, resume and selective posting |
| Native reconciliation and PDF/XLSX save/cancel | September 22 packaged check passed: difference $0.00, cancelled save left no file, saved files opened and reconciled. Server mode plus headless Playwright, with a separate Cocoa save-panel check. Not a complete native-window walkthrough |
| Signed/notarized Mac artifact and installed upgrade | Developer ID signature, secure timestamp, accepted Apple notarization, stapled ticket validation and Gatekeeper acceptance pass. September 22 installed upgrade from 1.7.2 passed: two launches, no passphrase, keychain, or Gatekeeper prompt |
| Windows pinned tests, frozen runtime and installer | 901 passed, two POSIX-only skips in 1465.90s; encryption, build, frozen selfcheck, compressed routes, window shutdown and Inno installer all passed |
| Windows native install/upgrade/save dialogs | GitHub-hosted machine: install of the published installer, browser-download mark, frozen self-check, compressed pages, and a desktop launch. SmartScreen showed the unsigned-app warning. Not a home-browser download. Remembered credentials, Start Menu, and uninstall were not recorded |

The Mac lockfile matched all 90 installed packages and `pip check` passed.
The signed archive was extracted and checked again: signature, notarization
staple and Gatekeeper passed, with all 132 recorded source/config/legal files
matching the build manifest. Production bundles contain no fixture vault backend
or environment files. Apple notarization was accepted before publication.

Public CI evidence:

- [Linux tests and browser acceptance](https://github.com/charliebarmore/LedgerTB/actions/runs/35531639105).
- [Pinned dependency audits, secret scan and workflow audit](https://github.com/charliebarmore/LedgerTB/actions/runs/35531639095).
- [Windows qualification and installer](https://github.com/charliebarmore/LedgerTB/actions/runs/35531662865).
- [Installed Windows acceptance](https://github.com/charliebarmore/LedgerTB/actions/runs/35709281793).

## Corrections during qualification

The initial runs exposed Streamlit test-state compatibility, a missing pywebview
test dependency, and a Windows assertion that incorrectly expected POSIX mode
bits. These were corrected and the clean suites above passed. Six HTTPX2/HTTPCore2
advisories were resolved by pinning the compatible 2.12.0 pair on both platforms.
Individual accounting assertions and crash-worker transaction deadlines were not
relaxed. Earlier host-pressure and native-input failures are not counted as passes.

The separate fake-vault packaged tests intentionally fail the real-vault identity
selfcheck. Their successful imports, encryption and OCR checks do not substitute
for the separately documented signed installed-upgrade acceptance.

## Reproducing the automated checks

Use a clean environment matching the platform lockfile. All automated checks
must use fictional encrypted books, fake credential backends and simulated
providers; never run them against a client ledger or the user's credential vault.

```sh
python scripts/verify_lock.py requirements-macos-arm64.lock
python -m pip check
python -m pytest -q -ra -m "not performance"
python -m pytest -q -s -m performance
```

See [TESTING.md](TESTING.md), [WINDOWS-TESTING.md](WINDOWS-TESTING.md), and the
Mac acceptance reports above for scope and reproduction steps. Generated logs,
screenshots, fixtures and test-modified bundles belong in ignored local output
folders, not the source tree. Never distribute a test-modified bundle.

## Remaining limitations and follow-ups

The Windows installer is unsigned. Runner SmartScreen evidence does not prove
a home-browser download or remembered credentials, Start Menu behavior,
uninstall, installed upgrades or native save dialogs on a user's Windows PC.
The Mac export report also records a save-destination automation limitation.

A canceled worksheet export still writes an export audit row, and reconciliation
history displays UTC while Audit Trail displays local time. These are follow-ups.
Independent CPA evaluation and each firm's approval of provider data-handling
terms remain separate from availability of the optional, off-by-default providers.
Synthetic results are not a guarantee of accuracy on client transactions.
