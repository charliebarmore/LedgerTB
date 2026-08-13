# Close Map

The Close Map connects each year-end balance to its support and human review.
It is deliberately account-centric: LedgerTB owns the balances, explanations,
status, and references; a workpaper binder such as LedgerPDF owns the documents.

## Review flow

Every account with a nonzero current- or prior-year balance appears in the map
and requires review by default. A reviewer can:

1. assign the reusable lead-sheet group;
2. explain the balance and prior-year change;
3. add one or more workpaper, LedgerPDF, external-file, or reconciliation
   references;
4. open and resolve review notes;
5. mark the account prepared, then reviewed.

An account can be marked **Not required**, but only with a written reason. The
reason is retained in the audit trail and close package.

## Signoffs do not hide later changes

Each signoff stores a fingerprint of that account's ledger lines through the
fiscal year end, adjusting entries, reconciliations, mapping, explanation,
evidence, and review notes. If any of those inputs changes, the prior signature
stays in history and the current status becomes **Changed**. Only the affected
account is reopened; unrelated ledger activity does not disturb completed work.

Signoffs are append-only and human-only. The assistant may submit a proposed
explanation, but a person must accept it, prepare the account, and review it.

## Fiscal close and exports

An out-of-balance trial balance remains a hard close block. Incomplete Close
Map accounts are visible warnings: closing with them requires explicit
acknowledgement, and the counts are captured in the close audit event.

Annual close-package Excel and PDF exports include the Close Map. They show
balances, prior-year comparisons, status, references, notes, explanations, and
the latest preparer and reviewer. LedgerTB stores references rather than copies
of supporting documents, keeping the LedgerTB/LedgerPDF product boundary clear.
