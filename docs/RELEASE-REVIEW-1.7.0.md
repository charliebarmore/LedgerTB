# LedgerTB v1.7.0 release review and testing plan

Reviewed September 5, 2026. This is a review and proposed test plan; it does
not change application behavior or certify the desktop installers.

## Release and evidence

- Published release: [v1.7.0, August 30, 2026](https://github.com/charliebarmore/LedgerTB/releases/tag/v1.7.0).
- Tested source: tag `v1.7.0`, commit `e066ef726feb072076a6bce9390f48250035ab4e`.
  Exported with `git archive` into a temporary directory, without local data,
  `.env`, or changes from the working checkout.
- Local environment: macOS, Python 3.12.7, pytest 9.1.1, SQLCipher available.
  `scripts/verify_lock.py` verified all 90 macOS pinned packages.
- Full release suite, including performance: **701 passed, 5 skipped in
  85.88 seconds**. All skips concern Windows NTFS alternate data streams.
- [Windows release CI](https://github.com/charliebarmore/LedgerTB/actions/runs/33305102043):
  **702 passed, 2 skipped, 2 performance tests deselected in 1,141.29 seconds**;
  frozen selfcheck, gzip HTTP checks, and native-window shutdown check passed.
  The Windows skips were not individually investigated in this review.
- Three additional contract probes: **3 failed in 1.27 seconds**, described
  below. These were run separately after collection of the existing suite.
- Evidence and reproduction source are in
  `output/release-review-1.7.0/`. Test databases were temporary; the probes
  use the existing fake credential-vault and synthetic client fixtures.

The checkout is one commit ahead of the release: `1a9a0db` exposes the stored
draft `entry_type` through MCP and adds a regression assertion. That fix is
not in the published v1.7.0 tag. The recurring code implicated below is
unchanged between the tag and this checkout.

## Findings

### P1: Editing recurrence changes an already-generated draft's reversal behavior

Reproduction:

1. Save an Adjusting accrual template and a monthly period-end schedule with
   next-day reversal enabled.
2. Generate January's draft but leave it pending.
3. Disable reversal on the schedule.
4. Approve the January draft.

Expected under the documented snapshot contract: the existing primary retains
its generation-time reversal instruction and creates one February 1 reversal
draft. Actual: it creates none. The opposite edit also reproduces: generating
with reversal disabled, then enabling it before approval, creates a reversal
that was not part of the original generation. The extra reversal remains a
draft requiring approval; it is not automatically posted.

Cause: `services/recurring_entries.py:691` joins the current schedule and reads
`rs.reversal_rule`; `create_reversal_after_primary_approval` uses that value.
The occurrence/draft link does not snapshot this instruction. The contract in
`docs/RECURRING-JOURNAL-ENTRIES.md:36` says later schedule/template edits affect
future generation only.

Recommended correction: persist generation-time reversal instructions and
read them when approving that generation. Use a new numbered migration.
Define a conservative policy for existing pending drafts whose original
instruction was never stored; do not imply their history can be reconstructed
with certainty. Test both toggle directions, unchanged controls, rejected
primary regeneration, and reversal regeneration.

### P2: An older draft takes a later template's workpaper reference

Generate a draft from a template referencing `Original workpaper`. Change the
template name and reference to `Future accrual` / `Future workpaper`, then
approve the original draft. The posted source reference is:

`Recurring · Future accrual · FY 2026 - Jan · Draft #1 · Future workpaper`

The old reference is absent. Amounts remain the original snapshot, but its
posted supporting reference describes the revised template. This weakens the
historical link between the entry and its supporting workpaper.

Cause: the same context query reads live template metadata, which
`models/draft_entry.py:270` uses during approval. Snapshot the relevant
reference/name for each draft generation and preserve it in posted lineage.
The existing snapshot test checks changes to amounts, so it misses this case.

### Release follow-up: MCP draft types

v1.7.0 omits `entry_type` from `list_drafts` responses. The checkout already
contains the fix. Carry it into the next release and test actual MCP response
payloads for Regular, Adjusting, and Beginning Balance drafts, including
pending and resolved states. A consumer should never infer an accounting type
from an absent field.

## What already works well

The suite covers much more than ledger arithmetic: encrypted reopen, backup
verification and failed restore, passphrase-change failures, client isolation,
engine-enforced assistant permissions, audit atomicity, import identity,
report/export tie-outs, close review, and process locks. Recurrence has 22
service/model tests plus page and permission tests, including concurrent
generation, leap years, noncalendar fiscal years, overlap prevention,
rejected-reversal recovery, and rollback on reversal creation failure.

The volume tests exercise 10,000 journal entries and a 50,000-row CSV. They
passed here; they are broad tripwires, not interactive response-time promises.
The release's existing 50,000-row test took 9.07 seconds including parsing and
classification assertions.

## Prioritized testing work

| Priority | Work | Acceptance evidence |
| --- | --- | --- |
| First | Turn the reproduced snapshot failures into regression tests alongside their fixes | Both reversal toggle directions and original workpaper lineage stay correct after edits; future generations adopt the edits |
| First | One synthetic client from first launch to completed close | Independently specified expected balances match the UI, MCP, PDF, and Excel; reload/restart preserves them |
| First | Real-browser journeys with actual navigation and client selection | Switching clients/books, loading templates, posting, back navigation, refresh, and retry do not retain the wrong form values or produce duplicate entries |
| First | Upgrade a populated v1.6.3 book to v1.7.0 and its successor | Historical rows and report totals survive; migration applies once; backup and restored copy open and tie out |
| Next | Crash and retry across process boundaries | Interrupt generation, primary approval/reversal creation, posting, and restore at controlled checkpoints; reopen yields a complete commit or rollback, with no duplicate or half-linked history |
| Next | MCP over a real stdio session | Discovery, JSON payloads, permissions, actor stamps, reconnection, errors, and approved export roots work through the transport |
| Next | Generated action sequences and accounting properties | Randomized but repeatable dates, cents, fiscal years, edits, approvals, rejection, skips, and retries preserve independent invariants |
| Later | Performance history and export visual review | Retained timing/memory results and readable representative multi-page exports; regressions compared on equivalent machines |

This extends existing protection. For example, there are already exception
rollback tests and a killed-process lock-release test; the missing extension
is interruption during complete accounting workflows. Schema tests cover
fresh creation, idempotency, and failure atomicity, but a populated book made
by a prior release adds a different kind of evidence.

## First complete accounting scenario

Use a fictional client, **Cedar Demo Services**, with a calendar fiscal year
and these accounts: Cash, Accrued Expenses, Owner Capital, Service Revenue,
Office Expense, and Rent Expense. Use a separate empty client and a second
book to test selection boundaries. All expected amounts below are specified
independently of the app's report code; machine fixtures should use cents.

1. January 1, 2026: opening cash and capital of $10,000.
2. Import and review two bank rows: January 5 customer receipt $2,500 and
   January 8 office payment $300. Confirm both rows are accounted for and post
   them. Reimport the exact same source; no additional journal entries post.
3. Generate a January 31 Adjusting rent accrual of $1,200, debit Rent Expense /
   credit Accrued Expenses, with a February 1 reversal draft after approval.
4. Before approval, confirm the accrual has no effect on ledger balances.
   Approve once, then retry; only one primary posts and one reversal is pending.
5. Reconcile January bank activity against an ending statement balance of
   $12,200, with no outstanding items.
6. Compare January's reports and exported close package against this oracle:

| January 31 balance | Debit | Credit |
| --- | ---: | ---: |
| Cash | $12,200 | |
| Office Expense | $300 | |
| Rent Expense | $1,200 | |
| Accrued Expenses | | $1,200 |
| Owner Capital | | $10,000 |
| Service Revenue | | $2,500 |
| Total | $13,700 | $13,700 |

Expected January net income is $1,000. Assets are $12,200; liabilities are
$1,200; equity including current earnings is $11,000. There are four posted
entries if each bank row posts separately. Opening cash plus $2,500 receipts
less $300 disbursements equals ending cash. Verify individual balances and row
identity as well as a balanced trial balance: omitted transactions can leave
the trial balance balanced.

7. Approve the February 1 reversal: there are five posted entries, the accrual
   liability is zero, and cumulative rent expense is zero through February 1.
   January reports remain unchanged. Both Adjusting entries have distinct
   fiscal-year AJE references.
8. Complete supporting review, export PDF/Excel, create a verified backup,
   close and reopen the app, then restore into an isolated test environment.
   Compare posted entries, amounts, types, occurrence links, draft statuses,
   and prior audit history. Allow the documented new restore audit event.

Branch from this fixture for: edit before approval; reject/regenerate; close
the fiscal year between generation and approval; pause/archive; change
frequency; import interruption/retry; and a June year-end client. Check that
generation alone never posts and rejected records remain in history.

## Test cadence and release evidence

- **Each code PR:** retain the existing fast Linux suite; add targeted contract
  tests and a small real-browser path for affected workflows. Keep the browser
  server, book location, fixture identities, and credential backend isolated.
- **Scheduled or pre-release:** pinned macOS and Windows suites, complete
  browser journeys, prior-version upgrade/restore, stdio MCP, and volume tests.
  Retain JUnit results, failing browser traces/screenshots, and timings. The
  Windows release suite already takes about 19 minutes, so measure before
  adding every expensive scenario to every PR. Do not blindly parallelize tests
  that share process-global state or common helper resources.
- **Each release candidate:** test the actual downloadable installer/zip on
  disposable OS profiles. Cover first launch, remembered passphrase, upgrade,
  real native file dialogs, export, shutdown, reopen, uninstall/reinstall, and
  preserved book data. Windows testing should include a real browser download
  to exercise download-origin handling. Use the final release assets rather
  than an older build-spike artifact.
- **Human acceptance:** one Windows and one Mac tester complete the Cedar
  scenario without coaching; a second accountant checks the exported package
  against the expected figures. Record confusion and time to first correct
  close, as well as errors. Keep OS-vault acceptance in disposable profiles;
  automated pytest tests must continue to fake the vault.

Release evidence should identify the tag/SHA, lock file, asset digest, OS,
scenario, expected result, observed result, and disposition. Require no
unexplained missing/duplicate postings, changed historical support, client/book
leakage, unrecoverable upgrade, or broken restore. Explain every skip.

The best next implementation slice is the snapshot correction plus the Cedar
scenario, followed by real-browser automation of that same scenario. Coverage
percentages can help locate unvisited code later; these reproduced failures
show why the first goal should be stronger behavioral assertions.

## Review limits

No installer was downloaded or launched, no native credential-vault acceptance
was performed, and no real-browser journey was executed in this review. Mac
results are source-level tests with verified pinned dependencies, not proof
that the distributed bundle behaves identically. No live Anthropic request or
external MCP client session was used. No application fix, CI change, PR, or
release was published.
