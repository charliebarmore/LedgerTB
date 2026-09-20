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
| Clean full functional suite | 898 passed, 5 Windows-only skips, 3 performance tests deselected in 325.68s |
| Default-threshold performance suite | All 3 passed in 38.11s; no assertion or deadline overrides |
| Migration 23–26, failed upgrade and restore | All populated upgrade/restore and failed-upgrade rollback/retry cases passed in the clean suite |
| Frozen imports, SQLCipher, OCR and source provenance | Pending fresh candidate build |
| Browser import/provider/recovery/export/book isolation | Earlier candidate passed; current candidate pending |
| Native close/cancel/quit, recovery, posting and reconciliation | Pending |
| Native PDF/XLSX save/cancel and file inspection | Pending |
| Signed/notarized Mac artifact and installed upgrade | Pending; installed app remains untouched |
| Windows pinned tests, frozen runtime and installer | Pending on candidate source |
| Windows native install/upgrade/save dialogs | Requires a suitable Windows desktop |

Artifacts for this pass: `output/release-candidate-20260920/`. Earlier evidence
remains in the dated Jev/usability/month-close guides and
[desktop-pilot record](DESKTOP-PILOT-2026-09-19.md).

The pinned Mac lockfile matches all 90 installed packages and `pip check` reports
no broken requirements. Performance observations: 10,000 staged rows rendered
50 row controls in 2.314s; a 50,000-row CSV parsed in 13.944s and classified
duplicates in 5.2523s with 44.91 MiB peak traced memory. The 10,000-entry journal
page rendered in 0.1575s and audit page in 0.223s. These are local measurements,
not response-time guarantees on other hardware.

## Local commands

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
