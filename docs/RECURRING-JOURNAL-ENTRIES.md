# Recurring and template journal entries — v1.7.0 specification

*Target: LedgerTB v1.7.0. This document is the product and technical contract
for the first release. Changes to the scope or posting behavior should be made
here deliberately before implementation.*

## Outcome

LedgerTB should let an accountant save a balanced journal entry as a reusable
template and optionally attach a schedule to it. A scheduled entry becomes a
pending draft for human review; it never posts itself.

The first release is aimed at fixed recurring amounts such as monthly prepaid
amortization, depreciation entered outside a future asset register, recurring
accruals, and their reversals. The same scheduling service is intended to be
the posting pipeline used by the fixed-asset register in the next feature
release.

## Product decisions

1. **Templates and schedules are separate.** An unscheduled template can be
   loaded into the normal New Entry form as often as needed. Adding a schedule
   makes the template recurring.
2. **Generation creates drafts, never journal entries.** The existing Drafts
   review remains the only path from a generated proposal to the ledger.
3. **Nothing runs in the background.** LedgerTB is a local desktop app. It
   shows what is due and generates selected drafts only after a person chooses
   Generate.
4. **One scheduled occurrence per accounting period.** Database uniqueness,
   not a UI check, prevents a rerun or concurrent click from creating a second
   occurrence.
   If a later frequency change calculates different period boundaries,
   LedgerTB blocks every candidate that overlaps an existing occurrence for
   that schedule. Changing Monthly to Quarterly (or the reverse) never reopens
   dates that were already generated or skipped.
5. **Generated drafts are snapshots.** Later template or schedule edits affect
   future generation only. They never rewrite a pending, rejected, or approved
   draft.
   Each draft generation also retains the template name, source/workpaper
   reference, and reversal instruction used when it was generated. Approval
   uses those saved values, including when the schedule has since changed.
6. **v1 amounts are fixed integer cents.** Formulas, percentages, allocations,
   imported variables, and changing amounts are outside this release.
7. **Only Regular and Adjusting templates are supported.** Beginning Balance
   and Closing entries remain specialized workflows.
8. **A generated draft is not edited in place.** If it is wrong, the reviewer
   rejects it, changes the template, and explicitly regenerates that occurrence.
   The rejected draft remains in history.
9. **No new audit action names are needed.** Template, schedule, occurrence,
   and draft changes use the existing INSERT, UPDATE, and DELETE actions, so
   this feature does not require another audit CHECK rebuild.

## User workflows

### Upgrading drafts created in v1.7.0

Migration 025 adds the saved instructions without rewriting historical rows.
Older primary drafts did not store their original reversal choice or workpaper
reference, so LedgerTB cannot safely infer them from the current template.
Their approval button is disabled with recovery instructions: reject the old
draft, review the template and schedule, then explicitly regenerate the period
in Templates & recurring. Reactivate or restore the schedule/template first if
needed. The new generation captures the reviewed settings and retains the
rejected draft in history.

Existing posted entries are unchanged. Older reversal drafts can still be
approved or rejected/regenerated: their date, opposite amounts, stored
attribution, and posted-primary relationship already establish what they
reverse. Regeneration preserves those saved values.

### Save and use an unscheduled template

- From New Entry, **Save as template** captures the description, entry type,
  source-reference default, accounts, amounts, memos, and line order. It does
  not capture the entry date or an AJE reference.
- Templates can also be created and edited from **Journal Entries → Templates
  & recurring**.
- **Use template** loads a copy into New Entry with today as the initial date.
  The accountant can change any field and posts it through the normal journal
  entry validation. Using a template does not create a recurring occurrence.

### Add a recurring schedule

A schedule defines:

- frequency: monthly, quarterly, or annually;
- first applicable date and optional last applicable date;
- entry-date rule: period end, period start, or a day of the month;
- whether a period-end entry should create a reversal draft dated the next day;
- active or paused state.

One template has at most one schedule in v1. Removing recurrence pauses the
schedule and preserves its history; it does not delete it.

### Generate due drafts

The Templates & recurring view shows a preview grouped into:

