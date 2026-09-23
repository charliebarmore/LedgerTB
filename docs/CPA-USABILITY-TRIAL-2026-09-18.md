# CPA usability trial: September 18–19, 2026

Historical design and test evidence. Final qualification is recorded in the
[v1.8.0 release review](RELEASE-REVIEW-1.8.0.md).

This document owns technical behavior and reproducible evidence. Work starts from
local `24eac4e` on `codex/jev-overnight`, in the ProBooks checkout of LedgerTB.
No release, push, installed upgrade or live provider call is part of this pass.

## Review usability

Import Review has one full-width action panel at a time. Selection, AI, bulk
category and sorting controls stay mounted when hidden, retaining widget state;
the hidden containers leave layout and the accessibility tree. Long selections
and model controls scroll with the page instead of clipping inside popovers.
Per-row AI opinions are expandable, with disagreement visible in the summary.
Retry appears only for an applicable failure. Asking, accepting and including
for posting remain three separate human actions.

A reproduced recovery defect described a failed included row plus an excluded
row as “all excluded.” The result now reports actual failed/excluded/unresolved
counts. When nothing posts, the entire review remains. After partial success,
failed and unresolved included rows remain; successfully posted rows and
explicitly excluded rows leave the current list, as before.

## Saved review and recovery contract

CSV and statement review edits are in session memory until **Save review for
later**. Closing the app, losing its session or switching clients/books can lose
unsaved edits. Starting a replacement import can also replace the current
review; save first if you need to return to it. Assistant-proposed imported rows already have durable Pending
records; their unsaved review edits have the same session limitation.

The explicit saved copy:

- Lives in migration `026`'s `import_review_drafts` table, inside SQLCipher. One
  copy per client per book; no plaintext sidecar or cloud storage.
- Saves row evidence, source identities, integer cents, categories, transfer
  flags and inclusion. Accepted-AI metadata retains the input hash for normal
  stale-input checks. No credentials, consent, provider responses or request
  cache are saved. No journal or imported-transaction records are created.
- Uses an opaque revision for optimistic concurrency. Another window cannot
  silently overwrite or delete a newer copy. Save and its human-attributed
  audit record commit together, or roll back together.
- Resume creates fresh widget identities, rechecks duplicates against current
  history, expires old duplicate overrides and revalidates accepted AI choices.
  Already-posted exact source rows are excluded. It sends no AI request and
  posts nothing. Replacing an active review requires an explicit checkbox.
- Later edits and posting do **not** silently update the saved copy. Save again
  to replace your loaded copy, or explicitly discard it. A stale saved copy can
  contain rows already posted; ordinary duplicate/idempotency safeguards still
  apply. Unaccepted AI opinions must be explicitly requested again in a new
  session and can incur a new charge.
- Is readable in a read-only session, but Save/Discard are disabled. Older
  read-only books without migration 026 remain viewable without attempting it.
  Engine read/propose/post assistant permissions do not gain write access to
  this human-owned table.

Crash tests terminate a child process without cleanup immediately before and
immediately after commit. They establish process-interruption atomicity, not
power-loss or hardware-failure resilience. Recovery tests never use the real
keychain or client books. The suite also forces dummy API environment values
and an unavailable OS-vault backend before application imports, in addition to
its per-test in-memory vault fixtures. This backstop survives fixture teardown.

## Read-only and storage failures

Import posting/account/profile/history mutations, journal saves/corrections/
reversals/deletes, worksheet year setup/closing/AJEs and policy-note saves are
disabled when read-only. The worksheet no longer tries to repair its fiscal
calendar while being viewed read-only. Model/SQLCipher boundaries still enforce
permissions; UI disabling does not replace those boundaries.

Read-only worksheet downloads are usable without attempting a forbidden audit
INSERT; a caption explains that those downloads are not added to book history.
Writable-session exports still audit the actual download. Storage errors in the
changed save paths give recovery steps without exposing raw database paths.
This is a focused pass, not a claim that every application error message or
write control has been redesigned.

