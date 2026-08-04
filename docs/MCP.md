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
- **Read-only by construction.** Every database connection the server
  opens is pinned with `PRAGMA query_only = ON`. A write attempt fails at
  the database, regardless of what any tool or prompt tries.
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
`client_id`. The assistant can read and reason about the books; it
cannot post, edit, or delete anything — if you want it to draft a
journal entry, it can only describe one for you to enter yourself.
