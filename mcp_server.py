"""ProBooks MCP server — read-only assistant access to the books.

Lets an MCP client (Claude Desktop, Claude Code) query a ProBooks database:
trial balance, statements, general ledger, entry search, integrity checks.

Access model, in order:
- Opt-in: the user must click "Enable assistant access" on the Data Safety
  page while unlocked. That stores the derived database KEY (never the
  passphrase) in the OS credential vault; this server reads it back. Disabling
  deletes it. No vault entry -> this server refuses to start.
- Read-only by construction: every connection is opened with
  PRAGMA query_only = ON (database.connection.READ_ONLY). A write attempt is
  a database error regardless of what any tool tries to do.
- Local: stdio transport only. Nothing listens on a network port.

Run from source:      python mcp_server.py
Run from the bundle:  PROBOOKS_MODE=mcp <ProBooks binary>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server import MCPServer

from database import connection as dbconn
from services import mcp_tools
from utils import secure_store

MCP_KEY_SECRET = "mcp_db_key"

server = MCPServer(
    "probooks",
    instructions=(
        "Access to ProBooks bookkeeping data. Start with list_clients to "
        "find the client_id; amounts are US dollars. This server cannot "
        "modify the books — it can read everything, file DRAFT entries "
        "(propose_entry), and stage bank transactions for the import flow "
        "(propose_import); a human reviews and posts everything in the app."
    ),
)


def _unlock_from_vault() -> bool:
    """Key the database from the vault entry written by 'Enable assistant
    access'. Returns False when access has not been enabled."""
    key = secure_store.get_secret(MCP_KEY_SECRET)
    if not key:
        return False
    # Firm mode: read whichever book the app most recently opened. Read-only
    # means no in-use lock is needed (or taken).
    from utils import books
    dbconn.DATABASE_PATH = books.active_book()
    # The ledger is unreachable by construction: an authorizer on every
    # connection allows reads everywhere and writes only to draft_entries.
    dbconn.DRAFT_INBOX_ONLY = True
    dbconn.set_active_key(key)
    return True


@server.tool()
def list_clients() -> list:
    """List the clients (sets of books) with their client_id."""
    return mcp_tools.list_clients()


@server.tool()
def list_accounts(client_id: int) -> list:
    """The client's chart of accounts: number, name, type, subtype, active."""
    return mcp_tools.list_accounts(client_id)


@server.tool()
def trial_balance(client_id: int, as_of: str = "") -> dict:
    """Trial balance as of a date (ISO, default today): every account's debit
    or credit balance, totals, and whether it balances."""
    return mcp_tools.trial_balance(client_id, as_of or None)


@server.tool()
def income_statement(client_id: int, start: str, end: str) -> dict:
    """Income statement for a period (ISO dates): revenues, expenses, and net
    income."""
    return mcp_tools.income_statement(client_id, start, end)


@server.tool()
def balance_sheet(client_id: int, as_of: str) -> dict:
    """Balance sheet as of a date (ISO): assets, liabilities, equity (including
    retained and current-year earnings), totals, and whether it balances."""
    return mcp_tools.balance_sheet(client_id, as_of)


@server.tool()
def general_ledger(client_id: int, account_number: str,
                   start: str = "", end: str = "") -> dict:
    """One account's ledger for a period: dated entries with running balance.
    account_number is the chart number, e.g. "1001"."""
    return mcp_tools.general_ledger(client_id, account_number,
                                    start or None, end or None)


@server.tool()
def find_entries(client_id: int, search: str = "", start: str = "",
                 end: str = "", account_number: str = "",
                 entry_type: str = "", limit: int = 50) -> list:
    """Search journal entries. search matches description/reference/amount;
    entry_type is Regular, Adjusting, Closing, or Beginning Balance; all
    filters optional and combinable."""
    return mcp_tools.find_entries(
        client_id, search or None, start or None, end or None,
        account_number or None, entry_type or None, limit,
    )


@server.tool()
def entry_detail(client_id: int, entry_id: int) -> dict:
    """A single journal entry with all its debit/credit lines and memos."""
    return mcp_tools.entry_detail(client_id, entry_id)


@server.tool()
def propose_entry(client_id: int, entry_date: str, description: str,
                  lines: list, rationale: str = "",
                  entry_type: str = "Regular") -> dict:
    """File a DRAFT journal entry for human review in ProBooks. It does NOT
    touch the ledger — a person approves or rejects it in the app. lines:
    [{"account_number": "7300", "debit": 24.00}, {"account_number": "2000",
    "credit": 24.00}] (dollars; optional "memo"). Explain WHY in rationale."""
    return mcp_tools.propose_entry(client_id, entry_date, description,
                                   lines, rationale, entry_type)


@server.tool()
def list_drafts(client_id: int, status: str = "pending") -> list:
    """Draft entries this server has filed and their review status
    ("pending", "approved", "rejected", or "all")."""
    return mcp_tools.list_drafts(client_id, status)


@server.tool()
def propose_import(client_id: int, bank_account_number: str, rows: list,
                   source_label: str = "Assistant import") -> dict:
    """Stage bank/card transactions for human review in ProBooks' import
    flow — use this after normalizing ANY statement format (CSV, PDF, OFX,
    a pasted table). rows: [{"date": "2026-07-03", "description": "...",
    "amount": -12.50}] — positive = money in, negative = money out, from the
    bank account's perspective. Duplicate-checked; nothing posts until a
    person categorizes and posts it in the app."""
    return mcp_tools.propose_import(client_id, bank_account_number, rows,
                                    source_label)


@server.tool()
def list_staged_imports(client_id: int) -> list:
    """Staged transactions still awaiting human review in the import flow."""
    return mcp_tools.list_staged_imports(client_id)


@server.tool()
def integrity_sweep(client_id: int, start: str, end: str) -> list:
    """Deterministic bookkeeping checks for a period: unbalanced or one-line
    entries, unposted imports, broken import links, future/pre-period dates,
    quiet P&L accounts, and import row-continuity gaps."""
    return mcp_tools.integrity_sweep(client_id, start, end)


def main() -> int:
    if not _unlock_from_vault():
        print(
            "ProBooks MCP: assistant access is not enabled. Open ProBooks -> "
            "Data Safety -> Enable assistant access, then restart this server.",
            file=sys.stderr,
        )
        return 1
    server.run()  # stdio transport
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
