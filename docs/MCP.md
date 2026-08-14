# Assistant access (MCP)

LedgerTB can act as a local, permissioned MCP server for an AI assistant on
the same computer (Claude Desktop, Claude Code). Depending on the level you
choose, it can query the books, file proposals for human review, or append new
balanced journal entries. Access is granted separately for each book; opening
a different book never carries the previous book's permission with it.

## Access levels — you choose how much your assistant can do

On Data Safety you pick one of three levels; the choice is stored in the
OS credential vault beside the unlock key, where the assistant's own
connections cannot reach it — the dial is physically outside its world.

| Level | The assistant can… |
|---|---|
| **Read only** | query everything, change nothing |
| **Read + propose** *(default)* | file draft entries, stage imports, and propose Close Map explanations or client-brand text/colors; you approve everything |
| **Read + propose + post** | additionally post balanced journal entries — **append-only** |

Even at the highest level the engine refuses every edit and delete: an
assistant works in ink, never with an eraser. Entries it posts carry
"Posted by assistant (MCP)" and the full audit trail; corrections are
new, visible entries. Changing the level takes effect on the assistant's next
tool call and is audit-logged. Disabling access also revokes the next tool call
from an already-running MCP process.

## Security model

- **Opt-in per book.** Off until you click **Enable assistant access** on the
  Data Safety page while that book is unlocked. That stores the *derived
  database key* — never your passphrase — plus the book's encrypted identity
  in an opaque, path-scoped entry in the operating system's credential vault.
  A different path or a different book at the same path does not inherit
  access. **Disable** revokes this book's key, level, identity, and export
  folder.

  The two vaults are not equally protective, and it matters here. macOS
  Keychain binds each item to the creating application's code signature, so
  another program reading LedgerTB's entry triggers a system prompt — the
  signed build is doing real work. **Windows Credential Manager has no
  per-application control**: any program running under your Windows account
  can read the stored key without prompting anyone. On Windows, treat
  enabling assistant access (and "remember on this computer") as trusting
  every program that runs as you.
- **The selected ceiling is enforced by the database.** Every connection
  carries a SQLite authorizer. Read permits queries (and append-only export
  audit records); propose additionally permits inserts into the draft and
  staged-import inboxes; post additionally permits inserts into journal-entry
  tables. Every update and delete is denied at every assistant level.
- **Drafts, not entries.** The assistant may *propose* a journal entry
  (`propose_entry`). When correcting an existing posting it uses
  `propose_correction`, which requires the original journal-entry ID and shows
  the original and proposed lines together. It lands in **Journal Entries →
  Drafts** for review; approving posts a real, audited entry under the
  approver's name, and rejecting marks it rejected while retaining its audit
  history. An approved correction retains the original → draft →
  posted-correction chain; the assistant still cannot edit, delete, or
  formally reverse the original. The sidebar badges pending drafts.
- **Imports, normalized by the assistant.** Drop ANY statement — a weird
  CSV, a PDF, a pasted table — into the assistant and ask it to stage the
  transactions (`propose_import`). No column mapping, no format rules:
  the assistant normalizes, LedgerTB stages with full duplicate
  protection, and you categorize and post in **Import Transactions →
  Review & Categorize** exactly as with a CSV upload. Re-proposing the
  same statement stages nothing twice. A person can dismiss unwanted staged
  rows; their identity and audit history remain, but they leave the queue.
- **Close Map stays human-controlled.** `close_readiness` and
  `account_close_detail` let the assistant identify unsupported, changed, or
  unexplained balances. Account detail also exposes the immediately preceding
  fiscal year's explanation, support references, notes, and signoff history as
  reference-only context; none of it counts as current-year support or signoff.
  At propose level the assistant may call
  `propose_close_explanation`, which lands in the selected account's Close Map
  panel. The assistant cannot accept its proposal or create preparer/reviewer
  signoffs; no signoff tool exists, and the model rejects assistant-attributed
  actors as defense in depth.
- **Client branding stays human-controlled.** `client_branding_detail` shows
  the effective deliverable name, tagline, accent, whether a logo is present,
  and pending proposals; it never returns the logo bytes. At propose level,
  `propose_client_branding` may suggest a display name, tagline, or accent
  color. The suggestion waits in **Firm Settings** and **Assistant Review** and
  changes no report until a person accepts it. Logo upload and removal exist
  only in the app. Close packages then use the client identity as the primary
  brand and the firm identity as the preparer.
- **Shared/custom book files are read-only.** The separate MCP process does
  not yet participate in firm mode's one-writer sidecar lock, so books outside
  LedgerTB's local managed-data folder are capped at read access even if the
  stored dial says propose or post.
- **Local only.** The server speaks over stdio to the assistant that
  launched it. Nothing listens on a network port. Your MCP client may send tool
  results to its configured AI provider; approve that provider for client data
  before enabling access.
- Enabling and disabling are recorded in the audit trail.

## Setup

1. Open the intended book, then go to LedgerTB → **Data Safety → Enable
   assistant access**. Repeat this explicit step for each book the assistant
   should use. If a book file moves, open it at the new path and enable access
   again.
2. The page then shows the exact JSON for your machine. In Claude
   Desktop: Settings → Developer → Edit Config, add it under
   `mcpServers`; in Claude Code: `claude mcp add ledgertb -- <command>`.
   Installed-app form:

   ```json
   {
     "mcpServers": {
       "ledgertb": {
         "command": "/Applications/LedgerTB.app/Contents/MacOS/LedgerTB",
         "args": [],
         "env": { "LEDGERTB_MODE": "mcp" }
       }
     }
   }
   ```

   (On Windows, `command` is the path to `LedgerTB.exe`. Running from
   source: `command` is your Python, `args` is the path to
   `mcp_server.py`, no env needed.)
3. Restart the assistant. Ask it something real: *"Run the integrity
   sweep on client 1 for Q2 and explain anything it finds."*

## Pairing with LedgerPDF

`export_close_package` writes the close package (PDF + Excel) into the
**export folder you choose for this book on Data Safety** (stored in the
credential vault, outside the assistant's reach; blank = file export off).
Another book has no export permission until you choose its folder. The
`LEDGERTB_MCP_EXPORT_ROOTS` environment variable is a fallback for
config-managed setups where nothing was chosen in the app; it can no longer
override your choice. It used to, and that was wrong: an MCP server's
environment comes from the client's own config file, which an assistant with
filesystem tools can edit — so the boundary you set was one the assistant
could move.

One honest caveat about that boundary. It holds against the assistant's
LedgerTB tools. It does not turn an assistant that *also* has shell and file
access to your machine (Claude Code does; Claude Desktop does not) into a
sandboxed one — such an assistant can read and copy files generally, and
LedgerTB cannot prevent that from inside. Every export is audit-logged either
way. If that distinction matters for a client, use Claude Desktop for books
work.

See `LEDGERPDF-PAIRING.md` for the full books-to-binder workflow with both
MCP servers in one session.

## What to expect

Amounts are US dollars. Start with `list_clients` to get the
`client_id`. What the assistant can do depends on the level you chose. At the
default level it reads, files drafts, and stages imports — you post
everything. At the "post" level it can also post balanced entries
directly (append-only, audited). Try: *"Here's my July bank statement PDF — stage the
transactions into account 1001"* or *"Propose the accrual for the July
retainer and explain your accounts."* Everything waits for you in the
app.
