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
from utils.secure_store import get_secret

MCP_KEY_SECRET = "mcp_db_key"

server = MCPServer(
    "probooks",
    instructions=(
        "Read-only access to ProBooks bookkeeping data. Start with "
        "list_clients to find the client_id; amounts are US dollars. "
        "This server cannot modify the books."
    ),
)


def _unlock_from_vault() -> bool:
    """Key the database from the vault entry written by 'Enable assistant
    access'. Returns False when access has not been enabled."""
    key = get_secret(MCP_KEY_SECRET)
    if not key:
        return False
    # Firm mode: read whichever book the app most recently opened. Read-only
    # means no in-use lock is needed (or taken).
    from utils import books
    dbconn.DATABASE_PATH = books.active_book()
    dbconn.READ_ONLY = True
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
