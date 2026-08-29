# Earnings attribution — the convention

*Decided for v1.6.3. This is the rule every statement, the Close Map, and the
close package follow. Change it only deliberately, with the `test_earnings_attribution_*` regression
tests in `tests/test_reports.py` updated to match.*

## The rule

LedgerTB derives two synthetic equity lines instead of requiring closing
entries: **Retained Earnings** (prior years) and **Current Year Earnings**
(the open year).

1. **Current Year Earnings always equals the income statement's net income**
   for the fiscal year containing the as-of date. Both are computed from
   ordinary activity only: journal entries other than Beginning Balance and
   Closing entries.
2. **Beginning Balance and Closing entries' revenue and expense legs always
   feed the synthetic Retained Earnings line**, whatever date the entry
   carries. They never touch Current Year Earnings.

Together the two rules keep the balance sheet balanced (every P&L dollar
lands in exactly one of the two lines) and make the balance sheet agree with
the income statement by construction.

## Why, in accounting terms

- A Beginning Balance entry that credits revenue records **earnings that
  predate the books** — conversion-date year-to-date income on a mid-year
  conversion. That is opening equity, not activity of the period, which is
  exactly what Retained Earnings means. The income statement already
  excludes it; now the balance sheet files it in the same place.
- A Closing entry **moves accumulated earnings into a real equity account**.
  Its P&L legs cancel the prior activity inside the synthetic Retained
  Earnings line, so the real equity account it credited stands alone and
  nothing is counted twice — including a closing entry posted after
  year-end, when the return is finished (dated January, or March).

## Documented consequences

- **Conversion year:** the income statement and Current Year Earnings both
  mean "since the conversion date." Pre-conversion income sits in Retained
  Earnings. This matches how mainstream bookkeeping software treats opening
  balances.
- **Closing entry dated inside the year it closes:** Current Year Earnings
  still shows the full year's net income (it ties to the income statement),
  and the synthetic Retained Earnings line carries the offsetting debit next
  to the real equity account the close credited. Net equity is unchanged.
  LedgerTB statements are always pre-closing-style within the open year;
  "post-closing" presentation (Current Year Earnings at zero) is deliberately
  not offered.

## The detection control

The close package Summary carries a tie-out: income-statement net income
must equal the balance sheet's Current Year Earnings for any
fiscal-year-to-date package (the period starts at the fiscal year and ends
inside it). Other periods report the comparison as not applicable instead
of failing.

One rule this depends on: **a reversal keeps the entry type of the entry it
reverses** (`JournalEntry.reverse`). A Regular-typed reversal of a Closing
or Beginning Balance entry would land on the ordinary-activity side and
double-count income instead of netting the pair to zero.