- **Due** — eligible through the selected Through date and not yet generated;
- **Already handled** — generated, posted, rejected, or deliberately skipped;
- **Blocked** — missing fiscal calendar, closed fiscal year, inactive account,
  invalid template, or another condition that makes generation unsafe.

The Through date defaults to today. A user may deliberately select a future
date to prepare entries ahead of time. Generate returns an accounted-for result
for every selected occurrence: generated, already generated, skipped, or
errored. Rerunning the same selection is safe.

### Review and post

- A generated primary entry appears in the existing Drafts inbox and is
  labeled with its template, schedule, and accounting period.
- Approval revalidates the accounts, cents, balance, client ownership, and
  closed-period rule, then posts through `JournalEntry.save` in the same
  transaction used to claim the pending draft.
- Every Adjusting draft receives the next fiscal-year AJE reference at human
  approval, whether it came from a recurring schedule, an assistant proposal,
  or another draft workflow. Filing a draft does not reserve a number.
- Rejection keeps the draft and occurrence visible in history.
- A rejected occurrence may be regenerated only through an explicit
  **Regenerate** action. It creates a new draft generation; it does not revive
  or edit the rejected row.
- A rejected reversal has the same explicit recovery path. Its replacement is
  copied from the rejected reversal snapshot—not the template's current
  values—because the reversal must continue to invert the primary that
  actually posted.

### Skip or pause

- **Skip this period** requires a short reason and creates the period's
  occurrence without a draft. It will not continue appearing as due.
- **Undo skip** is allowed while the affected fiscal year is open. It is
  audited and makes the occurrence eligible for generation.
- **Pause schedule** stops future due results without altering existing drafts
  or entries.
- **Archive template** removes it from the default template list and pauses its
  schedule. Templates with history are never hard-deleted.

## Scheduling convention

Schedules use LedgerTB's canonical fiscal-period calendar. Custom periods do
not drive recurrence.

| Frequency | Eligible fiscal periods | Default entry date |
| --- | --- | --- |
| Monthly | Month | Month end |
| Quarterly | Quarter | Quarter end |
| Annually | Year | Fiscal year end |

The service treats periods with identical type, start date, and end date as
one accounting period even if an old book contains duplicate calendar rows.
Occurrence uniqueness uses the period boundaries rather than a mutable period
row ID, so deleting and rebuilding a calendar cannot create a duplicate.
Before showing a newly calculated period as Due, the service also checks it
against every existing occurrence for that schedule. Any boundary overlap is
Blocked with an explanation. This is the idempotency rule when a schedule's
frequency changes after it has history.

Date rules are deterministic:

- **Period end** uses the fiscal period's end date.
- **Period start** uses its start date.
- **Day of month** is available only for monthly schedules. If the chosen day
  does not exist, LedgerTB uses that month's last day and says so in the
  schedule summary (for example, day 31 becomes February 28 or 29).
- The computed entry date must fall on or after the schedule's first date and
  on or before its optional last date.

If the necessary fiscal-year calendar is missing, the page offers an explicit
**Create fiscal calendar** action backed by `FiscalPeriod.ensure_periods_exist`.
Viewing the page never creates periods by itself.

A draft is not generated for a date inside a closed fiscal year. If a fiscal
year is closed after a draft was generated, approval remains blocked by the
existing journal-entry model until the year is reopened.

## Reversal convention

Automatic reversal is available only when the primary schedule uses Period
end. The reversal date is the calendar day immediately after the period end.

The primary draft is generated first. LedgerTB creates the reversal draft only
after the primary draft has successfully posted. That prevents a reversal from
being approved for an entry that was rejected or never reached the ledger.
Creation of the posted primary entry, its draft resolution, and the linked
reversal draft is one database transaction.

The reversal:

- swaps every debit and credit without changing cents or line order;
- preserves the source entry type, including Adjusting, in accordance with
  `docs/EARNINGS-ATTRIBUTION.md`;
- identifies the posted primary journal entry in its description and eventual
  source reference;
- remains a pending draft requiring a separate human approval; and
- is generated once even if the approval response is retried.

