"""LedgerTB MCP server — locally authorized assistant access to the books.

Lets an MCP client (Claude Desktop, Claude Code) query a LedgerTB database:
trial balance, statements, general ledger, entry search, integrity checks.

Access model, in order:
- Opt-in: the user must click "Enable assistant access" on the Data Safety
  page for each book while it is unlocked. That stores the derived database
  KEY (never the passphrase) and encrypted book identity in the OS credential
  vault; this server reads them back. Disabling deletes them. No matching
  vault entry -> this server refuses access.
- Permissioned by construction: every tool re-reads the enablement and access
  level from the credential vault, and SQLite's authorizer enforces that level
  for each new connection.
- Local: stdio transport only. Nothing listens on a network port.

Run from source:      python mcp_server.py
Run from the bundle:  LEDGERTB_MODE=mcp <LedgerTB binary>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server import MCPServer

from database import connection as dbconn
from services import mcp_tools
from services.backups import active_book_id
from utils.assistant_access import credential_names
from utils import secure_store


def _access_level(names) -> str:
    """The level chosen on Data Safety; stored in the OS vault beside the key
    so the assistant's own connections can never change it. Missing or unknown
    values fail to the least-privileged read level."""
    from database.connection import ASSISTANT_ACCESS_LEVELS

    if not secure_store.get_secret(names.key):
        raise PermissionError(
            "LedgerTB assistant access is disabled. Enable it in LedgerTB -> "
            "Data Safety -> Assistant access."
        )
    level = secure_store.get_secret(names.level)
    if level is None:
        return "read"
    return level if level in ASSISTANT_ACCESS_LEVELS else "read"


def _refresh_access() -> str:
    """Re-read enablement and permission level before every tool invocation.

    The MCP process is commonly long-lived. Reading the vault only at startup
    made Data Safety's Disable and Change level controls ineffective until the
    external assistant restarted its server process.
    """
    from utils import books
    active_book = books.active_book()
    names = credential_names(active_book)
    key = secure_store.get_secret(names.key)
    expected_book_id = secure_store.get_secret(names.book_id)
    if not key or not expected_book_id:
        # Key first, level second: the level may only read None while no key
        # is set, because None installs no authorizer at all on a connection
        # a concurrent tool call opens in between.
        dbconn.clear_active_key()
        dbconn.ASSISTANT_ACCESS_LEVEL = None
        raise PermissionError(
            "LedgerTB assistant access is not enabled for this book. Enable it in "
            "LedgerTB -> Data Safety -> Assistant access."
        )

    level = _access_level(names)
    # External/custom paths may be SMB/NFS books. The desktop app coordinates
    # one writer with a sidecar lock, but this separate MCP process does not yet
    # join that protocol. Keep those books read-only to avoid a second writer.
    if level != "read" and not books.is_local_book(active_book):
        level = "read"
    # Park at the least-privileged real level while the key is live. None is
    # NOT a deny value here — it means "not an assistant process" and installs
    # no authorizer, so a concurrent tool call opening a connection during the
    # identity check below would get an unrestricted one. MCP dispatches tool
    # calls to parallel worker threads against this process-global, so that
    # window is reachable in practice.
    dbconn.ASSISTANT_ACCESS_LEVEL = "read"
    dbconn.DATABASE_PATH = active_book
    dbconn.set_active_key(key)
    try:
        actual_book_id = active_book_id()
    except Exception as exc:
        dbconn.clear_active_key()
        dbconn.ASSISTANT_ACCESS_LEVEL = None
        raise PermissionError(
            "The authorized LedgerTB book could not be opened. Re-enable "
            "assistant access from that book's Data Safety page."
        ) from exc
    if actual_book_id != expected_book_id:
        dbconn.clear_active_key()
        dbconn.ASSISTANT_ACCESS_LEVEL = None
        raise PermissionError(
            "The book at this path is not the book that authorized assistant "
            "access. Open it in LedgerTB and grant access explicitly."
        )
    dbconn.ASSISTANT_ACCESS_LEVEL = level
    return level


def _require_level(minimum: str):
    order = ("read", "propose", "post")
    current = _refresh_access()
    if order.index(current) < order.index(minimum):
        raise ValueError(
            f"This tool needs assistant access level '{minimum}'; the current "
            f"level is '{current}'. Change it in LedgerTB -> Data Safety -> "
            "Assistant access."
        )

server = MCPServer(
    "ledgertb",
    instructions=(
        "Access to LedgerTB bookkeeping data. Start with list_clients to "
        "find the client_id; amounts are US dollars. What you may do is set "
        "by the user's chosen access level (Data Safety): read only; "
        "propose (file draft entries, stage imports, and suggest client "
        "branding text/colors for human review); "
        "or post (additionally post balanced entries, APPEND-ONLY — nothing "
        "can ever be edited or deleted from here). Tools tell you if the "
        "level is insufficient."
    ),
)


def _unlock_from_vault() -> bool:
    """Key the database from the vault entry written by 'Enable assistant
    access'. Returns False when access has not been enabled."""
    from utils import actor

    # Every audit row, created_by, and activity line this process writes says
    # "<user> (AI)" — assistant work is never presented as the person's own.
    actor.mark_as_assistant()
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
def client_branding_detail(client_id: int) -> dict:
    """The client identity used on deliverables and any pending text/color
    proposals. Reports whether a logo exists without exposing its contents."""
    _require_level("read")
    return mcp_tools.client_branding_detail(client_id)


