# Packaged macOS reconciliation and export acceptance

Verified September 22, 2026 on macOS 26.6.2 using the qualified v1.8.0 bundle.
Artifact identities are in the [release review](RELEASE-REVIEW-1.8.0.md).
All accounting data was fictional and stored in a disposable encrypted book.

## Method

The packaged binary ran in server mode with headless browser automation. A
separate Cocoa window exercised the native save panel against the same server.
This was not a complete native-window walkthrough. Provider requests were
suppressed; the fixture did not save credentials to the operating system vault.

## Results

- Posted one balanced entry: Office Supplies debit 3,333 cents, Cash credit
  3,333 cents. Trial balance totals were 3,333 cents on each side.
- Reconciled Cash to a statement ending at -$33.33. Clearing the entry reduced
  the difference to $0.00 and reconciliation history showed Completed.
- Canceling the native Excel save left no file and the application responsive.
- Saved a one-sheet trial balance workbook, an eight-page close-package PDF
  and a nine-sheet close-package workbook. All opened successfully, contained
  the fictional client name and agreed to the independently specified totals.
- The audit trail attributed the 27 records to the human OS user; none carried
  assistant attribution. The application bundle remained signed and unchanged.

## Limits and follow-ups

- A canceled export still records an EXPORT audit event when generation starts;
  that event does not prove the user saved a file.
- Programmatic save-panel acceptance saved to the default Downloads directory,
  rather than the requested test directory. Choosing a destination manually
  remains a separate native check; no claim of that interaction is made here.
- Reconciliation history displayed UTC while Audit Trail displayed local time.
- Raw screenshots, process inventories, vault metadata and machine-specific
  logs are not public release documentation. Retain only sanitized evidence.

To repeat, create a fictional encrypted book, post the balanced entry above,
reconcile it, cancel one native export and inspect all three saved formats.
Keep test exports and databases in an ignored disposable directory.