Rejecting a reversal does not remove the obligation to reverse the posted
primary. The recurring workspace therefore surfaces rejected reversal drafts
separately and allows an explicit regeneration. The new generation preserves
the rejected reversal's date, type, lines, cents, and primary relationship;
the rejected row remains in history.

If the reversal date has already passed when the primary is approved, the new
draft is shown as overdue; LedgerTB does not silently change its date.

## Data model

Implementation uses a new numbered migration (expected to be migration 024).
Existing migrations are not edited.

### `journal_entry_templates`

- `id` primary key
- `client_id` required foreign key
- `name` required, unique per client among unarchived templates
- `description` required
- `source_reference` optional default text
- `entry_type` constrained to Regular or Adjusting
- `archived_at`, `archived_by` optional
- `created_at`, `created_by`, `updated_at`, `updated_by`

### `journal_entry_template_lines`

- `id` primary key
- `template_id` required foreign key
- `account_id` required foreign key
- `debit_cents`, `credit_cents` nonnegative integer cents
- `memo` optional
- `sort_order` required

The template model validates at least two lines, exactly one side per line, a
nonzero balanced total, client-owned accounts, and an unambiguous line order.
Template lines are replaced only inside the parent template's audited
transaction; the parent audit payload carries the complete before-and-after
line snapshots.

### `recurring_schedules`

- `id` primary key
- `template_id` required unique foreign key
- `frequency`: Monthly, Quarterly, or Annually
- `date_rule`: PeriodEnd, PeriodStart, or DayOfMonth
- `day_of_month` optional, constrained to 1–31
- `starts_on` required and `ends_on` optional
- `reversal_rule`: None or NextDay
- `is_active`
- `created_at`, `created_by`, `updated_at`, `updated_by`

Model validation enforces the compatible combinations described above and a
last date that is not before the first date.

### `recurring_occurrences`

- `id` primary key
- `schedule_id` required foreign key
- period name, type, start date, and end date stored as an immutable snapshot
- `scheduled_entry_date`
- `disposition`: Generated or Skipped
- skip reason, actor, and timestamp when applicable
- generation actor and timestamp when applicable
- unique constraint on `(schedule_id, period_start, period_end)`

Posted and rejected states are derived from the linked drafts instead of being
copied into a second status column that could drift.

### `recurring_occurrence_drafts`

- `occurrence_id` required foreign key
- `draft_entry_id` required unique foreign key
- `role`: Primary or Reversal
- `generation_number` starting at 1
- unique constraint on `(occurrence_id, role, generation_number)`

This link table preserves every rejected and regenerated draft, avoids
overloading correction-only fields on `draft_entries`, and lets the Drafts UI
explain exactly where a proposal came from.

## Service boundaries and transactions

The implementation should keep Streamlit out of the accounting rules:

- `JournalEntryTemplate` owns template validation, persistence, and archive.
- `RecurringSchedule` owns schedule validation and persistence.
- A recurring-entry service previews due periods, generates or skips an
  occurrence, regenerates a rejected occurrence, and creates a reversal after
  primary approval.
- `DraftEntry.save` gains an optional caller-owned connection so an occurrence,
  its draft, its link, and their audit rows commit or roll back together.
- `DraftEntry.approve` consults the occurrence link inside its existing claim
  transaction. For a primary with NextDay reversal, it creates the reversal
  draft before committing.

Each selected occurrence is its own atomic unit. A batch may partially succeed,
but the result must account for every selection and a retry must safely return
Already generated for completed units. On a uniqueness race, the service reads
and returns the winning occurrence instead of surfacing a raw database error.

Template and schedule changes never update draft or journal-entry rows. Posted
journal entries retain the existing append-only and reversal rules.

## Audit and attribution

- Every template, schedule, occurrence, occurrence-link, skip, pause, archive,
  regeneration, and draft mutation is audited in the same transaction as the
  business change.
- Generation is attributed to the OS user who pressed Generate. It is not
  stamped as assistant work and should not create an Assistant Review item.
- `proposed_by` uses plain text such as `Recurring schedule: Monthly rent` so
  the Drafts inbox is understandable without exposing an internal ID.
- The posted journal entry source reference includes the template, accounting
  period, and draft ID. A scheduled reversal also identifies the primary JE.