@server.tool()
def propose_client_branding(client_id: int, display_name: str = "",
                            tagline: str = "", accent_hex: str = "",
                            rationale: str = "") -> dict:
    """Suggest client display-name, tagline, or six-digit hex accent changes
    for human approval. Blank fields are left unchanged; logo upload stays in
    LedgerTB and is always human-controlled. Needs access level "propose"."""
    _require_level("propose")
    return mcp_tools.propose_client_branding(
        client_id,
        display_name.strip() or None,
        tagline.strip() or None,
        accent_hex.strip() or None,
        rationale,
    )


@server.tool()
def trial_balance(client_id: int, as_of: str = "",
                  compare_to_prior_year: bool = False) -> dict:
    """Trial balance as of a date (ISO, default today): every account's debit
    or credit balance, totals, and whether it balances."""
    _require_level("read")
    return mcp_tools.trial_balance(
        client_id, as_of or None, compare_to_prior_year
    )


@server.tool()
def income_statement(client_id: int, start: str, end: str,
                     compare_to_prior_year: bool = False) -> dict:
    """Income statement for a period (ISO dates): revenues, expenses, and net
    income. Set compare_to_prior_year for line-by-line PY amounts and changes."""
    _require_level("read")
    return mcp_tools.income_statement(
        client_id, start, end, compare_to_prior_year
    )


@server.tool()
def balance_sheet(client_id: int, as_of: str,
                  compare_to_prior_year: bool = False) -> dict:
    """Balance sheet as of a date (ISO): assets, liabilities, equity (including
    retained and current-year earnings), totals, and whether it balances.
    Set compare_to_prior_year for line-by-line PY amounts and changes."""
    _require_level("read")
    return mcp_tools.balance_sheet(client_id, as_of, compare_to_prior_year)


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
def close_readiness(client_id: int, fiscal_year: int) -> dict:
    """Close Map status for a fiscal year: account balances, PY changes,
    evidence counts, exceptions, and human preparer/reviewer signoffs."""
    _require_level("read")
    return mcp_tools.close_readiness(client_id, fiscal_year)


@server.tool()
def account_close_detail(client_id: int, fiscal_year: int,
                         account_id: int) -> dict:
    """Detailed Close Map record for one account, including its explanation,
    evidence references, notes, AJE effect, signoff status, and reference-only
    context from the immediately preceding fiscal year."""
    _require_level("read")
    return mcp_tools.account_close_detail(client_id, fiscal_year, account_id)


@server.tool()
def propose_close_explanation(client_id: int, fiscal_year: int, account_id: int,
                              explanation: str, rationale: str = "") -> dict:
    """Propose a balance/variance explanation for human review in Close Map.
    This cannot accept the explanation or sign off the account. Needs assistant
    access level "propose" or higher."""
    _require_level("propose")
    return mcp_tools.propose_close_explanation(
        client_id, fiscal_year, account_id, explanation, rationale
    )


