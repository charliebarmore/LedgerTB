"""ProBooks MCP server — locally authorized assistant access to the books.

Lets an MCP client (Claude Desktop, Claude Code) query a ProBooks database:
trial balance, statements, general ledger, entry search, integrity checks.

Access model, in order:
- Opt-in: the user must click "Enable assistant access" on the Data Safety
  page while unlocked. That stores the derived database KEY (never the
  passphrase) in the OS credential vault; this server reads it back. Disabling
  deletes it. No vault entry -> this server refuses to start.
- Permissioned by construction: every tool re-reads the enablement and access
  level from the credential vault, and SQLite's authorizer enforces that level
  for each new connection.
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
MCP_LEVEL_SECRET = "mcp_access_level"


def _access_level() -> str:
    """The level chosen on Data Safety; stored in the OS vault beside the key
    so the assistant's own connections can never change it. Setups enabled
    before levels existed behave as "propose" (their original behavior)."""
    from database.connection import ASSISTANT_ACCESS_LEVELS

    if not secure_store.get_secret(MCP_KEY_SECRET):
        raise PermissionError(
            "ProBooks assistant access is disabled. Enable it in ProBooks -> "
            "Data Safety -> Assistant access."
        )
    level = secure_store.get_secret(MCP_LEVEL_SECRET)
    if level is None:
        return "propose"  # compatibility for installations enabled pre-levels
    return level if level in ASSISTANT_ACCESS_LEVELS else "read"


def _refresh_access() -> str:
    """Re-read enablement and permission level before every tool invocation.

    The MCP process is commonly long-lived. Reading the vault only at startup
    made Data Safety's Disable and Change level controls ineffective until the
    external assistant restarted its server process.
    """
    key = secure_store.get_secret(MCP_KEY_SECRET)
    if not key:
        dbconn.ASSISTANT_ACCESS_LEVEL = None
        dbconn.clear_active_key()
        raise PermissionError(
            "ProBooks assistant access was disabled. Re-enable it in "
            "ProBooks -> Data Safety -> Assistant access."
        )

    level = _access_level()
    from utils import books
    active_book = books.active_book()
    # External/custom paths may be SMB/NFS books. The desktop app coordinates
    # one writer with a sidecar lock, but this separate MCP process does not yet
    # join that protocol. Keep those books read-only to avoid a second writer.
    if level != "read" and not books.is_local_book(active_book):
        level = "read"
    dbconn.DATABASE_PATH = active_book
    dbconn.ASSISTANT_ACCESS_LEVEL = level
    dbconn.set_active_key(key)
    return level


def _require_level(minimum: str):
    order = ("read", "propose", "post")
    current = _refresh_access()
    if order.index(current) < order.index(minimum):
        raise ValueError(
            f"This tool needs assistant access level '{minimum}'; the current "
            f"level is '{current}'. Change it in ProBooks -> Data Safety -> "
            "Assistant access."
        )

server = MCPServer(
    "probooks",
    instructions=(
        "Access to ProBooks bookkeeping data. Start with list_clients to "
        "find the client_id; amounts are US dollars. What you may do is set "
        "by the user's chosen access level (Data Safety): read only; "
        "propose (file draft entries and stage imports for human review); "
        "or post (additionally post balanced entries, APPEND-ONLY — nothing "
        "can ever be edited or deleted from here). Tools tell you if the "
        "level is insufficient."
    ),
)


def _unlock_from_vault() -> bool:
    """Key the database from the vault entry written by 'Enable assistant
    access'. Returns False when access has not been enabled."""
    try:
        _refresh_access()
    except PermissionError:
        return False
    return True


@server.tool()
def list_clients() -> list:
    """List the clients (sets of books) with their client_id."""
    _require_level("read")
    return mcp_tools.list_clients()


@server.tool()
def list_accounts(client_id: int) -> list:
    """The client's chart of accounts: number, name, type, subtype, active."""
    _require_level("read")
    return mcp_tools.list_accounts(client_id)


@server.tool()
def trial_balance(client_id: int, as_of: str = "") -> dict:
    """Trial balance as of a date (ISO, default today): every account's debit
    or credit balance, totals, and whether it balances."""
    _require_level("read")
    return mcp_tools.trial_balance(client_id, as_of or None)


@server.tool()
def income_statement(client_id: int, start: str, end: str) -> dict:
    """Income statement for a period (ISO dates): revenues, expenses, and net
    income."""
    _require_level("read")
    return mcp_tools.income_statement(client_id, start, end)