## Export reuse

`services/worksheet_export.py` renders the existing formula-bearing worksheet.
`services/worksheet_export_cache.py` keeps one PDF/XLSX bundle in the current
Streamlit session. It reads fresh inputs under the existing consistent-export
window and hashes the resolved book path **and** durable book identity, client,
fiscal year settings, period, show-all choice, date format, actual worksheet and
close-package report data, comparative periods, close readiness and both firm
and client branding (including logo content).

Unchanged reruns reuse bytes; changed inputs replace the bundle. A failed new
build cannot expose the previous bundle. Switching client clears the old bundle;
there is no global shared cache. Refresh explicitly rebuilds it. The generated
stamp continues to describe when the cached files were built. Report reads still
run on rerun: this removes repeated rendering, not all report-query work. No new
runtime dependency is required; the existing desktop spec bundles service,
utility and migration directories and the already-pinned PDF/XLSX libraries.

## Fictional scenario matrix

`tests/fixtures/import_review_journeys.json` labels nine cases: bank supplies,
client receipt, bank expense refund, card purchase, card refund, card-balance
transfer, unknown purchase without a receipt, mixed personal/business purchase,
and owner contribution. Each journey runs through the real page and encrypted
posting with manual choices and each of the three simulated providers.

Each run requires human acceptance/manual review, posts seven balanced entries,
retains two unresolved rows, preserves exact-source idempotency and checks the
sign of each account leg. These are controlled integration tests, **not** a new
provider accuracy or cost benchmark. Historical real Jev evaluation and its
limitations remain in `JEV-EVALUATION-2026-09-17.md`. Anthropic/OpenAI live model
access, quality, usage and cost remain unqualified without approved accounts.

## Reproduction

```sh
.macos-venv/bin/python -m pytest -q \
  tests/test_import_workflow_scenarios.py tests/test_import_review_drafts.py \
  tests/test_worksheet_export_cache.py tests/test_release_upgrade.py \
  tests/test_accounting_pages.py tests/test_jev_review.py tests/test_ai_review.py
.macos-venv/bin/python -m pytest -q -ra -m 'not performance'
.macos-venv/bin/python -m pytest -q -s -m performance
.macos-venv/bin/python scripts/check_jev_browser.py --width 1024 --height 600 \
  --output output/usability-overnight-20260918/browser-small
.macos-venv/bin/python scripts/check_jev_browser.py --width 1360 --height 768 \
  --output output/usability-overnight-20260918/browser-laptop
PYINSTALLER_CONFIG_DIR="$PWD/output/usability-overnight-20260918/cache" \
  LEDGERTB_CODESIGN_ID= PROBOOKS_CODESIGN_ID= \
  .macos-venv/bin/python -m PyInstaller LedgerTB.spec --noconfirm \
  --distpath output/usability-overnight-20260918/dist \
  --workpath output/usability-overnight-20260918/build
.macos-venv/bin/python scripts/check_packaged_jev.py \
  --app output/usability-overnight-20260918/dist/LedgerTB.app \
  --output output/usability-overnight-20260918/packaged
```

All browser/packaged fixtures use fake vaults and disposable encrypted books.
Do not add `--key-file` for these simulated checks. Packaging and native preview
remain local; do not replace `/Applications/LedgerTB.app`.

## Suggested walkthrough of the separate preview

The separate synthetic native window is open. To reopen it from the repository
root, use the fixture launcher, which sets its isolated data directory and fake
vault:

```sh
.macos-venv/bin/python output/usability-overnight-20260918/walkthrough/launch.py
```

Do not launch the disposable `.app` directly for this trial: the launcher supplies
the isolation settings. Use
`output/usability-overnight-20260918/walkthrough/books/synthetic-import.csv`,
not a client book. Its providers and credential vault are simulated. Unlock with
`fictional-packaged-acceptance-only`.

1. Import the two-row CSV to Checking. Confirm disbursements of $81.58.
2. In Review, exclude both rows. Select only Cedar Paper for actions and open
   AI suggestions. Choose Jev, consent, request and expand the row's opinions.
