"""JSON-facing bookkeeping operations for the local MCP server.

Every function returns plain JSON-able dicts/lists and takes primitives
(ints, ISO date strings), so the MCP layer stays a thin wrapper and this
module is directly unit-testable. The server revalidates its user-selected
read/propose/post level for every call, and SQLite's authorizer enforces the
corresponding append-only write surface. Updates and deletes are always denied.

Amounts are dollars (floats) — presentation values for an assistant, not
ledger arithmetic (the ledger itself stores integer cents).
"""
import functools as _functools
import os
from datetime import date
from pathlib import Path
from typing import Optional

from constants import AccountSubtype, EntryType
from models.account import Account
from models.client import Client
from models.journal_entry import JournalEntry
from models.reports import ReportGenerator
from money import to_dollars
from services.book_review import run_integrity_sweep



def mutating(fn):
    """Declare a tool implementation as a write.

    The guard lives here rather than only on the MCP server's wrappers so that
    it holds for every caller of this module, not just the one entry point. It
    is reentrant, so the server's own declaration nesting over this one is
    harmless.

    Declaring is the contract; database/connection.py is the enforcement, which
    opens an assistant connection read-only outside a declared write. A mutating
    tool that loses this decorator fails loudly instead of silently escaping the
    maintenance lock.
    """
    @_functools.wraps(fn)
    def wrapper(*args, **kwargs):
        from database import connection as _dbconn
        from utils import maintenance_lock

        with maintenance_lock.writer(_dbconn.DATABASE_PATH):
            return fn(*args, **kwargs)

    wrapper._ledgertb_mutating = True
    return wrapper


