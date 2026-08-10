# LedgerTB + LedgerPDF — books to binder

LedgerTB keeps the books; [LedgerPDF](https://ledgerpdf.com) builds the
workpaper binder. LedgerPDF is a separate product, not part of this
repository — this page only matters if you have it. Both are local-first,
both expose an MCP server, and both attribute and audit everything an agent
does — so one Claude session can close the books in one app and support them
in the other, with a human reviewing at both ends.

## The workflow

1. **Close in LedgerTB.** Post the period (or review your assistant's drafts
   and staged imports), run Book Review, verify the trial balance.
2. **Export for the binder.** The assistant calls LedgerTB's
   `export_close_package` — the branded close-package **PDF** plus the
   **Excel workbook** (Summary, Trial Balance, Transactions, Adjusting
   Entries, Receipts & Disbursements) land in a folder you approved.
3. **Assemble in LedgerPDF.** The assistant ingests that folder
   (`binder_add_folder`), together with whatever else the engagement has —
   bank statement PDFs, receipts, memos.
4. **Tie it out.** LedgerPDF reads spreadsheet cells exactly
   (`binder_read_cells` — the Excel totals are computed values, never
   uncalculated formulas), so the assistant can foot columns
   (`binder_foot`), tie figures across documents (`binder_tie`), place
   tick marks where the figures sit, drop calculator tapes that carry
   their addends, and flag anything that doesn't agree
   (`binder_add_note`) — while cross-checking any number against the live
   books through LedgerTB's `trial_balance` / `general_ledger` /
   `entry_detail`.
5. **Cover and review.** `binder_add_cover` writes the summary as page 1 —
   facts read from the binder, narrative by the assistant — and the
   review queue lists what's waiting on a human. Every agent mark exports
   attributed (e.g. `CJB (AI)`), and both apps keep their own audit
   trails.

## Setup

Both MCP servers in one Claude Code session:

```bash
claude mcp add ledgertb --env LEDGERTB_MODE=mcp \
  -- /Applications/LedgerTB.app/Contents/MacOS/LedgerTB

claude mcp add ledgerpdf -e WPT_MCP_ROOTS=/path/to/engagements \
  -- node <ledgerpdf>/app/out/mcp-server.cjs
```

Then choose LedgerTB's per-book export folder on **Data Safety → Assistant
access** (no config editing — it's stored with that book's access level) and
point it at the same engagement folder as LedgerPDF's `WPT_MCP_ROOTS`:
LedgerTB may write exports there, LedgerPDF may read sources from
there, and neither can touch anything outside it.

## The consent model, stated plainly

- LedgerTB's assistant access is opt-in with a user-chosen level, and its
  ledger writes are append-only at most. File exports only land inside
  the export folder chosen on Data Safety (an env-var override exists
  for managed setups) and are audit-logged.
- LedgerPDF's file access is off until `WPT_MCP_ROOTS` names approved
  folders.
- Client data flowing to a hosted model is a disclosure decision (in the
  US, an IRC §7216 decision) that belongs to the professional, not to
  either tool. Both default closed so that the decision is always made,
  never assumed.
