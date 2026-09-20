# LedgerTB v1.8.0

Release candidate — not published. Final qualification is recorded in
[the release review](RELEASE-REVIEW-1.8.0.md).

## Import review and optional AI

- Request account suggestions from TypeSafe Jev, Anthropic or OpenAI using the
  provider and model controls. You can request another provider's independent
  opinion without replacing an accepted category.
- Cloud requests are optional and require explicit consent. Selected transaction
  evidence, eligible accounts and relevant business context go to the chosen
  provider. Credentials stay in the operating system's credential store.
- Suggestions require human review. Insufficient information, mixed purchases
  and transfers can remain unresolved for manual review. A confidence score
  from Jev describes distribution concentration, not guaranteed correctness.
- Repeated reruns reuse matching results within the current session. Changed
  evidence, instructions, account choices or ledger invalidate reuse. Failed
  requests need an explicit retry. Offline bookkeeping remains available.
- Import review separates selecting rows for actions from including them for
  posting. Compact controls, paging and wider comparisons make larger imports
  easier to review. Dashboard currency, journal lines and account-grouping
  notices are clearer.

## Keeping review work

- Save an encrypted review for later and resume it with duplicate checks. Save,
  Discard and Cancel protect supported client/book switches and replacement
  imports. A failed replacement import leaves the prior review available.
- Automatic encrypted recovery checkpoints are separate from explicitly saved
  copies. They retain completed server-side edits, not edits still being
  processed when the app closes. Continue using **Save review for later** for
  an intentional checkpoint.
- Normal desktop close/quit warns while a review may be open. Force-quit and
  hardware failure cannot be intercepted. Resuming a saved or recovery copy
  neither calls an AI provider nor creates ledger entries.
- A restored book requires old windows to reload. Changed saved-review revisions
  require an explicit choice before posting or replacing the saved copy.
- Read-only books cannot be migrated or modified. Writable upgrades close their
  connection even on failure so retry and restore remain available.

## Worksheet exports and accounting safeguards

- Unchanged worksheet reruns reuse generated export files within the session;
  changed ledger/report/branding inputs invalidate them.
- Integer-cent amounts, balanced posting, import identity and duplicate checks,
  human audit attribution, and engine-enforced assistant permissions remain in
  force. AI suggestions do not change posting inclusion or post automatically.

## Upgrade preparation

Create and verify an encrypted backup before upgrading, then close every window
using that book. This version adds migrations 026 (explicit saved reviews) and
027 (automatic recovery and restore detection). Keep the pre-upgrade backup for
rollback; do not assume an older app understands newer review metadata.

Live AI quality evaluations, native platform acceptance and signing/build results
are described in the release review. Synthetic results do not establish accuracy
on client transactions. This document is not a claim that the release is ready.