def _write_private(path: Path, payload: bytes) -> None:
    """Write an export without following a symlink and without exposing it.

    Containment checks the DIRECTORY, but the filename is predictable from the
    client name and period, so on a shared engagement folder a colleague could
    pre-place a symlink under that exact name and catch the export. O_NOFOLLOW
    refuses that; O_EXCL means an existing file is replaced deliberately rather
    than written through. 0600 because this is the whole close package —
    trial balance, every journal line — in the clear.
    """
    # O_BINARY matters on Windows: without it these handles translate newlines
    # and every PDF and XLSX comes out corrupt. O_NOFOLLOW does not exist there
    # and resolves to 0; the explicit is_symlink() check below covers both.
    flags = (os.O_WRONLY | os.O_CREAT | os.O_TRUNC
             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    if path.is_symlink():
        raise ValueError(
            f"{path.name} already exists as a symbolic link; refusing to write "
            "the export through it. Remove it and try again."
        )
    # fdopen rather than a bare os.write: os.write is not obliged to consume
    # the whole buffer in one call, and a short write truncates the file.
    with os.fdopen(os.open(path, flags, 0o600), "wb") as handle:
        handle.write(payload)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows has no POSIX mode; the user profile is the boundary


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
    requested = str(account_number).strip()
    matches = [a for a in Account.get_all(client_id, active_only=False)
               if a.account_number == requested]
    if not matches:
        raise ValueError(f"No account numbered {account_number} for this client.")
    return matches[0]


def list_clients() -> list:
    return [{"client_id": c.id, "name": c.name} for c in Client.get_all()]


def list_accounts(client_id: int, account_number: Optional[str] = None) -> list:
    _require_client(client_id)
    accounts = Account.get_all(client_id, active_only=False)
    if account_number:
        requested = str(account_number).strip()
        accounts = [a for a in accounts if a.account_number == requested]
    return [
        {
            "account_id": a.id,
            "number": a.account_number,
            "name": a.name,
            "type": a.type,
            "subtype": a.subtype or "",
            "active": bool(a.is_active),
        }
        for a in accounts
    ]


def client_branding_detail(client_id: int) -> dict:
    """Client deliverable identity and pending human-review proposals."""
    from services import branding

    client = _require_client(client_id)
    current = branding.get_client_branding(client_id)
    return {
        "client_id": client_id,
        "legal_name": client.name,
        "display_name": current.display_name or client.dba_name or client.name,
        "tagline": current.tagline,
        "accent_hex": current.accent_hex or None,
        "logo_present": bool(current.logo),
        "pending_proposals": [
            {"proposal_id": item["id"], "display_name": item["display_name"],
             "tagline": item["tagline"], "accent_hex": item["accent_hex"],
             "rationale": item["rationale"], "created_by": item["created_by"]}
            for item in branding.pending_client_branding_proposals(client_id)
        ],
    }


@mutating
def propose_client_branding(
    client_id: int,
    display_name: Optional[str] = None,
    tagline: Optional[str] = None,
    accent_hex: Optional[str] = None,
    rationale: str = "",
) -> dict:
    """Propose client identity text/color; a human approves and uploads logos."""
    from services import branding

    _require_client(client_id)
    proposal_id = branding.propose_client_branding(
        client_id, display_name, tagline, accent_hex, rationale
    )
    return {
        "proposal_id": proposal_id,
        "status": "pending",
        "note": (
            "Filed for human approval in Firm Settings. This does not change "
            "deliverables yet. Logo upload remains human-controlled."
        ),
    }


def _validated_fiscal_year(value) -> int:
    try:
        year = int(value)
    except (TypeError, ValueError):
        raise ValueError("fiscal_year must be a four-digit year.")
    if not 1900 <= year <= 9999:
        raise ValueError("fiscal_year must be between 1900 and 9999.")
    return year


def _resolve_year_period(client_id: int, fiscal_year: int):
    from models.fiscal_period import FiscalPeriod

    _require_client(client_id)
    label = f"FY {_validated_fiscal_year(fiscal_year)}"
    period = next(
        (item for item in FiscalPeriod.get_all(client_id, period_type="Year")
         if item.period_name == label),
        None,
    )
    if period is None:
        raise ValueError(
            f"No {label} for this client. Use ensure_fiscal_year, or add it in "
            "LedgerTB -> Trial Balance Worksheet -> + Add Year."
        )
    return period


@mutating
def ensure_fiscal_year(client_id: int, fiscal_year: int) -> dict:
    """Create one client's Year, Quarter, and Month periods if absent."""
    from models.fiscal_period import FiscalPeriod

    client = _require_client(client_id)
    year = _validated_fiscal_year(fiscal_year)

    label = f"FY {year}"
    before = {
        period.period_name for period in FiscalPeriod.get_all(client_id)
        if period.period_name == label
        or period.period_name.startswith(f"{label} - ")
    }
    periods = FiscalPeriod.ensure_periods_exist(
        client_id, year, client.fiscal_year_end_month
    )
    period = next(item for item in periods if item.period_name == label)
    periods_added = sum(item.period_name not in before for item in periods)
    return {
        "fiscal_year": year,
        "created": label not in before,
        "repaired": label in before and periods_added > 0,
        "periods_added": periods_added,
        "period": {
            "start": period.start_date.isoformat(),
            "end": period.end_date.isoformat(),
        },
        "note": (
            "Fiscal year created and available to Close Map."
            if label not in before else
            "Fiscal year calendar repaired."
            if periods_added else
            "Fiscal year already existed; nothing changed."
        ),
    }


def _close_row(row) -> dict:
    return {
        "account_id": row.account_id,
        "account_number": row.account_number,
        "account_name": row.account_name,
        "account_type": row.account_type,
        "adjusted_balance": row.current_balance,
        "prior_year_balance": row.prior_balance,
        "change": row.change,
        "change_percent": row.change_percent,
        "lead_sheet": ({"code": row.group_code, "name": row.group_name}
                       if row.group_code else None),
        "required": row.required,
        "exclusion_reason": row.exclusion_reason,
        "evidence_count": row.evidence_count,
        "open_note_count": row.open_note_count,
        "pending_proposal_count": row.pending_proposal_count,
        "status": row.status,
        "prepared_by": row.prepared_by or None,
        "prepared_at": row.prepared_at or None,
        "reviewed_by": row.reviewed_by or None,
        "reviewed_at": row.reviewed_at or None,
    }


def close_readiness(client_id: int, fiscal_year: int) -> dict:
    """Account-by-account support and signoff status for a fiscal year."""
    from models import close_map

    period = _resolve_year_period(client_id, fiscal_year)
    result = close_map.readiness(client_id, period.id)
    return {
        "fiscal_year": int(fiscal_year),
        "period": {"start": result["period_start"], "end": result["period_end"]},
        "ready": result["ready"],
        "required_count": result["required_count"],
        "reviewed_count": result["reviewed_count"],
        "incomplete_count": result["incomplete_count"],
        "status_counts": result["counts"],
        "accounts": [_close_row(row) for row in result["rows"]],
    }


def account_close_detail(client_id: int, fiscal_year: int, account_id: int) -> dict:
    """One Close Map account with current work and prior-year context."""
    from models import close_map

    period = _resolve_year_period(client_id, fiscal_year)
    detail = close_map.account_detail(client_id, period.id, int(account_id))
    prior = detail["prior_year_context"]
    return {
        **_close_row(detail["row"]),
        "explanation": detail["row"].explanation,
        "ledger_line_count": detail["snapshot"]["ledger_line_count"],
        "aje_effect": to_dollars(detail["snapshot"]["aje_cents"]),
        "evidence": [
            {"type": item["evidence_type"], "reference": item["reference"],
             "description": item["description"], "created_by": item["created_by"]}
            for item in detail["evidence"]
        ],
        "review_notes": [
            {"body": item["body"], "status": item["status"],
             "created_by": item["created_by"], "resolution": item["resolution"]}
            for item in detail["notes"]
        ],
        "pending_explanation_proposals": [
            {"proposal_id": item["id"], "explanation": item["explanation"],
             "rationale": item["rationale"], "created_by": item["created_by"]}
            for item in detail["proposals"]
        ],
        "prior_year_context": ({
            "fiscal_year": date.fromisoformat(prior["period_end"]).year,
            "period_name": prior["period_name"],
            "period": {"start": prior["period_start"], "end": prior["period_end"]},
            "had_review": prior["had_review"],
            "explanation": prior["explanation"],
            "evidence": [
                {"type": item["evidence_type"], "reference": item["reference"],
                 "description": item["description"], "created_by": item["created_by"]}
                for item in prior["evidence"]
            ],
            "review_notes": [
                {"body": item["body"], "status": item["status"],
                 "created_by": item["created_by"], "resolution": item["resolution"]}
                for item in prior["notes"]
            ],
            "prepared_by": prior["prepared_by"] or None,
            "prepared_at": prior["prepared_at"] or None,
            "reviewed_by": prior["reviewed_by"] or None,
            "reviewed_at": prior["reviewed_at"] or None,
            "current_year_requirement": (
                "Reference only: add fresh support and complete new preparer and "
                "reviewer signoffs for this fiscal year."
            ),
        } if prior else None),
    }


@mutating
def propose_close_explanation(client_id: int, fiscal_year: int, account_id: int,
                              explanation: str, rationale: str = "") -> dict:
    """File an explanation proposal; only a person can accept or sign it."""
    from models import close_map

    period = _resolve_year_period(client_id, fiscal_year)
    proposal_id = close_map.propose_explanation(
        client_id, period.id, int(account_id), explanation, rationale
    )
    return {
        "proposal_id": proposal_id,
        "status": "pending",
        "note": ("Filed for human review in Close Map. It does not change the "
                 "account explanation and cannot sign off the balance."),
    }


def trial_balance(
    client_id: int, as_of: Optional[str] = None,
    compare_to_prior_year: bool = False,
) -> dict:
    _require_client(client_id)
    as_of_date = _parse_date(as_of, "as_of") or date.today()
    rows = ReportGenerator.trial_balance(client_id, as_of_date)
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
    result = {
        "as_of": as_of_date.isoformat(),
        "accounts": out,
        "total_debits": total_debits,
        "total_credits": total_credits,
        "balanced": total_debits == total_credits,
    }
    if compare_to_prior_year:
        comparison = ReportGenerator.comparative_trial_balance(
            client_id, as_of_date
        )
        result["comparison"] = {
            **comparison,
            "current_as_of": comparison["current_as_of"].isoformat(),
            "prior_as_of": comparison["prior_as_of"].isoformat(),
        }
    return result


def income_statement(
    client_id: int, start: str, end: str,
    compare_to_prior_year: bool = False,
) -> dict:
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
    def _groups(groups):
        return [
            {
                "key": group["key"],
                "group": group["group"],
                "accounts": [
                    {
                        "number": item["account_number"],
                        "name": item["name"],
                        "statement_subtype": item["statement_subtype"],
                        "amount": round(item["balance"], 2),
                    }
                    for item in group["accounts"]
                ],
                "subtotal": round(group["subtotal"], 2),
            }
            for group in groups
        ]
    result = {
        "start": start,
        "end": end,
        "revenues": _lines(report["revenues"]),
        "revenue_groups": _groups(report["revenue_groups"]),
        "expenses": _lines(report["expenses"]),
        "expense_groups": _groups(report["expense_groups"]),
        "total_revenue": round(report["total_revenue"], 2),
        "total_expenses": round(report["total_expenses"], 2),
        "operating_revenue": round(report["operating_revenue"], 2),
        "other_income": round(report["other_income"], 2),
        "cost_of_goods_sold": round(report["cost_of_goods_sold"], 2),
        "gross_profit": (
            None if report["gross_profit"] is None
            else round(report["gross_profit"], 2)
        ),
        "operating_expenses": round(report["operating_expenses"], 2),
        "depreciation_amortization": round(
            report["depreciation_amortization"], 2
        ),
        "operating_income": (
            None if report["operating_income"] is None
            else round(report["operating_income"], 2)
        ),
        "other_expenses": round(report["other_expenses"], 2),
        "net_income": round(report["net_income"], 2),
        "multistep_ready": report["multistep_ready"],
        "statement_warnings": report["statement_warnings"],
    }
    if compare_to_prior_year:
        comparison = ReportGenerator.comparative_income_statement(
            client_id, _parse_date(start, "start"), _parse_date(end, "end")
        )
        result["comparison"] = {
            **comparison,
            "current_period": {
                "start": comparison["current_period"]["start"].isoformat(),
                "end": comparison["current_period"]["end"].isoformat(),
            },
            "prior_period": {
                "start": comparison["prior_period"]["start"].isoformat(),
                "end": comparison["prior_period"]["end"].isoformat(),
            },
        }
    return result


def balance_sheet(
    client_id: int, as_of: str, compare_to_prior_year: bool = False,
) -> dict:
    _require_client(client_id)
    report = ReportGenerator.balance_sheet(client_id, _parse_date(as_of, "as_of"))
    def _lines(items):
        return [
            {"number": i["account_number"], "name": i["name"],
             "amount": round(i["balance"], 2)}
            for i in items
        ]
    def _groups(groups):
        return [
            {
                "key": group["key"],
                "group": group["group"],
                "accounts": [
                    {
                        "number": item["account_number"],
                        "name": item["name"],
                        "statement_subtype": item["statement_subtype"],
                        "amount": round(item["balance"], 2),
                    }
                    for item in group["accounts"]
                ],
                "subtotal": round(group["subtotal"], 2),
            }
            for group in groups
        ]
    result = {
        "as_of": as_of,
        "assets": _lines(report["assets"]),
        "asset_groups": _groups(report["asset_groups"]),
        "liabilities": _lines(report["liabilities"]),
        "liability_groups": _groups(report["liability_groups"]),
        "equity": _lines(report["equity"]),  # includes RE + current-year earnings
        "equity_groups": _groups(report["equity_groups"]),
        "total_assets": round(report["total_assets"], 2),
        "total_liabilities": round(report["total_liabilities"], 2),
        "total_equity": round(report["total_equity"], 2),
        "total_liabilities_equity": round(report["total_liabilities_equity"], 2),
        "balanced": round(report["total_assets"], 2)
        == round(report["total_liabilities_equity"], 2),
    }
    if compare_to_prior_year:
        comparison = ReportGenerator.comparative_balance_sheet(
            client_id, _parse_date(as_of, "as_of")
        )
        result["comparison"] = {
            **comparison,
            "current_as_of": comparison["current_as_of"].isoformat(),
            "prior_as_of": comparison["prior_as_of"].isoformat(),
        }
    return result


def cash_flow_statement(
    client_id: int, start: str, end: str,
    compare_to_prior_year: bool = False,
) -> dict:
    """Derived indirect-method cash flow with explicit quality diagnostics."""
    _require_client(client_id)
    start_date = _parse_date(start, "start")
    end_date = _parse_date(end, "end")
    report = ReportGenerator.cash_flow_statement(
        client_id, start_date, end_date
    )
    result = {
        **report,
        "start_date": report["start_date"].isoformat(),
        "end_date": report["end_date"].isoformat(),
    }
    if compare_to_prior_year:
        comparison = ReportGenerator.comparative_cash_flow_statement(
            client_id, start_date, end_date, current_report=report
        )
        result["comparison"] = {
            **comparison,
            "current_period": {
                "start": comparison["current_period"]["start"].isoformat(),
                "end": comparison["current_period"]["end"].isoformat(),
            },
            "prior_period": {
                "start": comparison["prior_period"]["start"].isoformat(),
                "end": comparison["prior_period"]["end"].isoformat(),
            },
        }
    return result


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
                 entry_type: Optional[str] = None, limit: int = 50) -> dict:
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
    results = [
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
    return {"entries": results, "count": len(results)}


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


INTEGRITY_CHECKS = [
    "entry_balance",
    "minimum_entry_lines",
    "unposted_imports",
    "posted_import_links",
    "future_dated_entries",
    "quiet_profit_and_loss_accounts",
    "import_row_continuity",
]


def integrity_sweep(client_id: int, start: str, end: str) -> dict:
    """Deterministic bookkeeping checks: unbalanced or short entries, unposted
    imports, broken import links, future dates, quiet P&L accounts,
    import row gaps. Always returns an explicit result, including when the book
    is clean, so an empty finding set cannot be mistaken for a failed tool."""
    _require_client(client_id)
    period_start = _parse_date(start, "start")
    period_end = _parse_date(end, "end")
    findings = run_integrity_sweep(client_id, period_start, period_end)
    finding_rows = [
        {
            "severity": f.severity,
            "check": f.skill,
            "title": f.title,
            "detail": f.detail,
            "entry_id": f.entry_id,
        }
        for f in findings
    ]
    return {
        "period": {
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
        },
        "checks_run": list(INTEGRITY_CHECKS),
        "clean": not finding_rows,
        "findings": finding_rows,
    }


@mutating
def propose_entry(client_id: int, entry_date: str, description: str,
                  lines: list, rationale: str = "",
                  entry_type: str = "Regular",
                  original_entry_id: int | None = None) -> dict:
    """File a DRAFT journal entry for human review. Never touches the ledger:
    a person approves or rejects it in LedgerTB. lines: dicts with
    account_number and debit or credit in dollars (optional memo). Set
    original_entry_id when this proposal corrects an existing entry so the
    accountant can review the complete chain."""
    from models.draft_entry import DraftEntry, DraftLine
    from money import to_cents

    _require_client(client_id)
    _parse_date(entry_date, "entry_date")
    draft = DraftEntry(
        client_id=client_id,
        proposed_by="Assistant (MCP)",
        entry_date=entry_date,
        entry_type=entry_type,
        description=description,
        rationale=rationale,
        original_entry_id=original_entry_id,
        lines=[DraftLine(
            account_number=str(l.get("account_number", "")),
            debit_cents=to_cents(float(l.get("debit", 0) or 0)),
            credit_cents=to_cents(float(l.get("credit", 0) or 0)),
            memo=str(l.get("memo", "") or ""),
        ) for l in lines],
    )
    draft_id = draft.save()
    return {
        "draft_id": draft_id,
        "original_entry_id": original_entry_id,
        "status": "pending",
        "note": ("Filed for human review — it posts only if approved in "
                 "LedgerTB (Journal Entries → Drafts)."),
    }


@mutating
def propose_correction(client_id: int, original_entry_id: int,
                       entry_date: str, description: str, lines: list,
                       rationale: str = "",
                       entry_type: str = "Regular") -> dict:
    """File a correction draft with a required, validated original entry."""
    return propose_entry(
        client_id=client_id,
        entry_date=entry_date,
        description=description,
        lines=lines,
        rationale=rationale,
        entry_type=entry_type,
        original_entry_id=original_entry_id,
    )


def list_drafts(client_id: int, status: str = "pending") -> list:
    """Draft entries, accounting type, and review status ('pending', or any
    status via 'all')."""
    from database.connection import get_cursor

    _require_client(client_id)
    query = "SELECT * FROM draft_entries WHERE client_id = ?"
    params = [client_id]
    if status != "all":
        query += " AND status = ?"
        params.append(status)
    with get_cursor() as cursor:
        cursor.execute(query + " ORDER BY id", params)
        rows = cursor.fetchall()
    import json as _json
    return [
        {
            "draft_id": r["id"],
            "status": r["status"],
            "entry_date": r["entry_date"],
            "entry_type": r["entry_type"],
            "description": r["description"],
            "rationale": r["rationale"] or "",
            "original_entry_id": r["original_entry_id"],
            "posted_entry_id": r["posted_entry_id"],
            "lines": [
                {"account_number": l["account_number"],
                 "debit": round(l["debit_cents"] / 100, 2),
                 "credit": round(l["credit_cents"] / 100, 2),
                 "memo": l.get("memo", "")}
                for l in _json.loads(r["lines_json"])
            ],
        }
        for r in rows
    ]


@mutating
def propose_import(client_id: int, bank_account_number: str, rows: list,
                   source_label: str = "Assistant import",
                   sign_convention: str = "auto") -> dict:
    """Stage bank/card statement rows for human review in LedgerTB's import
    flow. Amounts use the source register's convention: bank withdrawals are
    negative, while card charges are positive. LedgerTB normalizes both so
    negative always means money out. Rows never post from here: a person
    categorizes and posts them in the app, with the same duplicate protection
    as a CSV import."""
    import json as _json

    from models.transaction import ImportedTransaction
    from services.csv_import import (
        SIGN_CONVENTIONS,
        apply_sign_convention,
        default_sign_convention,
    )
    from services.import_identity import (
        classify_import_duplicates,
        hash_source,
    )
    from database.connection import get_cursor

    _require_client(client_id)
    account = _resolve_account(client_id, bank_account_number)
    if account.type not in ("Asset", "Liability"):
        raise ValueError(
            "bank_account_number must be a cash or credit-card account "
            f"(got {account.type}). Sign convention: positive = money in / "
            "deposit, negative = money out / charge paid."
        )
    if not rows:
        raise ValueError("No rows to stage.")
    if len(rows) > 500:
        raise ValueError("Stage at most 500 rows per proposal.")
    if sign_convention not in {"auto", *SIGN_CONVENTIONS}:
        raise ValueError(
            "sign_convention must be auto, bank, credit_card, or flip."
        )

    resolved_sign_convention = (
        default_sign_convention(account.type)
        if sign_convention == "auto"
        else sign_convention
    )
    staged_dicts = []
    for i, r in enumerate(rows, start=1):
        row_date = _parse_date(str(r.get("date", "")), f"rows[{i}].date")
        description = str(r.get("description", "")).strip()
        if not description:
            raise ValueError(f"rows[{i}] needs a description.")
        try:
            amount = round(
                apply_sign_convention(
                    float(r.get("amount")), resolved_sign_convention
                ),
                2,
            )
        except (TypeError, ValueError):
            raise ValueError(f"rows[{i}].amount must be a number.")
        if amount == 0:
            raise ValueError(f"rows[{i}].amount cannot be zero.")
        staged_dicts.append({
            "date": row_date,
            "description": description,
            "amount": amount,
            "client_id": client_id,
            "bank_account_id": account.id,
            "source_row_number": i,
        })

    content = _json.dumps(
        [[d["date"].isoformat(), d["description"], d["amount"]]
         for d in staged_dicts] + [account.account_number],
        separators=(",", ":"),
    ).encode("utf-8")
    source_id = hash_source(content)
    batch_id = f"mcp-{source_id[:8]}"
    for d in staged_dicts:
        d["source_id"] = source_id
        d["source_filename"] = source_label

    classify_import_duplicates(
        staged_dicts, client_id, persist_backfill=False
    )

    # Never double-stage: rows whose identity already exists (staged earlier
    # or already posted) are skipped, so re-proposing is harmless.
    keys = [d["idempotency_key"] for d in staged_dicts]
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT idempotency_key FROM imported_transactions "
            f"WHERE client_id = ? AND idempotency_key IN ({','.join('?' * len(keys))})",
            [client_id] + keys,
        )
        already = {r["idempotency_key"] for r in cursor.fetchall()}
    fresh = [d for d in staged_dicts if d["idempotency_key"] not in already]
    flagged_fresh = sum(bool(d.get("is_duplicate")) for d in fresh)

    if fresh:
        ImportedTransaction.bulk_insert([
            ImportedTransaction(
                client_id=client_id,
                import_batch=batch_id,
                transaction_date=d["date"],
                description=d["description"][:200],
                amount=d["amount"],
                bank_account_id=account.id,
                status="Pending",
                source_id=source_id,
                source_filename=source_label,
                source_row_number=d["source_row_number"],
                row_fingerprint=d["row_fingerprint"],
                idempotency_key=d["idempotency_key"],
            )
            for d in fresh
        ])
    return {
        "batch_id": batch_id,
        "staged": len(fresh),
        "skipped_already_known": len(staged_dicts) - len(fresh),
        "flagged_as_possible_duplicates": flagged_fresh,
        "staged_clean": len(fresh) - flagged_fresh,
        "note": ("Staged for human review — LedgerTB -> Import Transactions "
                 "-> Review & Categorize. Exact retries are skipped; rows with "
                 "the same accounting facts from a different batch are staged "
                 "but flagged for a person. Nothing posts until a person "
                 "categorizes and posts it there."),
    }


