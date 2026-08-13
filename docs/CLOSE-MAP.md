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

LedgerTB requires a current-period explanation and at least one current-period
evidence reference before the preparer can sign off. An account cannot reuse a
prior-year reference to satisfy that control.

An account can be marked **Not required**, but only with a written reason. The
reason is retained in the audit trail and close package.

## Year-to-year roll-forward

Lead-sheet groups and account assignments are reusable client settings, so they
are already in place when a new fiscal year is created. The immediately
preceding fiscal year's explanation, evidence references, review notes, and
preparer/reviewer history appear in the account panel as read-only context.

That context is there to help the current-year team understand what was done
and what changed. It does not create a current-year review, copy evidence, or
carry a signature forward. The new year starts **Not started** until a person
saves a current explanation, adds fresh support, and completes new preparer and
reviewer signoffs. If the adjacent prior fiscal year does not exist, LedgerTB
does not pull older context across the gap.

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
