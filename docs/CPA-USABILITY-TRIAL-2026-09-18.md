# CPA usability trial: September 18–19, 2026

Current priorities and handoff: [LedgerTB Notion project](https://app.notion.com/p/3d7f5bd8d2b981ed91d3fad1a03255ce).
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

## Verification record

- Initial partial-post regression: 2 failed / 1 passed before correction;
  3 passed after correction (13.02 s).
- Nine-case journey and failure/read-only scenarios: 7 passed (383.05 s).
- Recovery/export/accounting-page run: 47 passed (199.43 s), before final added
  crash/readonly tests and generic journal storage-error handlers.
- Initial 1024×600 workflow: 13 checks passed before the final saved-review and
  retry-button changes. First final-browser attempt failed at fixture startup;
  retained under `output/usability-overnight-20260918/browser-final-small`.
- Final-source full suite, performance, browser, packaging and preview evidence
  will be recorded here after completion.