3. Accept Office Supplies. Confirm that posting inclusion is still unchecked.
4. Use **Choose another AI** and explicitly request another provider's opinion.
   The fixture deliberately demonstrates disagreement. Keep the supported
   Office Supplies decision; simulated answers do not measure provider quality.
5. **Save review for later**, clear the current list, then expand Saved review
   and resume it. Check the retained category and exclusion decisions.
6. Include only Cedar Paper and post. Verify one balanced $33.33 journal entry.
   Resuming the older saved copy must identify the already-posted row as a
   duplicate and leave it excluded. Discard the saved copy when finished.
7. Open the Trial Balance Worksheet and inspect the worksheet Excel and both
   close-package formats. Unchanged reruns reuse their prepared bytes; Refresh
   explicitly rebuilds them.

Saving a copy is intentional; edits made after that save remain session-only.
The installed application and older preview windows are separate.

## Verification record

- Initial partial-post regression: 2 failed / 1 passed before correction;
  3 passed after correction (13.02 s).
- Nine-case journey and failure/read-only scenarios: 7 passed (383.05 s).
- Recovery/export/accounting-page run: 47 passed (199.43 s), before final added
  crash/readonly tests and generic journal storage-error handlers.
- Initial 1024×600 workflow: 13 checks passed before the final saved-review and
  retry-button changes. First final-browser attempt failed at fixture startup;
  retained under `output/usability-overnight-20260918/browser-final-small`.
- AI/recovery regression run: 31 passed (426.08 s). Focused migration,
  process-interruption and export follow-up: 8 passed (18.54 s), using original
  deadlines. A separate crash attempt timed out before passing on rerun.
- First full run on the implemented source: 865 passed, 5 Windows-only skips,
  2 timeouts, 3 performance cases deselected (1261.27 s). Failures were the
  30-second accounting crash-worker startup and a 30-second journal book-switch
  AppTest. Both passed with original limits in the 28-case follow-up (30.28 s),
  which also verified the final test-vault backstop and retry-button behavior.
  Final full run at `dc5f2f4`, including the process-wide backstop: **868 passed,
  5 Windows-only skips, 3 performance cases deselected in 748.30 s**, using the
  original deadlines. This clean run supersedes aggregate-only qualification;
  earlier failed/interrupted runs remain available.
- Performance: all 3 checks passed original thresholds (56.70 s). The 10,000-row
  review rendered 50 controls in 2.794 s; the 50,000-row CSV parsed in 19.1441 s
  and classified duplicates in 9.4986 s, at 44.92 MiB peak traced memory. All
  identities and nine expected duplicates were retained. These are single
  synthetic server-side observations on a contended Mac, not browser-paint or
  production performance guarantees.
- Small fictional export timing: initial rendering 0.156 s; unchanged-input
  reuse 0.073 s, with zero of the three builders called on reuse. The test also
  invalidated on posting, branding, visibility, period, book identity and client
  naming changes, and verified a failed new build removes the old bundle.
- The next small-window browser attempt reached all three simulated providers
  and proved panel geometry (left 80, right 944, viewport 1024; no horizontal
  overflow), then hit a harness race opening opinions before the result existed.
  The harness now waits for the expected opinion count and success/failure state.
  That attempt also exposed the remaining disabled other-provider Retry control,
  now hidden unless a retry applies. Earlier failure artifacts are retained.
- A further browser run reproduced a persistent focused help tooltip covering
  the request button. Local `8eb8b62` removes those tooltip overlays, renames
  the provider-switch action **Choose another AI**, and puts the retry-cost
  reminder in a visible caption only after failure. All 26 affected review UI
  tests passed afterward (120.38 s). The 868-test full run above precedes this
  final label/help-presentation adjustment; accounting/request logic is unchanged.
- At `8e3d734`, inactive panel wrappers also leave the layout and the Include
  column remains one line at 1024 pixels. The complete source-browser journey
  passed 16 checks at 1024×600 with the default 20-second startup allowance.
  This small-window run precedes the saved-confirmation key fix below.