- The existing correction link remains correction-only; recurring drafts do
  not repurpose `original_entry_id`.

## UI placement and state isolation

Add **Templates & recurring** to the Journal Entries view switcher rather than
adding another top-level sidebar page. It contains:

1. due/blocked occurrence preview with Through date and Generate/Skip actions;
2. active and paused schedules;
3. unscheduled and archived templates; and
4. generation history with links to drafts and posted entries.

The New Entry view gains Save as template and Use template actions. The Drafts
view gains recurring-source labels and history links; its existing approval and
rejection controls stay authoritative.

All template, preview, dialog, selected-row, Through-date, and form-prefill
state is keyed by both book identity and client. Switching a client or book
must clear it. Any widget reset that needs to override browser state uses the
existing generation-nonce pattern and receives a real-browser regression pass.

Read-only books show the same information but disable every mutation with a
plain explanation before the user clicks.

## Assistant access

v1.7.0 adds no MCP tools for creating, changing, generating, skipping, or
approving recurring entries. The existing assistant can still file ordinary
drafts. New recurring tables are not added to the propose or post authorizer
write allowlists.

A later additive read tool may expose templates and occurrence status if there
is a demonstrated workflow. Human approval remains mandatory regardless of
assistant access level.

## Acceptance criteria

### Accounting and lifecycle

- Template and generated-draft validation uses integer cents and rejects every
  unbalanced, zero, negative, both-sided, cross-client, or missing-account line.
- Regular and Adjusting entries post through the existing journal-entry model;
  Adjusting approvals receive the next fiscal-year AJE reference.
- Generated drafts never post without the existing human approval action.
- Reversals exactly invert the approved primary, preserve its type, and cannot
  exist unless the primary posted.
- Pausing, archiving, editing, skipping, rejecting, and regenerating never
  rewrite a posted entry or an earlier draft snapshot.

### Idempotency and calendar behavior

- Repeated and concurrent Generate actions produce one occurrence for a
  schedule and period.
- Frequency changes cannot produce a candidate whose dates overlap any
  existing occurrence for the schedule.
- Repeated primary approval cannot create a second reversal draft.
- Monthly, quarterly, annual, non-December fiscal year, leap-year, day-31,
  historical catch-up, future-through-date, duplicate-period-row, missing
  calendar, and closed-year cases have regression tests.
- A rejected primary or reversal can be regenerated; a pending or approved
  generation cannot be duplicated.
- Skip requires a reason and prevents repeated due reminders until explicitly
  undone.

### Audit, isolation, and upgrade safety

- Every mutation has an audit row written in the same transaction.
- No new audit action is added without a matching CHECK-rebuild migration.
- Template names, accounts, schedules, previews, drafts, and occurrence history
  are client-scoped at every ID lookup.
- Same-client-ID cross-book switching cannot retain or display recurring state.
- Fresh-book and upgraded-book migration tests pass under SQLCipher.
- Assistant read/propose/post authorizer tests prove that none of the new setup
  or occurrence tables is assistant-writable.

### User experience and release gate

- The Due preview explains why blocked items are blocked and what the user can
  do next; it never surfaces a raw exception.
- Batch results account for every selected occurrence.
- A draft shows its template, period, generation, and reversal relationship.
- Read-only mode disables all writes before they reach the model.
- AppTest covers the main flows, with a real-browser client/book switch and
  widget-reset smoke test.
- The full non-performance suite, security checks, Windows built-app serve and
  close smokes, and signed/notarized macOS smoke pass before v1.7.0 is published.

## Explicit non-goals for v1.7.0

- Automatic posting or unattended/background generation
- Formula, percentage, allocation, usage-based, or imported-variable amounts
- Tax depreciation, MACRS, bonus depreciation, or Section 179
- A fixed-asset register or prepaid subledger
- Class, department, location, payee, or project dimensions
- Multiple schedules attached to one template
- MCP mutation tools for templates or schedules
- Email, desktop, or cloud reminders

These exclusions keep the first release small enough to verify while leaving a
stable occurrence-and-draft pipeline for the fixed-asset register that follows.
