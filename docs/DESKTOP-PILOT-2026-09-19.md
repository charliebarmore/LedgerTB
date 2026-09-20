# Desktop review recovery and pilot verification

Technical work on `codex/jev-overnight`, based on `f8e2755`. Current priorities
and handoff belong in the [LedgerTB project](https://app.notion.com/p/3d7f5bd8d2b981ed91d3fad1a03255ce).
This document is in progress; verification results below must be completed
before treating the pilot goal as finished.

## Recovery and concurrent windows

Migration 027 adds a book generation and per-window review recovery copies.
Changed review evidence and human decisions are checkpointed inside the keyed
SQLCipher book. Unchanged reruns reuse the checkpoint without adding audit
records. These copies are separate from **Save review for later**; automatic
recovery does not replace the explicitly saved review.

Only the saved-review allowlist is serialized, with amounts in integer cents.
Provider credentials, cloud consent, response caches, widget IDs and duplicate
overrides are not restored. Recovery writes require encryption and normal
engine permissions. Every changed checkpoint and deletion is audited in the
same transaction. There are at most 20 copies per client; reaching the limit
leaves existing copies intact and asks the user to discard an unneeded copy.

**Recovery copies** offers checkpoints from other or interrupted windows.
Replacing the active review requires an explicit checkbox. Resuming rechecks
current import history and account/context validity; it neither requests AI nor
posts entries. Exclusion decisions survive. Duplicate overrides require a new
decision. A successfully resumed source copy remains available until discarded.

The native launcher reads a private, atomically replaced file containing only
`review_open: true/false`. It contains no book paths, transaction information or
credentials. When a review is open, normal window close and application quit
show the native confirmation dialog. Cancel returns to the app. The closing
handler performs no JavaScript evaluation or nested dialog call, which avoids
blocking Cocoa's main thread. Missing/corrupt status requires confirmation.

Force-quit and process crashes cannot show a warning. Recovery retains the last
completed server-side checkpoint; edits still in transit or being processed may
not be included. **Save review for later** remains the explicit way to preserve
a known version before quitting. The warning intentionally also covers a saved
review that is still open, so a pending edit cannot silently bypass it.

A successful restore rotates the generation in the prepared encrypted copy,
before its atomic replacement of the live book. Other windows stop at **Reload
restored book** before rendering mutation controls. Reload clears old import,
AI, navigation and page-widget context. Recovery available in the restored book
can then be resumed deliberately. The pre-restore safety backup preserves the
book state that was replaced.

A changed saved-review revision requires resuming the current saved copy or
choosing **Keep this window’s review**. Posting checks both the revision and
book generation on its own connection, holding the write lock through the
commit. A conflicting row remains available for review. A restore cannot race
that connection because of the existing maintenance lease. Upgrade failures
now close the connection in `finally`, releasing that lease for retry/restore.

## Local verification

All automated tests use the existing fake-vault fixture and disposable encrypted
databases. No real keychain, client book, or paid provider is needed.

```sh
.macos-venv/bin/python -m pytest -q \
  tests/test_desktop_review_recovery.py tests/test_launcher_lifecycle.py \
  tests/test_release_upgrade.py tests/test_schema_migrations.py \
  tests/test_import_review_drafts.py tests/test_review_transitions.py
```

Crash workers signal when imports are complete, then retain a 30-second limit
for the interrupted transaction. `LEDGERTB_TEST_STARTUP_TIMEOUT` changes only the
worker's startup allowance (default 60 seconds), not its assertions or transaction
deadline. Under the observed September 19 memory pressure, a diagnostic run uses
600 seconds; this does not establish acceptable product startup time.

AppTest still creates a fresh component manager/registry for each app instance.
The session fixture caches the real installed-component manifest scan because
tests do not change installed packages. It does not cache application state,
widget values, database results or a mocked component list.

The optional native source test controller uses the real Cocoa/WebKit window,
real native dialogs, and the same fake-vault backend as packaged verification:

```sh
.macos-venv/bin/python scripts/packaged_jev_fixture.py \
  --data-dir output/desktop-pilot-20260919/native-books
.macos-venv/bin/python scripts/native_pilot_driver.py \
  --data-dir output/desktop-pilot-20260919/native-books \
  --output output/desktop-pilot-20260919/native-control
```

Fixture creation requires an empty directory. The controller accepts test
command JSON files under its output directory and writes corresponding result
files. It exposes no listening control endpoint and is not included by the
desktop packaging specification. It refuses fixture/output paths outside the
checkout's disposable `output/` tree and requires the synthetic fixture marker.
Any native downloads must be directed to its disposable `downloads/` directory.
The public fictional passphrase is `fictional-packaged-acceptance-only`.

## September 20 verification in progress

The focused run passed 36 checks before an existing saved-review AppTest hit its
60-second limit. A diagnostic rerun passed that UI check and the new recovery
failure check, but reproduced a real older-book read-only startup defect:
`init_database` attempted migrations on a read-only connection. That path now
skips schema writes. The real older-book read-only page and all ten review
transition checks then passed (11 passed, 260.95 seconds).

The focused passes include migration rehearsals from versions 23–26, failed
upgrade/restore recovery, native close-event wiring, encrypted checkpoint crash
boundaries, stale-book posting rejection, and conflicting saved-copy checks.
These are automated results, not native dialog acceptance.

A diagnostic Python stack captured Streamlit's magic AST rewrite spending more
than 45 seconds compiling the import page on this memory-constrained Mac. An
AST audit of `app.py` and every page found no bare expressions requiring magic:
pages render explicitly. `runner.magicEnabled=false` is now set in project
configuration and both launchers. This removes an unused compilation pass;
the clean functional run will verify the UI with it disabled. See Streamlit's
[configuration reference](https://docs.streamlit.io/develop/api-reference/configuration/config.toml).

The source native Cocoa/WebKit preview opened and unlocked a fictional encrypted
book, navigated to Import Transactions, and accepted the synthetic CSV into the
uploader. The parsed review/posting workflow and native dialogs have not yet
been verified. After the idle interval the preview returned to its unlock
screen. Native automation and ordinary local commands remain slow under host
memory pressure. Increasing a diagnostic startup allowance is not a startup
performance pass.

## Evidence to complete

- Focused and clean full functional suite: pending.
- Migration 23/24/25/26 and failed-upgrade/restore focused checks passed; clean full-suite confirmation pending.
- Native close/cancel/quit, import, review, posting, reconciliation, PDF/XLSX: pending.
- Relevant performance checks and fresh isolated frozen Mac build: pending.
- Installed application baseline, local commits, morning walkthrough: pending.

## Walkthrough after the isolated preview passes verification

Use only the fictional Cedar book in the isolated preview. Do not use a client
book to qualify this migration or recovery behavior.

1. Import the fixture CSV into Checking. Categorize the Cedar Paper row as
   Office Supplies and exclude the unknown marketplace row. Selecting rows for
   an action must not change their posting inclusion.
2. Close the native window with the review open. Cancel the warning and verify
   both rows, the category and the exclusion remain. Save the review explicitly.
3. Change a category or inclusion, allow the rerun to finish, then quit and
   reopen. Compare the explicitly saved review with the automatic recovery
   copy. Resume the recovery copy; it must not request AI or post anything.
4. In two windows, save a changed review in one. The other must require an
   explicit saved-copy choice before posting. Restore a disposable backup and
   verify the old window requires reloading the restored book.
5. Post only Cedar Paper. Confirm the journal debits Office Supplies 3,333 cents
   and credits Checking 3,333 cents. Reimporting the fixture must flag the posted
   transaction as a duplicate.
6. Reconcile the fictional Checking account to a statement ending at -33.33.
   Inspect the reconciliation result before completing it.
7. Export PDF and Excel through native dialogs: cancel once, then save into the
   isolated artifact directory and open the resulting files. Verify totals and
   formatting. Windows native dialogs and installed-app upgrade qualification
   remain separate checks.

Initial diagnostic attempts are retained in
`output/desktop-pilot-20260919/`. Crash workers exceeded the initial 30-second
startup-inclusive timeout. The first native launch exceeded the ordinary server
startup allowance before opening a window. These are failed diagnostic attempts,
not acceptance passes. Native Windows, real-vault behavior, and installed-app
upgrade acceptance are separate rollout requirements.