- A new AppTest reproduced confirmation carrying to a newer saved revision
  (1 failed before correction). Resume/Discard controls now use book, client and
  displayed revision identities. The new test checks that both confirmations
  reset on a new revision and client, and that neither saved copy is deleted.
  All 26 recovery/import-state tests then passed (42.31 s). The 868-test full
  run predates this bounded correction; its focused regression is separate.
- Final larger-window browser, packaging and preview evidence follows below.


## Final desktop evidence

Source checkpoint: `8e3d734`. PyInstaller completed the isolated ad-hoc-signed
build under `output/usability-overnight-20260918/dist/LedgerTB.app` (build log:
`output/usability-overnight-20260918/build.log`). No new runtime dependency.

- Final source browser: 16 checks passed at **1360×768**, including the
  saved-confirmation fix, using the default startup allowance. The 1024×600
  16-check result above verified the same final panel/column layout. Screenshots
  and transcripts live in `final-browser-laptop/` and `final-browser-small/`
  beneath the output directory.
- Frozen selfcheck exited 1 with **only** `keyring backend: unexpected
  packaged_fake_vault.Keyring`. The complete failure list implies all 51 imports,
  SQLCipher requirement and Apple Vision OCR completed without reported failure.
  This is the expected test-backend rejection, **not a passing release gate or
  native credential acceptance**. The gate remains unchanged. See `selfcheck.log`.
- The native preview opened the isolated LedgerTB unlock window at 1360×876 logical
  pixels. Exact-PID window metadata and `walkthrough/native-startup.png` verify
  startup; this does not claim a complete native-window walkthrough. The window
  is left open with fresh fictional books for manual review. Existing preview windows
  and the installed v1.7.2 app remain separate.

- Frozen-server Chromium walkthrough: **20 checks passed** at 1360×768 using
  simulated Jev/Anthropic/OpenAI and a fake vault. This covers import totals,
  comparison/reuse/failure/retry, selection/inclusion, a single balanced 3,333-cent
  journal posting with human audit/import identity, all three worksheet export
  formats, saved-copy recovery after posting without another request, and book
  switching with intentionally identical client/account IDs. Both books remained
  encrypted; the second had zero journal entries/imported transactions. The
  harness verified 19 bundled source files against this checkout. Full evidence:
  `output/usability-overnight-20260918/packaged-final/`.
- The first packaged attempt reached posting, exports and recovery, then failed
  a test selector expecting `/var/...` instead of macOS's canonical
  `/private/var/...` recent-book path. The harness now resolves the expected path.
  Its complete rerun passed; no application change or rebuild was required.
  Earlier failure remains under `packaged/` and `packaged.log`.
- Installed `/Applications/LedgerTB.app` remains v1.7.2; its Info.plist and
  executable timestamps match the pre-build-check record. Nothing was pushed,
  released or installed. The preview has a test-only fake-vault module and is
  not a distributable release artifact.

Machine-readable evidence: [cpa-usability-trial-2026-09-18.json](jev-evaluation-results/cpa-usability-trial-2026-09-18.json).
Useful screenshots under `output/usability-overnight-20260918/`: source
`final-browser-small/provider-picker.png`,
`final-browser-laptop/comparison.png`, `final-browser-laptop/saved-review.png`;
packaged `packaged-final/upload-totals.png`, `packaged-final/journal-table.png`,
`packaged-final/saved-review-resumed.png`, `packaged-final/book-switched.png`;
native `walkthrough/native-startup.png`. Earlier screenshot/failure attempts
remain beside their logs, rather than being relabeled as passes.

## Remaining rollout requirements

The isolated synthetic preview supports a CPA usability trial.
Live provider quality/cost qualification, independently adjudicated labels,
provider terms review, real native credential/installed-upgrade acceptance and
Windows native acceptance remain separate. No broader UI redesign or automatic
multi-provider routing is included. Release approval is separate from this
local trial checkpoint. See the release review for final qualification.
