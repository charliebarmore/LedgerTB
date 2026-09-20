# CPA usability verification: September 19, 2026

Current decisions and next action belong in the [LedgerTB Notion project](https://app.notion.com/p/3d7f5bd8d2b981ed91d3fad1a03255ce).
This document records technical changes and reproducible local evidence for the
daytime follow-up to [the overnight trial](CPA-USABILITY-TRIAL-2026-09-18.md).
The checkout is `LedgerLabs/ProBooks`, repository `charliebarmore/LedgerTB`,
branch `codex/jev-overnight`.

## Changes

Production checkpoint `841f44d` builds on the account-grouping disclosure in
`2c36842`:

- AI opinions appear below each transaction across the full review width.
  Provider disagreement, independent acceptance and explicit request consent
  retain their existing behavior. Accepting an opinion does not include a row
  for posting. No automatic provider routing was added.
- Review action buttons use their natural width and wrap as a group. The
  transfer checkbox has an accessible label without repeating it in the narrow
  table cell. Include/Exclude All remain explicit actions.
- Worksheet export actions use a wrapping toolbar. The export builders, audit
  callbacks and ledger/input-scoped reuse are unchanged.
- Chart of Accounts shows a short count warning and a collapsed account list.
  Read-only books retain viewing/edit-form inspection but disable save, delete,
  add, import and statement-grouping submission. A regression reproduced the
  enabled write controls before the correction.

No accounting schema, posting logic, credential handling, provider transport,
runtime dependency or installed application was changed by this follow-up.

## Verification procedure

The source-browser fixture now has an expanded sidebar; previous fixture runs
without one did not reproduce the user's cramped layout. Geometry checks require
the comparison to occupy at least 95% of the transaction width, outside a table
column, without horizontal overflow. The full-app harness also checks worksheet
button labels and the collapsed/expanded account grouping notice.

Saved-copy checks change the saved revision through a test-only fixture control,
reopen the refreshed disclosure, and require both confirmations to reset without
another provider request. The disclosure can collapse when its timestamp changes.

All fixtures use fictional data, disposable SQLCipher books, fake vaults and
simulated providers. The nine labeled integration journeys and their limits are
documented in the overnight trial; this is not a live-model quality benchmark.

```sh
.macos-venv/bin/python -m pytest -q -ra -m 'not performance'
.macos-venv/bin/python -m pytest -q -s -m performance
.macos-venv/bin/python scripts/check_jev_browser.py --width 1024 --height 600 \
  --output output/daytime-usability-20260919/recheck-small
.macos-venv/bin/python scripts/check_jev_browser.py --width 1360 --height 768 \
  --output output/daytime-usability-20260919/recheck-laptop
.macos-venv/bin/python scripts/check_packaged_jev.py --source \
  --width 1024 --height 600 \
  --output output/daytime-usability-20260919/recheck-source
.macos-venv/bin/python scripts/check_packaged_jev.py \
  --app output/daytime-usability-20260919/dist/LedgerTB.app \
  --output output/daytime-usability-20260919/recheck-packaged
```

Do not add `--key-file`: these checks must not make live provider requests.
`--source` verifies the checkout with the full-app journey; it does not qualify
packaging. Packaged mode checks 20 bundled source files against the checkout and
adds a test-only fake-vault module to the disposable bundle. That bundle is not a
release artifact. Never replace `/Applications/LedgerTB.app` with it.

## Evidence

Artifacts live under `output/daytime-usability-20260919/`. Production source is
`841f44d`; browser-harness checkpoints are `d5fe150` and `63cd281`.

- Full regression: **870 passed, five Windows-only skips**, three performance
  tests deselected, 769.23 seconds. Original deadlines retained; see
  `functional.log` and `functional.xml`.
- Performance: **all three passed**, 43.70 seconds. A 10,000-row review rendered
  50 row controls in 1.429 seconds. The 50,000-row CSV parsed in 13.8713 seconds;
  duplicate classification took 4.824 seconds, with 44.92 MiB peak memory.
  These are synthetic measurements on this Mac, not production guarantees.
- Focused validation: **91 passed**, one performance check deselected; includes
  saved-review conflicts, the nine fictional posting journeys, failed/partial
  posting, request and export reuse/invalidation, and read-only behavior.
- Source full-app journey: **22 checks passed at 1360×768** in
  `source-laptop-final/`. Includes three simulated providers, failure/retry,
  accepted-category and exclusion preservation, balanced human posting,
  encrypted import identity/audit, all worksheet export formats, account-grouping
  disclosure, saved-copy recovery and a switch to a second encrypted book with
  intentionally identical client/account IDs.
- Fresh PyInstaller build completed and its ad-hoc signature verified. See
  `build.log` and `signature.log`. No new runtime dependency.
- Frozen selfcheck exited 1 with **only** the expected fake-vault identity
  rejection (`packaged_fake_vault.Keyring`). All 51 imports, SQLCipher and Apple
  Vision OCR reported no failure. This is not a passing native-keychain release
  gate. See `selfcheck.log`.
- Native preview PID 48055 opened a 1360×900 logical-pixel window. Exact-PID
  metadata and screenshot are in `walkthrough/native-windows.json` and
  `walkthrough/native-startup.png`. Native startup is separate from the complete
  frozen-server Chromium workflow.

- Source review: **18 checks passed at both 1024×600 and 1360×768**, in
  `review-small-final/` and `review-laptop-settled/`. Comparisons occupy the full
  564/900-pixel review width with a 300-pixel sidebar; no horizontal overflow.
  Saved-copy confirmations were verified unchecked after a revision change,
  with no new provider request. Multiple opinions use normal vertical scrolling
  in short windows.

- Final frozen-server workflows: **22 checks passed at both 1024×600 and
  1360×768**, in `packaged-small-settled/` and `packaged-laptop-final/`. Both
  verified 20 bundled source files against the checkout. Each produced exactly
  one balanced 3,333-cent human posting; the excluded purchase did not post.
  Both books retained encrypted headers, and the second book received no
  journal entries, imported transactions or provider results. These runs include
  the final disclosure synchronization corrections.
- Installed v1.7.2 app version and Info.plist/executable timestamps match the
  pre-build baseline (`installed-app-unchanged.json`). Prior preview sessions and
  unrelated instruction edits were preserved.

Machine-readable record:
[cpa-usability-daytime-2026-09-19.json](jev-evaluation-results/cpa-usability-daytime-2026-09-19.json).
Useful screenshots: `review-laptop-settled/comparison.png`,
`review-small-final/saved-revision-reset.png`,
`packaged-small-settled/worksheet-exports.png`,
`packaged-laptop-final/grouping-expanded.png`, and
`walkthrough/native-startup.png` under the artifact directory.
Failed attempts remain beside successful runs; they are not relabeled as passes.
System Chrome 153.0.8010.52 test tabs intermittently stopped responding; disabling
experimental WebMCP alone did not resolve that. Final browser runs use the
already-installed Chrome for Testing 149.0.7827.55 with `--headed --no-webmcp`.
This is evidence for that browser configuration, not a diagnosis of all Chrome
153 behavior. The pinned agent-browser CLI remains 0.36.0. Local wrapper:
`output/daytime-usability-20260919/browser-cached` (pass it using
`--agent-browser`). On another machine, supply that machine's installed test
browser through `AGENT_BROWSER_EXECUTABLE_PATH`.

Other retained failures were harness synchronization/inspection issues: the
new saved timestamp collapsed its disclosure; back-to-back checkbox actions
needed to wait for rerenders; worksheet markup includes hidden duplicate download
buttons; the account warning can render before its disclosure. Corrections wait
for visible controls and inspect visible button geometry. No production deadline
or accounting assertion was relaxed.

## Preview walkthrough

From the repository root, reopen the separate synthetic native preview with:

```sh
.macos-venv/bin/python output/daytime-usability-20260919/walkthrough/launch.py
```

Use the fictional passphrase `fictional-packaged-acceptance-only`. Launch through
this helper to retain the isolated data directory and fake vault, rather than
opening the disposable app directly. Previous previews are separate.

1. Open **Chart of Accounts**. Expand “View accounts and assign groupings (4)”
   to see individual accounts; collapse it to return to the short warning.
2. In **Import Transactions**, upload
   `output/daytime-usability-20260919/walkthrough/books/synthetic-import.csv`
   to Checking. Confirm the $81.58 disbursement total and continue to review.
3. Exclude both rows. Select only Cedar Paper for actions; open **AI suggestions**,
   consent to the fictional Jev request and ask. Accept Office Supplies; verify
   that the row remains excluded.
4. Use **Choose another AI** and explicitly request Anthropic and OpenAI opinions.
   Expand the full-width comparison. The fixture intentionally disagrees so you
   can inspect the review experience; its outputs are not model-quality evidence.
5. Save the review, then resume it using the replacement confirmation. Verify the
   saved category and exclusions. No provider request should run on resume.
6. Include only Cedar Paper and post it. Journal Entries should show a balanced
   $33.33 debit to Office Supplies and credit to Checking. The unknown $48.25
   purchase stays out of the ledger.
7. Open Trial Balance Worksheet and inspect its wrapping export toolbar with the
   sidebar open. Exports and Refresh retain their usual behavior.

Remaining rollout gates are unchanged: Charlie's usability acceptance,
independent label adjudication, provider terms and approved live-provider
quality/usage/cost checks, real native credentials/installed-upgrade acceptance,
Windows native acceptance and separate release approval. Nothing was pushed,
published or installed; real client books and the real keychain were not used.
