# Assistant access (MCP)

ProBooks can act as a local, permissioned MCP server for an AI assistant on
the same computer (Claude Desktop, Claude Code). Depending on the level you
choose, it can query the books, file proposals for human review, or append new
balanced journal entries.

## Access levels — you choose how much your assistant can do

On Data Safety you pick one of three levels; the choice is stored in the
OS credential vault beside the unlock key, where the assistant's own
connections cannot reach it — the dial is physically outside its world.

| Level | The assistant can… |
|---|---|
| **Read only** | query everything, change nothing |
| **Read + propose** *(default)* | file draft entries and stage imports; you post everything |
| **Read + propose + post** | additionally post balanced journal entries — **append-only** |

Even at the highest level the engine refuses every edit and delete: an
assistant works in ink, never with an eraser. Entries it posts carry
"Posted by assistant (MCP)" and the full audit trail; corrections are
new, visible entries. Changing the level takes effect on the assistant's next
tool call and is audit-logged. Disabling access also revokes the next tool call
from an already-running MCP process.

## Security model

- **Opt-in.** Off until you click **Enable assistant access** on the Data
  Safety page while unlocked. That stores the *derived database key* —
  never your passphrase — in the operating system's credential vault
  (macOS Keychain / Windows Credential Manager). **Disable** deletes it.
- **The selected ceiling is enforced by the database.** Every connection
  carries a SQLite authorizer. Read permits queries (and append-only export
  audit records); propose additionally permits inserts into the draft and
  staged-import inboxes; post additionally permits inserts into journal-entry
  tables. Every update and delete is denied at every assistant level.
- **Drafts, not entries.** The assistant may *propose* a journal entry
  (`propose_entry`). It lands in **Journal Entries → Drafts** for review;
  approving posts a real, audited entry under the approver's name, and
  rejecting marks it rejected while retaining its audit history. The sidebar
  badges pending drafts.
- **Imports, normalized by the assistant.** Drop ANY statement — a weird
  CSV, a PDF, a pasted table — into the assistant and ask it to stage the
  transactions (`propose_import`). No column mapping, no format rules:
  the assistant normalizes, ProBooks stages with full duplicate
  protection, and you categorize and post in **Import Transactions →
  Review & Categorize** exactly as with a CSV upload. Re-proposing the
  same statement stages nothing twice. A person can dismiss unwanted staged
  rows; their identity and audit history remain, but they leave the queue.
- **Shared/custom book files are read-only.** The separate MCP process does
  not yet participate in firm mode's one-writer sidecar lock, so books outside
  ProBooks' local managed-data folder are capped at read access even if the
  stored dial says propose or post.
- **Local only.** The server speaks over stdio to the assistant that
  launched it. Nothing listens on a network port. Your MCP client may send tool
  results to its configured AI provider; approve that provider for client data
  before enabling access.
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

## Pairing with LedgerPDF

`export_close_package` writes the close package (PDF + Excel) into the
**export folder you choose on Data Safety** (stored in the credential
vault, outside the assistant's reach; blank = file export off). The
`PROBOOKS_MCP_EXPORT_ROOTS` environment variable remains as an override
for config-managed setups. See `LEDGERPDF-PAIRING.md` for the full
books-to-binder workflow with both MCP servers in one session.

## What to expect

Amounts are US dollars. Start with `list_clients` to get the
`client_id`. What the assistant can do depends on the level you chose. At the
default level it reads, files drafts, and stages imports — you post
everything. At the "post" level it can also post balanced entries
directly (append-only, audited). Try: *"Here's my July bank statement PDF — stage the
transactions into account 1001"* or *"Propose the accrual for the July
retainer and explain your accounts."* Everything waits for you in the
app.