@server.tool()
def propose_entry(client_id: int, entry_date: str, description: str,
                  lines: list, rationale: str = "",
                  entry_type: str = "Regular",
                  original_entry_id: int | None = None) -> dict:
    """File a DRAFT journal entry for human review in LedgerTB. It does NOT
    touch the ledger — a person approves or rejects it in the app. lines:
    [{"account_number": "7300", "debit": 24.00}, {"account_number": "2000",
    "credit": 24.00}] (dollars; optional "memo"). Explain WHY in rationale.
    entry_type: "Regular" (default), "Adjusting", "Beginning Balance" (use
    for opening-balance entries), or "Closing". When correcting an existing
    posting, original_entry_id is required so LedgerTB presents the original
    and proposal together and retains the review chain."""
    _require_level("propose")
    return mcp_tools.propose_entry(client_id, entry_date, description,
                                   lines, rationale, entry_type,
                                   original_entry_id)


@server.tool()
def propose_correction(client_id: int, original_entry_id: int,
                       entry_date: str, description: str, lines: list,
                       rationale: str = "",
                       entry_type: str = "Regular") -> dict:
    """File a correction DRAFT linked to an existing journal entry. The
    original and proposed lines are shown together for a person's review, and
    approval retains the original -> draft -> posted-correction chain. This
    never edits, deletes, or reverses the original itself. Use propose_entry
    for a new entry that does not correct an existing posting."""
    _require_level("propose")
    return mcp_tools.propose_correction(
        client_id, original_entry_id, entry_date, description, lines,
        rationale, entry_type,
    )


@server.tool()
def list_drafts(client_id: int, status: str = "pending") -> list:
    """Draft entries this server has filed and their review status
    ("pending", "approved", "rejected", or "all")."""
    _require_level("read")
    return mcp_tools.list_drafts(client_id, status)


@server.tool()
def propose_import(client_id: int, bank_account_number: str, rows: list,
                   source_label: str = "Assistant import") -> dict:
    """Stage bank/card transactions for human review in LedgerTB's import
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
    to post. lines and entry_type like propose_entry (dollars; entry_type
    "Beginning Balance" for opening balances)."""
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
    listed in LEDGERTB_MCP_EXPORT_ROOTS; anywhere else is refused. The export
    is audit-logged."""
    _require_level("read")
    return mcp_tools.export_close_package(client_id, period_start, period_end,
                                          out_dir)


@server.tool()
def create_client(name: str, entity_type: str = "",
                  fiscal_year_end_month: int = 12,
                  seed_default_chart: bool = True) -> dict:
    """Create a new client (a new set of books), optionally seeded with the
    default chart of accounts. Setup only — the assistant can never modify or
    delete a client afterwards. Needs access level "propose" or higher."""
    _require_level("propose")
    return mcp_tools.create_client(name, entity_type, fiscal_year_end_month,
                                   seed_default_chart)


@server.tool()
def import_accounts(client_id: int, rows: list) -> dict:
    """Add accounts to a client's chart of accounts. rows:
    [{"number": "1000", "name": "Operating Checking", "type": "Bank"}] —
    canonical or QuickBooks type names both work (QB names imply subtypes,
    e.g. Bank -> Asset/Cash). Existing numbers are skipped and reported;
    nothing is silently dropped. Needs access level "propose" or higher."""
    _require_level("propose")
    return mcp_tools.import_accounts(client_id, rows)


@server.tool()
def integrity_sweep(client_id: int, start: str, end: str) -> dict:
    """Deterministic bookkeeping checks for a period: unbalanced or one-line
    entries, unposted imports, broken import links, future dates,
    quiet P&L accounts, and import row-continuity gaps. Returns an explicit
    clean status and the checks run even when there are no findings."""
    _require_level("read")
    return mcp_tools.integrity_sweep(client_id, start, end)


def main() -> int:
    if not _unlock_from_vault():
        print(
            "LedgerTB MCP: assistant access is not enabled. Open LedgerTB -> "
            "Data Safety -> Enable assistant access, then restart this server.",
            file=sys.stderr,
        )
        return 1
    server.run()  # stdio transport
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