def list_staged_imports(client_id: int) -> list:
    """Assistant-staged transactions still awaiting review (status Pending)."""
    from models.transaction import ImportedTransaction

    _require_client(client_id)
    return [
        {
            "id": t.id,
            "batch_id": t.import_batch,
            "date": t.transaction_date.isoformat()
            if hasattr(t.transaction_date, "isoformat") else str(t.transaction_date),
            "description": t.description,
            "amount": t.amount,
            "source": t.source_filename or "",
        }
        for t in ImportedTransaction.get_by_status(client_id, "Pending")
    ]


@mutating
def post_entry(client_id: int, entry_date: str, description: str,
               lines: list, entry_type: str = "Regular") -> dict:
    """POST a balanced journal entry directly (assistant access level "post"
    only — append-only: an assistant can add entries but can never edit or
    delete anything). lines: dicts with account_number and debit or credit in
    dollars (optional memo)."""
    from datetime import date as _date

    from models.journal_entry import JournalEntry, JournalEntryLine

    _require_client(client_id)
    when = _parse_date(entry_date, "entry_date")
    if entry_type not in EntryType.ALL:
        raise ValueError(
            "entry_type must be one of: " + ", ".join(EntryType.ALL) + ".")
    by_number = {a.account_number: a.id
                 for a in Account.get_all(client_id, active_only=False)}
    entry_lines = []
    for i, l in enumerate(lines, start=1):
        number = str(l.get("account_number", ""))
        if number not in by_number:
            raise ValueError(f"lines[{i}]: no account numbered {number}.")
        entry_lines.append(JournalEntryLine(
            account_id=by_number[number],
            debit=round(float(l.get("debit", 0) or 0), 2),
            credit=round(float(l.get("credit", 0) or 0), 2),
            memo=str(l.get("memo", "") or "") or None,
        ))
    entry = JournalEntry(
        client_id=client_id,
        entry_date=when if isinstance(when, _date) else _date.fromisoformat(entry_date),
        description=description,
        entry_type=entry_type,
        source_reference="Posted by assistant (MCP)",
        lines=entry_lines,
    )
    entry_id = entry.save()  # model validation: unbalanced entries never post
    return {
        "entry_id": entry_id,
        "note": ("Posted to the ledger (append-only). To undo, a person "
                 "reverses it in the app — assistant connections can never "
                 "edit or delete entries."),
    }


