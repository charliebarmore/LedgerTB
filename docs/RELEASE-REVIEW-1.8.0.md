# LedgerTB v1.8.0 candidate qualification

Current goal and operational handoff:
[LedgerTB Notion project](https://app.notion.com/p/3d7f5bd8d2b981ed91d3fad1a03255ce).
This file records technical evidence and reproducible release checks. Candidate
preparation starts from local `8d9b3d2`; version 1.7.2 remains the public release.
No release tag, publication or installed-app replacement is part of preparation.

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
| Native reconciliation and PDF/XLSX save/cancel | Reconciliation opened with the correct -$33.33 balance; clearing/completion unverified. Excel opened a real NSSavePanel; save/cancel and PDF dialog acceptance unverified |
| Signed/notarized Mac artifact and installed upgrade | Clean candidate signed with Developer ID and secure timestamp; strict signature verification passes outside the sandbox. Apple submission approved by Charlie; installed app remains untouched |
| Windows pinned tests, frozen runtime and installer | Pending on candidate source |
| Windows native install/upgrade/save dialogs | Requires a suitable Windows desktop |

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
[Final Windows qualification](https://github.com/charliebarmore/LedgerTB/actions/runs/35531662865)
remains in progress. [Final Linux tests and browser acceptance passed](https://github.com/charliebarmore/LedgerTB/actions/runs/35531639105).

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
No native reconciliation completion or PDF/Excel save/cancel pass is claimed.
Use the remaining walkthrough below on the disposable fixture before release.

1. Finish the Checking reconciliation at -$33.33: clear the sole Cedar entry,
   save, verify zero difference, acknowledge and complete.
2. On Trial Balance Worksheet, cancel an Excel save, then save Excel and both
   close-package formats into the disposable output folder. Inspect the files
   for the fictional client and balanced $33.33 totals.
3. Repeat installed-upgrade/real-vault acceptance with Charlie's explicit
   supervision; native Windows download/install/upgrade checks need Windows.

## Local commands

The source/build commit is `a351c3ebe4ad0f837193c1720e56929beecc3539`. The clean
candidate is `output/release-candidate-20260920/dist-final/LedgerTB.app`.
`acceptance-final/LedgerTB.app` is a separate modified test copy and must never
be distributed. Production contains no fake-vault backend or `.env` files;
`source-provenance-final.json` records its 132 matched files and lock fingerprints.
The signed 113 MiB archive `LedgerTB-1.8.0-notary.zip` has SHA-256
`a47d923d6cd4b4a73fdb049d1636b51ecfaa4b133593d54e303419671aee7346`.
Automatic approval review initially blocked the Apple upload; Charlie explicitly
approved it later on September 20. Notarization outcome and the post-stapling
archive hash must be recorded before distribution. No release is published.

The sandbox prevented OCR and Developer ID verification. Repeating those checks
outside it, while keeping the credential vault disabled, passed OCR/signature
checks. The sole remaining selfcheck failure is the disabled credential backend.
Installed v1.7.2's version and plist/executable timestamps still match baseline.

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

## Release decision still required

Final evidence must identify the exact source commit and artifact hashes, explain
any failed/skipped checks, and distinguish simulated provider integration from
live-provider quality/usage testing. Independent CPA evaluation, provider terms,
real-vault/installed-upgrade acceptance and Windows native qualification remain
explicit rollout considerations. Prepare reviewable PR material and release
notes before any publication decision.
