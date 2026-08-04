"""Read-only views of the books for the MCP server.

Every function returns plain JSON-able dicts/lists and takes primitives
(ints, ISO date strings), so the MCP layer stays a thin wrapper and this
module is directly unit-testable. Nothing here writes: the server also pins
every connection read-only (database.connection.READ_ONLY), so a write
attempt is a database error, not a policy.

Amounts are dollars (floats) — presentation values for an assistant, not
ledger arithmetic (the ledger itself stores integer cents).
"""
from datetime import date
from typing import Optional

from models.account import Account
from models.client import Client
from models.journal_entry import JournalEntry
from models.reports import ReportGenerator
from services.book_review import run_integrity_sweep


def _parse_date(value: Optional[str], name: str) -> Optional[date]:
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name} must be an ISO date (YYYY-MM-DD), got {value!r}")


def _require_client(client_id: int) -> Client:
    client = Client.get_by_id(client_id)
    if client is None:
        raise ValueError(f"No client with id {client_id}. Use list_clients first.")
    return client


def _resolve_account(client_id: int, account_number: str) -> Account:
    matches = [a for a in Account.get_all(client_id, active_only=False)
               if a.account_number == str(account_number)]
    if not matches:
        raise ValueError(f"No account numbered {account_number} for this client.")
    return matches[0]


def list_clients() -> list:
    return [{"client_id": c.id, "name": c.name} for c in Client.get_all()]


def list_accounts(client_id: int) -> list:
    _require_client(client_id)
    return [
        {
            "number": a.account_number,
            "name": a.name,
            "type": a.type,
            "subtype": a.subtype or "",
            "active": bool(a.is_active),
        }
        for a in Account.get_all(client_id, active_only=False)
    ]


def trial_balance(client_id: int, as_of: Optional[str] = None) -> dict:
    _require_client(client_id)
    rows = ReportGenerator.trial_balance(client_id, _parse_date(as_of, "as_of"))
    out = [
        {
            "number": r.account_number,
            "name": r.account_name,
            "type": r.account_type,
            "debit": round(r.debit, 2),
            "credit": round(r.credit, 2),
        }
        for r in rows
    ]
    total_debits = round(sum(r["debit"] for r in out), 2)
    total_credits = round(sum(r["credit"] for r in out), 2)
    return {
        "as_of": as_of or date.today().isoformat(),
        "accounts": out,
        "total_debits": total_debits,
        "total_credits": total_credits,
        "balanced": total_debits == total_credits,
    }


def income_statement(client_id: int, start: str, end: str) -> dict:
    _require_client(client_id)
    report = ReportGenerator.income_statement(
        client_id, _parse_date(start, "start"), _parse_date(end, "end")
    )
    def _lines(items):
        return [
            {"number": i["account_number"], "name": i["name"],
             "amount": round(i["balance"], 2)}
            for i in items
        ]
    return {
        "start": start,
        "end": end,
        "revenues": _lines(report["revenues"]),
        "expenses": _lines(report["expenses"]),
        "total_revenue": round(report["total_revenue"], 2),
        "total_expenses": round(report["total_expenses"], 2),
        "net_income": round(report["net_income"], 2),
    }


def balance_sheet(client_id: int, as_of: str) -> dict:
    _require_client(client_id)
    report = ReportGenerator.balance_sheet(client_id, _parse_date(as_of, "as_of"))
    def _lines(items):
        return [
            {"number": i["account_number"], "name": i["name"],
             "amount": round(i["balance"], 2)}
            for i in items
        ]
    return {
        "as_of": as_of,
        "assets": _lines(report["assets"]),
        "liabilities": _lines(report["liabilities"]),
        "equity": _lines(report["equity"]),  # includes RE + current-year earnings
        "total_assets": round(report["total_assets"], 2),
        "total_liabilities": round(report["total_liabilities"], 2),
        "total_equity": round(report["total_equity"], 2),
        "total_liabilities_equity": round(report["total_liabilities_equity"], 2),
        "balanced": round(report["total_assets"], 2)
        == round(report["total_liabilities_equity"], 2),
    }


def general_ledger(client_id: int, account_number: str,
                   start: Optional[str] = None, end: Optional[str] = None) -> dict:
    _require_client(client_id)
    account = _resolve_account(client_id, account_number)
    entries = ReportGenerator.general_ledger(
        account.id, _parse_date(start, "start"), _parse_date(end, "end"),
        client_id=client_id,
    )
    return {
        "account": f"{account.account_number} - {account.name}",
        "entries": [
            {
                "date": e.entry_date.isoformat(),
                "entry_id": e.entry_id or None,
                "description": e.description or "",
                "reference": e.source_reference or "",
                "debit": round(e.debit, 2),
                "credit": round(e.credit, 2),
                "balance": round(e.balance, 2),
            }
            for e in entries
        ],
        "ending_balance": round(entries[-1].balance, 2) if entries else 0.0,
    }


def find_entries(client_id: int, search: Optional[str] = None,
                 start: Optional[str] = None, end: Optional[str] = None,
                 account_number: Optional[str] = None,
                 entry_type: Optional[str] = None, limit: int = 50) -> list:
    """Search journal entries by text/amount, date range, account, or type."""
    _require_client(client_id)
    account_id = (_resolve_account(client_id, account_number).id
                  if account_number else None)
    entries = JournalEntry.get_all(
        client_id,
        start_date=_parse_date(start, "start"),
        end_date=_parse_date(end, "end"),
        entry_type=entry_type,
        search_term=search or None,
        account_id=account_id,
        limit=min(int(limit), 200),
    )
    return [
        {
            "entry_id": e.id,
            "date": e.entry_date.isoformat() if hasattr(e.entry_date, "isoformat") else str(e.entry_date),
            "type": e.entry_type,
            "description": e.description or "",
            "reference": e.source_reference or "",
            "lines": [
                {
                    "account": f"{l.account_number} - {l.account_name}",
                    "debit": round(l.debit, 2),
                    "credit": round(l.credit, 2),
                }
                for l in e.lines
            ],
        }
        for e in entries
    ]


def entry_detail(client_id: int, entry_id: int) -> dict:
    _require_client(client_id)
    entry = JournalEntry.get_by_id(entry_id, client_id=client_id)
    if entry is None:
        raise ValueError(f"No journal entry #{entry_id} for this client.")
    return {
        "entry_id": entry.id,
        "date": entry.entry_date.isoformat() if hasattr(entry.entry_date, "isoformat") else str(entry.entry_date),
        "type": entry.entry_type,
        "description": entry.description or "",
        "reference": entry.source_reference or "",
        "aje_reference": getattr(entry, "aje_reference", None),
        "lines": [
            {
                "account": f"{l.account_number} - {l.account_name}",
                "debit": round(l.debit, 2),
                "credit": round(l.credit, 2),
                "memo": getattr(l, "memo", "") or "",
            }
            for l in entry.lines
        ],
    }


def integrity_sweep(client_id: int, start: str, end: str) -> list:
    """Deterministic bookkeeping checks: unbalanced or short entries, unposted
    imports, broken import links, future/pre-period dates, quiet P&L accounts,
    import row gaps."""
    _require_client(client_id)
    findings = run_integrity_sweep(
        client_id, _parse_date(start, "start"), _parse_date(end, "end")
    )
    return [
        {
            "severity": f.severity,
            "check": f.skill,
            "title": f.title,
            "detail": f.detail,
            "entry_id": f.entry_id,
        }
        for f in findings
    ]