@mutating
def export_close_package(client_id: int, period_start: str, period_end: str,
                         out_dir: str = "") -> dict:
    """Write the close package (branded PDF + Excel workbook) to disk so a
    workpaper tool (e.g. LedgerPDF) can ingest it. Filesystem writes are
    consent-gated: out_dir must sit inside the book's approved export folder
    or a folder named by the LEDGERTB_MCP_EXPORT_ROOTS environment variable
    (os.pathsep-separated); no approved folder means all exports are refused."""
    import os
    import re
    from pathlib import Path

    from models.audit_log import AuditLog
    from models.client import Client
    from models.reports import ReportGenerator
    from services.close_package import (
        build_close_package,
        build_close_package_pdf,
        consistent_export_window,
        load_close_package_snapshot,
    )

    client = _require_client(client_id)
    start = _parse_date(period_start, "period_start")
    end = _parse_date(period_end, "period_end")
    if start is None or end is None or start > end:
        raise ValueError("period_start and period_end must be ISO dates in order.")

    # The export folder is the user's consent boundary. Normal path: chosen on
    # Data Safety and stored in the OS vault (outside the assistant's reach,
    # like the access level). The env var remains as a power-user override so
    # config-managed setups keep working.
    from database import connection as dbconn
    from utils import secure_store
    from utils.assistant_access import credential_names

    # The folder chosen in the app WINS. It used to be possible to override it
    # from the environment, but an MCP server's environment comes from the
    # client's own config file — which a shell-capable assistant (Claude Code
    # has Write and Bash) can edit. That turned a user's consent decision into
    # something the assistant could rewrite for itself, so the override is gone
    # and the env var is only a fallback when nothing was chosen in the app.
    names = credential_names(dbconn.DATABASE_PATH)
    roots_raw = (secure_store.get_secret(names.export_roots)
                 or os.environ.get("LEDGERTB_MCP_EXPORT_ROOTS", "")
                 or os.environ.get("PROBOOKS_MCP_EXPORT_ROOTS", "") or "")
    roots = [Path(p).expanduser().resolve()
             for p in roots_raw.split(os.pathsep) if p.strip()]
    if not roots:
        raise ValueError(
            "File export is off: choose an export folder on LedgerTB -> "
            "Data Safety -> Assistant access (or set the "
            "LEDGERTB_MCP_EXPORT_ROOTS environment variable)."
        )
    requested_out_dir = (out_dir or "").strip()
    if requested_out_dir:
        requested_path = Path(requested_out_dir).expanduser()
        target = (
            requested_path.resolve()
            if requested_path.is_absolute()
            else (roots[0] / requested_path).resolve()
        )
    else:
        target = roots[0]
    if not any(target == root or root in target.parents for root in roots):
        approved = os.pathsep.join(str(root) for root in roots)
        raise ValueError(
            f"{out_dir} is outside the approved export folder(s): {approved}. "
            "Omit out_dir to export into the first approved folder."
        )
    target.mkdir(parents=True, exist_ok=True, mode=0o700)

    safe_client = re.sub(r"[^A-Za-z0-9 ._-]", "_", client.name).strip() or "client"
    stem = f"{safe_client} close package {start.isoformat()} to {end.isoformat()}"
    pdf_path = target / f"{stem}.pdf"
    xlsx_path = target / f"{stem}.xlsx"
    with consistent_export_window():
        tb_rows, _ = ReportGenerator.trial_balance_worksheet(client_id, start, end)
        snapshot = load_close_package_snapshot(client_id, start, end)
        _write_private(
            pdf_path,
            build_close_package_pdf(
                client_id, client.name, start, end, tb_rows, snapshot=snapshot
            ).read(),
        )
        _write_private(
            xlsx_path,
            build_close_package(
                client_id, client.name, start, end, tb_rows, snapshot=snapshot
            ).read(),
        )

    AuditLog.log_event(client_id, "EXPORT", "close_package_mcp", {
        "start_date": start, "end_date": end,
        "pdf": pdf_path.name, "xlsx": xlsx_path.name, "directory": str(target),
    })
    return {
        "pdf": str(pdf_path),
        "xlsx": str(xlsx_path),
        "accounts": len(tb_rows),
        "note": ("Both files written. The Excel totals are computed values "
                 "(no uncalculated formulas), so a workpaper tool reads them "
                 "exactly."),
    }


