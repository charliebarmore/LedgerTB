# Recoverable fictional monthly close — September 19, 2026

Local implementation and acceptance evidence for LedgerTB in `LedgerLabs/ProBooks`,
branch `codex/jev-overnight`, based on `c647f7a`. Current business status and next
actions belong to the [existing Notion project](https://app.notion.com/p/3d7f5bd8d2b981ed91d3fad1a03255ce).
Nothing in this pass was published, installed over Charlie's app, or run against a
real client ledger or real credential vault. No paid provider requests.

Production changes are committed locally in `c4ab546`. Machine-readable evidence:
[recoverable-month-close-2026-09-19.json](jev-evaluation-results/recoverable-month-close-2026-09-19.json).

## Changes and reproduced defects

- The active review now says whether its evidence and decisions differ from the
  saved encrypted copy. Display sorting does not make it dirty. Pending inclusion,
  transfer and category widget changes are captured before a transition.
- Changing clients/books or replacing an import offers **Save and continue**,
  **Discard changes**, and **Cancel** when work is unsaved. The old context stays
  active until resolved. Save errors and revision conflicts leave both the review
  and pending replacement intact. Read-only books cannot save.
- A replacement CSV is parsed and validated separately. A reproduced parse failure
  previously erased the current review before returning an error; it now leaves
  that review intact. Statement replacement uses the same transition guard.
- Clearing the review also uses the guard. Streamlit's unmounted navigation widget
  is reseeded from the persistent selected view, so Cancel returns to Review.
  Client cancellation rotates its widget key; this was verified in a real browser.
- A successful backup restore clears this window's obsolete import/review/AI state.
  A reproduced failure showed the old review surviving restoration. Failed restores
  retain it. The restore confirmation explains that unsaved work is cleared and a
  saved copy is retained in the pre-restore safety backup.

No schema or runtime dependency change. The new guard module is explicitly included
in frozen selfcheck. Integer-cent posting, audit ownership, optimistic saved-review
revisions, import identities and assistant permissions retain their existing engine
boundaries. Suggestions still require acceptance and do not alter posting inclusion.

## Independent fictional-month specification

`tests/helpers/month_close.py` defines Juniper Month Services, a fictional sole
proprietor, and two independently labeled source files. Labels are explicit human
review decisions, **not provider predictions or evidence of AI accuracy**.

| Source | Cases | Rows |
|---|---|---:|
| Checking | Customer receipts | 100 |
| Checking | Office purchases / refunds / fees | 30 / 5 / 5 |
| Checking | Card payments / owner contributions | 4 / 2 |
| Checking | Mixed purchase / missing receipt / duplicate receipts | 1 / 1 / 2 |
| Card | Software / stationery purchases | 70 / 20 |
| Card | Refunds / payment mirrors / mixed purchase | 5 / 4 / 1 |
| **Total** | **150 checking + 100 card** | **250** |

Checking CSV uses negative payments; card CSV uses positive charges. The importer
normalizes both. Two duplicate receipts and four card-side payment mirrors are
excluded; the four checking-side transfers book both accounts exactly once.
Three purchases remain uncategorized pending human evidence/allocation.

The opening entry is $5,000 checking/capital. The final human allocation records
$40 and $20 of the mixed purchases as owner draws using separate adjusting entries;
this is the existing two-line import plus AJE workflow, not a new split-posting UI.
A fictional receipt supports the $75 repair. A third AJE accrues $300 wages.

| Account | Debit cents | Credit cents |
|---|---:|---:|
| 1000 Checking | 1,506,500 | 0 |
| 2000 Credit Card | 0 | 130,000 |
| 2100 Accrued Wages | 0 | 30,000 |
| 3000 Owner Capital | 0 | 600,000 |
| 3100 Owner Draw | 6,000 | 0 |
| 4000 Service Revenue | 0 | 1,000,000 |
| 6100 Office Supplies | 71,000 | 0 |
| 6200 Software | 138,000 | 0 |
| 6300 Bank Fees | 1,000 | 0 |
| 6400 Repairs | 7,500 | 0 |
| 6500 Wages | 30,000 | 0 |
| **Total** | **1,760,000** | **1,760,000** |

Independent arithmetic: checking is
`5000 + 10000 - 375 + 25 - 10 - 400 + 1000 - 100 - 75 = 15065` dollars.
Office expense is `375 - 25 + 300 + 100 - 40 = 710`; software is
`1400 - 50 + 50 - 20 = 1380`. Total expenses are $2,475 and net income $7,525.
Assets $15,065 equal liabilities $1,600 plus equity including income $13,465.

Expected final population: **244 imported entries + opening + 3 AJEs = 248 entries**.
Checking reconciles 149 lines including opening; card reconciles 100 lines including
the four checking-side transfer legs. Both differences are zero. Canonical statement
groupings, Close Map evidence/signoffs, and PDF/XLSX close outputs are verified.
Close Map uses the existing fiscal-year review scope; this test does not close the
fiscal year or introduce a monthly posting lock.

## Verification and evidence

All artifacts below are under `output/month-close-20260919/` (local, uncommitted).

- Full functional run: **879 passed, 5 Windows-only skips, 3 failures** in 373.64s.
  The three failures were newly added tests passing AppTest's `SafeSessionState`
  directly to a mapping helper; its public `.filtered_state` mapping fixes the
  harness. Final `guard-final.log`: **10 passed** in 18.29s, including those three.
  **882 unique functional cases passed across these runs**, not a clean single-run
  claim. Existing deadlines and accounting assertions were not relaxed.
- All **3 performance checks passed** at their existing thresholds in 36.02s.
- Full monthly close test passed in 16.45s, with actual Streamlit review posting,
  encrypted database assertions, exports and restore. See `close-final.log` and
  `close-evidence/test_month_close_partial_post_0/`: `verified-close.json`,
  `juniper-close.xlsx`, `juniper-close.pdf`.
- The close test injects failure after journal insertion but before its imported
  record commits: 240 completed posts remain, the failed transaction fully rolls
  back, and four rows remain. Retry posts just the failed row. Saving and resuming
  the three unresolved rows retains identity/evidence; a competing stale revision
  is refused. Final re-import flags 246 already-posted/duplicate source rows.
- A same-day $100 purchase also matches the conservative journal duplicate warning
  against a distinct $100 receipt after resume. The test explicitly reviews and
  overrides that warning. It does not weaken duplicate matching to make the fixture
  pass. Duplicate overrides are intentionally rechecked rather than saved.
- Restoring the encrypted backup returns exact balances, completed reconciliations,
  Close Map signoffs and saved-review revision after an intervening ledger mutation.
  Integrity check is `ok`; all entries balance; attribution remains human and the
  other fictional client stays empty.
- Real-browser source checks: 250-row resume, unsaved inclusion retained after
  client Cancel, Save-and-switch plus return/resume, book-switch Cancel, and
  **241 reviewed posts with three unresolved rows retained**. Screenshots/text
  captures: `month-review`, `client-guard`, `client-cancel`,
  `resumed-after-client-switch`, `book-guard`, `month-partial-post`.
- Abruptly stopped only the disposable source server with SIGKILL after saving
  the three unresolved rows, restarted it, unlocked and resumed through the
  browser. All three remained uncategorized. Separate SQLCipher inspection found
  exactly 241 posted imports / 242 entries including opening, zero entries for the
  other client, encrypted header and `integrity_check=ok`. No provider requests.
  `restart-recovered.png`, `restart-recovered.txt`, `browser-month-evidence.json`.
- Fresh isolated Mac build passed **22 frozen full-app acceptance checks at
  1360×900**, with **23 bundled source files matching the checkout**. Includes
  three simulated providers, independent opinions, failure/retry/reuse, consent,
  selective balanced posting, audit/identity, exports and cross-book isolation.
  See `frozen-1360/result.json`, database evidence and browser transcript.
- Frozen selfcheck reports only the expected `keyring.backends.fail.Keyring`
  backend rejection; all **52 imports**, SQLCipher and Apple Vision OCR report no
  failure. This is not a passing real-keychain release gate. `selfcheck.log`.
- Native startup separately verified at 1360×900: `walkthrough/native-startup.png`
  and exact-PID `native-windows.json`. This does not claim the full workflow was
  driven through the native webview. Installed v1.7.2 version/timestamps match the
  baseline (`installed-app-unchanged.json`); earlier previews remain untouched.

## Reproduce locally

```sh
.macos-venv/bin/python -m pytest -q tests/test_review_transitions.py tests/test_data_safety_page.py tests/test_import_review_drafts.py tests/test_month_close_workflow.py
.macos-venv/bin/python -m pytest -q -m 'not performance'
.macos-venv/bin/python -m pytest -q -m performance
.macos-venv/bin/python scripts/month_close_fixture.py --data-dir /tmp/juniper-new-empty-directory
```

Fixture creation refuses a nonempty directory, disables the real vault before app
imports, requires SQLCipher, and produces two disposable books, CSVs and labeled
`expected.json`. Do not point the regular app at this directory without applying
the isolated fake-vault launch environment used by the preview/harness.

The fresh native preview is open at unlock. Reopen **from the repository root**:

```sh
.macos-venv/bin/python output/month-close-20260919/walkthrough/launch.py
```

Use `fictional-packaged-acceptance-only`. This is a public fictional test passphrase.
The launch wrapper applies the fake vault and disposable data directory. Launching
the bundle directly would omit those isolation settings.

## Walkthrough

1. Unlock the separate preview. Choose Juniper Month Services, then Import
   Transactions → Review & Categorize → Saved review → Resume saved review.
2. Confirm **250 total, 244 included, 3 uncategorized, 2 duplicates**. The saved
   fixture contains labeled human choices; no AI calls have been made.
3. Change one Include checkbox. Try switching to Willow. Cancel should return to
   Juniper with that checkbox unchanged. Save and continue should retain a saved
   copy in Juniper; return and resume it. Restore the checkbox before posting.
4. Try a replacement CSV or Switch book from Data Safety while the review has
   unsaved edits. Inspect Save/Discard/Cancel. Discard leaves the older saved copy;
   saving does not post transactions. Browser/native force-quit cannot prompt:
   explicitly save before closing.
5. Post the ready rows. Expect **241 posted, 3 needing a category, 6 excluded**.
   Save the three unresolved rows. Close/reopen the isolated preview and resume.
   Duplicate warnings may change because current journal history is rechecked.
6. Review the fictional receipt/allocation facts above. Choose Office Supplies,
   Repairs and Software for the three purchases; explicitly review any duplicate
   warning. Post, then record the three documented AJEs. Do not treat AI confidence
   as a substitute for the purchase evidence or allocation.
7. Reconcile checking to $15,065 and the card to $1,300. Review the canonical account
   groupings and January reports against the table above. Generate the close
   package and compare it with the automatically verified exports.

Unsaved work is session-only. Saved reviews are explicit checkpoints, not automatic
draft synchronization. Native exit interception, other-window live-state recovery
after restoring a backup, real credential/installed-upgrade qualification, Windows
native acceptance, independently adjudicated AI labels and live-provider terms/
quality/usage/cost checks remain outside this local synthetic acceptance. Separate
approval is still required for release or installed-app replacement.