@server.tool()
def balance_sheet(client_id: int, as_of: str) -> dict:
    """Balance sheet as of a date (ISO): assets, liabilities, equity (including
    retained and current-year earnings), totals, and whether it balances."""
    _require_level("read")
    return mcp_tools.balance_sheet(client_id, as_of)


@server.tool()
def general_ledger(client_id: int, account_number: str,
                   start: str = "", end: str = "") -> dict:
    """One account's ledger for a period: dated entries with running balance.
    account_number is the chart number, e.g. "1001"."""
    _require_level("read")
    return mcp_tools.general_ledger(client_id, account_number,
                                    start or None, end or None)


@server.tool()
def find_entries(client_id: int, search: str = "", start: str = "",
                 end: str = "", account_number: str = "",
                 entry_type: str = "", limit: int = 50) -> list:
    """Search journal entries. search matches description/reference/amount;
    entry_type is Regular, Adjusting, Closing, or Beginning Balance; all
    filters optional and combinable."""
    _require_level("read")
    return mcp_tools.find_entries(
        client_id, search or None, start or None, end or None,
        account_number or None, entry_type or None, limit,
    )


@server.tool()
def entry_detail(client_id: int, entry_id: int) -> dict:
    """A single journal entry with all its debit/credit lines and memos."""
    _require_level("read")
    return mcp_tools.entry_detail(client_id, entry_id)


@server.tool()
def propose_entry(client_id: int, entry_date: str, description: str,
                  lines: list, rationale: str = "",
                  entry_type: str = "Regular") -> dict:
    """File a DRAFT journal entry for human review in ProBooks. It does NOT
    touch the ledger — a person approves or rejects it in the app. lines:
    [{"account_number": "7300", "debit": 24.00}, {"account_number": "2000",
    "credit": 24.00}] (dollars; optional "memo"). Explain WHY in rationale."""
    _require_level("propose")
    return mcp_tools.propose_entry(client_id, entry_date, description,
                                   lines, rationale, entry_type)


@server.tool()
def list_drafts(client_id: int, status: str = "pending") -> list:
    """Draft entries this server has filed and their review status
    ("pending", "approved", "rejected", or "all")."""
    _require_level("read")
    return mcp_tools.list_drafts(client_id, status)


@server.tool()
def propose_import(client_id: int, bank_account_number: str, rows: list,
                   source_label: str = "Assistant import") -> dict:
    """Stage bank/card transactions for human review in ProBooks' import
    flow — use this after normalizing ANY statement format (CSV, PDF, OFX,
    a pasted table). rows: [{"date": "2026-07-03", "description": "...",
    "amount": -12.50}] — positive = money in, negative = money out, from the
    bank account's perspective. Duplicate-checked; nothing posts until a
    person categorizes and posts it in the app. Needs access level "propose"
    or higher."""
    _require_level("propose")
    return mcp_tools.propose_import(client_id, bank_account_number, rows,
                                    source_label)


@server.tool()
def list_staged_imports(client_id: int) -> list:
    """Staged transactions still awaiting human review in the import flow."""
    _require_level("read")
    return mcp_tools.list_staged_imports(client_id)


@server.tool()
def post_entry(client_id: int, entry_date: str, description: str,
               lines: list, entry_type: str = "Regular") -> dict:
    """POST a balanced journal entry directly to the ledger. Only works at
    assistant access level "post" (chosen by the user on Data Safety) and is
    APPEND-ONLY: entries can be added, never edited or deleted — corrections
    are new visible entries. Prefer propose_entry unless the user asked you
    to post. lines like propose_entry (dollars)."""
    _require_level("post")
    return mcp_tools.post_entry(client_id, entry_date, description,
                                lines, entry_type)


@server.tool()
def export_close_package(client_id: int, period_start: str, period_end: str,
                         out_dir: str) -> dict:
    """Write the period's close package — a branded PDF and an Excel workbook
    (Summary, Trial Balance, Transactions, Adjusting Entries, Receipts &
    Disbursements) — into out_dir, so a workpaper tool such as LedgerPDF can
    ingest it. Works at every access level, but ONLY into folders the user
    listed in PROBOOKS_MCP_EXPORT_ROOTS; anywhere else is refused. The export
    is audit-logged."""
    _require_level("read")
    return mcp_tools.export_close_package(client_id, period_start, period_end,
                                          out_dir)


@server.tool()
def integrity_sweep(client_id: int, start: str, end: str) -> list:
    """Deterministic bookkeeping checks for a period: unbalanced or one-line
    entries, unposted imports, broken import links, future/pre-period dates,
    quiet P&L accounts, and import row-continuity gaps."""
    _require_level("read")
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
