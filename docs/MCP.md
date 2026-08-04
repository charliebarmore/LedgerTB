# Assistant access (MCP)

ProBooks can act as a local, **read-only** MCP server, letting an AI
assistant on the same computer (Claude Desktop, Claude Code) query the
books: trial balance, income statement, balance sheet, general ledger,
journal-entry search and detail, and the deterministic integrity sweep.

## Security model

- **Opt-in.** Off until you click **Enable assistant access** on the Data
  Safety page while unlocked. That stores the *derived database key* —
  never your passphrase — in the operating system's credential vault
  (macOS Keychain / Windows Credential Manager). **Disable** deletes it.
- **The ledger is unreachable by construction.** Every database
  connection the server opens carries a SQLite authorizer that permits
  reads everywhere and writes to exactly one table: the draft inbox. A
  ledger write fails at the engine, regardless of what any tool or
  prompt tries.
- **Drafts, not entries.** The assistant may *propose* a journal entry
  (`propose_entry`). It lands in **Journal Entries → Drafts** for review;
  approving posts a real, audited entry under the approver's name, and
  rejecting discards it. The sidebar badges pending drafts.
- **Imports, normalized by the assistant.** Drop ANY statement — a weird
  CSV, a PDF, a pasted table — into the assistant and ask it to stage the
  transactions (`propose_import`). No column mapping, no format rules:
  the assistant normalizes, ProBooks stages with full duplicate
  protection, and you categorize and post in **Import Transactions →
  Review & Categorize** exactly as with a CSV upload. Re-proposing the
  same statement stages nothing twice.
- **Local only.** The server speaks over stdio to the assistant that
  launched it. Nothing listens on a network port.
- Enabling and disabling are recorded in the audit trail.

## Setup

1. ProBooks → **Data Safety → Enable assistant access**.
2. The page then shows the exact JSON for your machine. In Claude
   Desktop: Settings → Developer → Edit Config, add it under
   `mcpServers`; in Claude Code: `claude mcp add probooks -- <command>`.
   Installed-app form:

   ```json
   {
     "mcpServers": {
       "probooks": {
         "command": "/Applications/ProBooks.app/Contents/MacOS/ProBooks",
         "args": [],
         "env": { "PROBOOKS_MODE": "mcp" }
       }
     }
   }
   ```

   (On Windows, `command` is the path to `ProBooks.exe`. Running from
   source: `command` is your Python, `args` is the path to
   `mcp_server.py`, no env needed.)
3. Restart the assistant. Ask it something real: *"Run the integrity
   sweep on client 1 for Q2 and explain anything it finds."*

## What to expect

Amounts are US dollars. Start with `list_clients` to get the
`client_id`. The assistant can read and reason about the books, file draft entries,
and stage imports for your review — but it cannot post, edit, or delete
anything itself. Try: *"Here's my July bank statement PDF — stage the
transactions into account 1001"* or *"Propose the accrual for the July
retainer and explain your accounts."* Everything waits for you in the
app.