@mutating
def create_client(name: str, entity_type: str = "",
                  fiscal_year_end_month: int = 12,
                  seed_default_chart: bool = True,
                  initial_fiscal_year: int = 0) -> dict:
    """Create a new client (set of books), optionally seeded with the default
    chart of accounts. Available at access level 'propose' and above."""
    from models.client import Client

    name = (name or "").strip()
    if not name:
        raise ValueError("The client needs a name.")
    try:
        fiscal_end_month = int(fiscal_year_end_month)
    except (TypeError, ValueError):
        raise ValueError("fiscal_year_end_month must be 1-12.")
    if not 1 <= fiscal_end_month <= 12:
        raise ValueError("fiscal_year_end_month must be 1-12.")
    if initial_fiscal_year:
        fiscal_year = _validated_fiscal_year(initial_fiscal_year)
    else:
        from utils.fiscal_dates import fiscal_year_ending_year

        fiscal_year = fiscal_year_ending_year(date.today(), fiscal_end_month)
    existing = [c for c in Client.get_all(active_only=False) if c.name == name]
    if existing:
        raise ValueError(
            f"A client named {name!r} already exists (client_id "
            f"{existing[0].id}) — use it rather than creating a duplicate."
        )
    client = Client(
        name=name,
        entity_type=(entity_type or "").strip() or None,
        fiscal_year_end_month=fiscal_end_month,
    )
    from database.connection import get_connection
    from models.fiscal_period import FiscalPeriod

    conn = get_connection()
    try:
        client_id = client.save(
            seed_accounts=bool(seed_default_chart), conn=conn
        )
        periods = FiscalPeriod.ensure_periods_exist(
            client_id, fiscal_year, fiscal_end_month, conn=conn
        )
        year_period = next(
            period for period in periods
            if period.period_name == f"FY {fiscal_year}"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    chart = list_accounts(client_id)
    return {
        "client_id": client_id,
        "accounts_seeded": len(chart) if seed_default_chart else 0,
        "fiscal_year": fiscal_year,
        "fiscal_year_period": {
            "start": year_period.start_date.isoformat(),
            "end": year_period.end_date.isoformat(),
        },
        "note": ("Client created" +
                 (" with the default chart of accounts."
                  if seed_default_chart else
                  " with an empty chart — import_accounts can fill it.")),
    }


@mutating
def import_accounts(client_id: int, rows: list) -> dict:
    """Add accounts to a client's chart. rows: dicts with number, name, type
    (canonical or QuickBooks type names — Bank, Credit Card, Accounts
    Receivable (A/R), Cost of Goods Sold, … — which imply subtypes), optional
    subtype/description. Existing numbers are skipped; unmappable rows are
    reported, never silently dropped. Available at 'propose' and above."""
    from models.account import Account
    from services.coa_import import normalize_import_subtype, normalize_type

    _require_client(client_id)
    if not rows:
        raise ValueError("No account rows given.")
    existing = {a.account_number for a in Account.get_all(client_id, active_only=False)}
    created, skipped_existing, errors, warnings = [], [], [], []
    assigned_numbers = []
    seen = set()
    for i, r in enumerate(rows, start=1):
        number = str(r.get("number", "")).strip()
        name = str(r.get("name", "")).strip()
        raw_type = str(r.get("type", "")).strip()
        if not name or not raw_type:
            errors.append(f"rows[{i}]: needs name and type (number optional).")
            continue
        if number and number in seen:
            errors.append(f"rows[{i}]: duplicate number {number} in this request.")
            continue
        if number:
            seen.add(number)
        if number and number in existing:
            skipped_existing.append(number)
            continue
        mapped = normalize_type(raw_type)
        if mapped is None:
            errors.append(
                f"rows[{i}]: unknown type {raw_type!r} (#{number} {name}) — "
                "NOT imported."
            )
            continue
        acct_type, implied_subtype = mapped
        if not number:
            from services.coa_import import assign_missing_numbers

            pending = {"number": "", "name": name, "type": acct_type}
            assign_missing_numbers([pending], taken=existing | seen)
            number = pending["number"]
            seen.add(number)
            assigned_numbers.append((number, name))
        explicit_subtype = str(
            r.get("subtype", "") or r.get("detail_type", "") or ""
        ).strip()
        stored_subtype, warning = normalize_import_subtype(
            acct_type, name, explicit_subtype, implied_subtype
        )
        if warning:
            warnings.append(f"rows[{i}]: #{number} {warning}")
        account = Account(
            client_id=client_id,
            account_number=number,
            name=name,
            type=acct_type,
            subtype=stored_subtype,
            description=str(r.get("description", "") or "").strip() or None,
        )
        account.save()
        created.append(number)
    return {
        "created": len(created),
        "skipped_existing": skipped_existing,
        "numbers_assigned": [{"number": no, "name": nm}
                             for no, nm in assigned_numbers],
        "errors": errors,
        "warnings": warnings,
        "note": ("Every row is accounted for above — nothing is silently "
                 "dropped. Fix error rows and re-send just those; review any "
                 "semantic warnings on rows that were created."),
    }
