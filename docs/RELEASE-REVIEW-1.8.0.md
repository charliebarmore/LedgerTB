# LedgerTB v1.8.0 release review

Published September 22, 2026:
[LedgerTB v1.8.0](https://github.com/charliebarmore/LedgerTB/releases/tag/v1.8.0).
Tag `982386c40b3a03f2b162809279caae3ec9215550` points at commit
`ae360a66d586ebcde6dacc60b53a24b6369b4558`.

Current goal and operational handoff:
[LedgerTB Notion project](https://app.notion.com/p/3d7f5bd8d2b981ed91d3fad1a03255ce).
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

## Scope and evidence rules

Freeze the implemented Jev/provider choices, import usability, saved/automatic
review recovery, restore protection and worksheet export reuse. Fix reproduced
release blockers; defer unrelated features and dependency upgrades. All automated
checks use fake vaults and disposable fictional SQLCipher books. No paid AI calls.
Keep failed runs and distinguish source/browser/frozen/native acceptance.

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

Artifacts for this pass: `output/release-candidate-20260920/`. Earlier evidence
remains in the dated Jev/usability/month-close guides and
[desktop-pilot record](DESKTOP-PILOT-2026-09-19.md).

The pinned Mac lockfile matches all 90 installed packages and `pip check` reports
no broken requirements. Final performance observations: 10,000 staged rows rendered
50 row controls in 1.428s; a 50,000-row CSV parsed in 12.2676s and classified
duplicates in 4.0158s with 44.92 MiB peak traced memory. The 10,000-entry journal
page rendered in 0.1219s and audit page in 0.1611s. These are local measurements,
not response-time guarantees on other hardware.

## Candidate review and CI corrections

[Draft PR #50](https://github.com/charliebarmore/LedgerTB/pull/50) contains the
candidate. `e712156` records the initial version and evidence; `2ca2a5a` corrects
open-range Streamlit AppTest compatibility, declares pywebview as a test
dependency, and records reviewed scanner false positives. The affected 46 tests
passed locally; [Linux tests and browser acceptance passed](https://github.com/charliebarmore/LedgerTB/actions/runs/35508910672).

The original Windows run had 900 passes, two POSIX-only skips and one failure:
the close-channel test incorrectly expected POSIX mode bits on Windows.
Encryption, PyInstaller, frozen selfcheck, compressed HTTP serving, window
shutdown and the Inno installer all passed. `a351c3e` keeps channel and close-guard
assertions on both platforms and restricts only the mode-bit assertion to POSIX.
The hosted encrypted suite took 24m43s, so its job allowance is now 30 minutes;
individual crash-worker deadlines and accounting assertions are unchanged.

The dependency audit found six advisories in Windows' old HTTPX2/HTTPCore2 pins.
Both platform locks now use 2.12.0, the matching pair required by package
metadata. The Mac environment matches all 90 pins and passes `pip check`.
[Security CI passes on a351c3e](https://github.com/charliebarmore/LedgerTB/actions/runs/35531639095),
including both lockfile audits, tracked-file secret scanning and workflow audit.
[Final Windows qualification passed](https://github.com/charliebarmore/LedgerTB/actions/runs/35531662865):
901 tests passed, two POSIX-only checks skipped and three performance checks
deselected in 1465.90s. All seven build/runtime/installer signals and both artifact
uploads passed. [Final Linux tests and browser acceptance passed](https://github.com/charliebarmore/LedgerTB/actions/runs/35531639105).

Scanner review identified 389 commit/content hashes, two artifact paths and 15
synthetic credential lines. The detect-secrets baseline matches value and path;
gitleaks retains default rules with an exception only for SHA-1 hash fields in
that baseline. Negative controls confirmed new synthetic credentials, other
fields in the baseline, and the same field in unrelated files are still rejected.
An attempted one-commit hook bypass was rejected by automatic approval review;
the commit instead passed the active hook using its narrow supported exception.

## Native evidence and tool limits

The native fixture imported the two-row CSV with $81.58 disbursements. Manual
categorization selected 6100 Office Supplies for Cedar Paper and excluded the
unknown $48.25 purchase. Canceling the actual close warning retained both values.
After explicitly saving, excluding Cedar too and quitting, a fresh native session
offered the saved review and a separate recovery copy. Resuming recovery retained
both exclusions and the category; explicitly including Cedar and posting produced
one $33.33 entry, with the unknown purchase excluded. No provider requests occurred.

Reconciliation's canvas checkbox could not be operated reliably by the test
driver. An experimental native-input helper was removed after it failed to change
the control. The Excel action opened an actual save panel, whose remote controls
were not exposed by the driver. Computer Use discovery returned, but selecting
the Python app subsequently timed out after 26,379 seconds despite a requested
10-second limit. This is a tool failure, not evidence of an application hang.
That September 20 source-window attempt did not complete reconciliation or a
PDF/Excel save and cancel. The September 22 packaged check in the acceptance
section above did: difference $0.00, one cancelled save with no file, and saved
files that opened and reconciled. The steps below are that earlier attempt.
The task-owned native acceptance window was closed after preserving evidence;
prior user previews remain untouched. Reopen the existing fictional book with:

```sh
.macos-venv/bin/python scripts/native_pilot_driver.py \
  --data-dir output/release-candidate-20260920/native-books \
  --output output/release-candidate-20260920/manual-walkthrough
```

Unlock with `fictional-packaged-acceptance-only`. This source fixture uses a fake
vault; it does not establish acceptance of the signed app’s real credential vault.
Keep exports inside its `manual-walkthrough/downloads/` directory.

1. Finish the Checking reconciliation at -$33.33: clear the sole Cedar entry,
   save, verify zero difference, acknowledge and complete.
2. On Trial Balance Worksheet, cancel an Excel save, then save Excel and both
   close-package formats into the disposable output folder. Inspect the files
   for the fictional client and balanced $33.33 totals.
3. Repeat installed-upgrade/real-vault acceptance with Charlie's explicit
   supervision; native Windows download/install/upgrade checks need Windows.

The acceptance section records the September 22 results for those checks.

## Local commands

The source/build commit is `a351c3ebe4ad0f837193c1720e56929beecc3539`. The clean
candidate is `output/release-candidate-20260920/dist-final/LedgerTB.app`.
`acceptance-final/LedgerTB.app` is a separate modified test copy and must never
be distributed. Production contains no fake-vault backend or `.env` files;
`source-provenance-final.json` records its 132 matched files and lock fingerprints.
The signed 105,073,373-byte archive `LedgerTB-1.8.0-notary.zip` has SHA-256
`a47d923d6cd4b4a73fdb049d1636b51ecfaa4b133593d54e303419671aee7346`.
Automatic approval review initially blocked the Apple upload; Charlie explicitly
approved it later on September 20. Submission
`ce953e33-d251-4290-be94-f3aeb514bb46` is Accepted. Stapling/validation succeeded
and Gatekeeper reports `accepted`, `source=Notarized Developer ID`.
The final distribution archive is `LedgerTB-1.8.0-macos-arm64.zip` (105,079,255 bytes),
SHA-256 `7bb1bfe143f057ebdbe80c33970ba3b440917ec7166513743f55148586748ef2`.
The extracted archive also passes strict signature verification, ticket validation
and Gatekeeper assessment; all 132 recorded source/config/legal hashes match.
Those bytes are the published `LedgerTB-1.8.0-mac.zip` and `LedgerTB-mac.zip`.

The Windows installer from that same source and CI run is
`windows-final/LedgerTB-1.8.0-windows-x64-setup.exe` (79,750,025 bytes), SHA-256
`4374221b0e3bf80d21e3d89b50f4a2e0dec488400b37213336673887334f2bf0`.
Those bytes are the published `LedgerTB-1.8.0-windows-x64-setup.exe` and
`LedgerTB-windows-x64-setup.exe`. Its PE certificate table is empty: this
installer is unsigned. The published `LedgerTB-1.8.0-windows-x64.zip` and
`LedgerTB-windows-x64.zip` are the tag-build package, SHA-256
`c8368f09d285f655041024802b64b96542339c9a88962ac732d8d2eb77248534`, not this
installer. Ship the installer, not that zip. The GitHub-hosted Windows check
in the acceptance section recorded the SmartScreen wording. It was not a
download from the release page in a home browser. Both qualified artifact
hashes are retained locally in `SHA256SUMS` and `artifact-manifest.json`.

The sandbox prevented OCR and Developer ID verification. Repeating those checks
outside it, while keeping the credential vault disabled, passed OCR/signature
checks. The sole remaining selfcheck failure is the disabled credential backend.
The installed Mac app was replaced with this 1.8.0 bundle on September 22.
See the acceptance section.

Use the pinned `.macos-venv` environment. Set BLAS worker counts to one if other
workloads are active; do not relax assertions or performance thresholds.

```sh
.macos-venv/bin/python scripts/verify_lock.py requirements-macos-arm64.lock
.macos-venv/bin/python -m pip check
.macos-venv/bin/python -m pytest -x -q -ra -m "not performance"
.macos-venv/bin/python -m pytest -q -s -m performance
```

For native fixture isolation and the fictional passphrase, follow
`scripts/native_pilot_driver.py` and the desktop-pilot guide. For frozen browser
acceptance use `scripts/check_packaged_jev.py` without `--key-file`; all provider
requests remain simulated. Build into a new directory under the dated artifact
folder. Do not overwrite earlier previews or `/Applications/LedgerTB.app`.

## Release decision

v1.8.0 was published on September 22, 2026. The published commit is
`ae360a66d586ebcde6dacc60b53a24b6369b4558`. The published asset hashes are in
the section above. Independent CPA evaluation of the synthetic labels, and firm
approval of TypeSafe, Anthropic, and OpenAI data-handling terms, remain product
decisions. Those features are opt-in and off by default. A cancelled worksheet
export still writes an export audit row, and reconciliation history shows UTC
while the Audit Trail shows local time. Those are accepted follow-ups.
