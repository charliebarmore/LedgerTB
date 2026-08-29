"""Account-level close evidence, review notes, and append-only signoffs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from constants import AccountType
from database.connection import get_cursor
from models.audit_log import AuditLog
from money import to_dollars
from utils.actor import current_actor
from utils.fiscal_dates import prior_year_date


NOT_STARTED = "Not started"
IN_PROGRESS = "In progress"
PREPARED = "Prepared"
REVIEWED = "Reviewed"
CHANGED = "Changed"
EXCEPTION = "Exception"
NOT_REQUIRED = "Not required"


@dataclass
class CloseMapRow:
    account_id: int
    account_number: str
    account_name: str
    account_type: str
    current_balance: float
    prior_balance: float
    change: float
    change_percent: Optional[float]
    group_id: Optional[int]
    group_code: str
    group_name: str
    required: bool
    exclusion_reason: str
    review_id: Optional[int]
    explanation: str
    evidence_count: int
    evidence_references: str
    open_note_count: int
    open_notes: str
    pending_proposal_count: int
    status: str
    prepared_by: str = ""
    prepared_at: str = ""
    reviewed_by: str = ""
    reviewed_at: str = ""


def _period(cursor, client_id: int, period_id: int):
    row = cursor.execute(
        "SELECT * FROM fiscal_periods WHERE id = ? AND client_id = ? "
        "AND period_type = 'Year'", (period_id, client_id),
    ).fetchone()
    if not row:
        raise ValueError("Fiscal year not found for this client.")
    return row


def _account(cursor, client_id: int, account_id: int):
    row = cursor.execute(
        "SELECT * FROM accounts WHERE id = ? AND client_id = ?",
        (account_id, client_id),
    ).fetchone()
    if not row:
        raise ValueError("Account not found for this client.")
    return row


def _ensure_review(cursor, client_id: int, period_id: int, account_id: int) -> int:
    _period(cursor, client_id, period_id)
    _account(cursor, client_id, account_id)
    row = cursor.execute(
        "SELECT id FROM account_close_reviews WHERE client_id = ? "
        "AND fiscal_period_id = ? AND account_id = ?",
        (client_id, period_id, account_id),
    ).fetchone()
    if row:
        return row["id"]
    cursor.execute(
        "INSERT INTO account_close_reviews "
        "(client_id, fiscal_period_id, account_id, updated_by) VALUES (?, ?, ?, ?)",
        (client_id, period_id, account_id, current_actor()),
    )
    review_id = cursor.lastrowid
    AuditLog.write(cursor, client_id, "account_close_reviews", review_id, "INSERT",
                   new_values={"period_id": period_id, "account_id": account_id})
    return review_id


def list_groups(client_id: int, active_only: bool = True) -> list[dict]:
    query = "SELECT * FROM lead_sheet_groups WHERE client_id = ?"
    if active_only:
        query += " AND is_active = 1"
    query += " ORDER BY sort_order, code"
    with get_cursor() as cursor:
        return [dict(row) for row in cursor.execute(query, (client_id,)).fetchall()]


def create_group(client_id: int, code: str, name: str) -> int:
    code, name = (code or "").strip().upper(), (name or "").strip()
    if not code or not name:
        raise ValueError("A lead-sheet code and name are required.")
    with get_cursor(commit=True) as cursor:
        if not cursor.execute("SELECT 1 FROM clients WHERE id = ?", (client_id,)).fetchone():
            raise ValueError("Client not found.")
        if cursor.execute(
            "SELECT 1 FROM lead_sheet_groups WHERE client_id = ? AND code = ?",
            (client_id, code),
        ).fetchone():
            raise ValueError(f"Lead-sheet code {code} already exists.")
        cursor.execute(
            "INSERT INTO lead_sheet_groups (client_id, code, name) VALUES (?, ?, ?)",
            (client_id, code, name),
        )
        group_id = cursor.lastrowid
        AuditLog.write(cursor, client_id, "lead_sheet_groups", group_id, "INSERT",
                       new_values={"code": code, "name": name})
    return group_id


def update_group(client_id: int, group_id: int, code: str, name: str) -> None:
    code, name = (code or "").strip().upper(), (name or "").strip()
    if not code or not name:
        raise ValueError("A lead-sheet code and name are required.")
    with get_cursor(commit=True) as cursor:
        old = cursor.execute(
            "SELECT * FROM lead_sheet_groups WHERE id = ? AND client_id = ?",
            (group_id, client_id),
        ).fetchone()
        if not old:
            raise ValueError("Lead-sheet group not found for this client.")
        duplicate = cursor.execute(
            "SELECT 1 FROM lead_sheet_groups WHERE client_id = ? AND code = ? AND id != ?",
            (client_id, code, group_id),
        ).fetchone()
        if duplicate:
            raise ValueError(f"Lead-sheet code {code} already exists.")
        cursor.execute(
            "UPDATE lead_sheet_groups SET code = ?, name = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ? AND client_id = ?",
            (code, name, group_id, client_id),
        )
        AuditLog.write(cursor, client_id, "lead_sheet_groups", group_id, "UPDATE",
                       old_values={"code": old["code"], "name": old["name"]},
                       new_values={"code": code, "name": name})


def bulk_assign_group(client_id: int, account_ids: list[int], group_id: int) -> int:
    unique_ids = list(dict.fromkeys(int(account_id) for account_id in account_ids))
    if not unique_ids:
        raise ValueError("Select at least one account.")
    with get_cursor(commit=True) as cursor:
        if not cursor.execute(
            "SELECT 1 FROM lead_sheet_groups WHERE id = ? AND client_id = ?",
            (group_id, client_id),
        ).fetchone():
            raise ValueError("Lead-sheet group not found for this client.")
        for account_id in unique_ids:
            _account(cursor, client_id, account_id)
            old = cursor.execute(
                "SELECT * FROM account_close_mappings WHERE account_id = ? "
                "AND client_id = ?", (account_id, client_id),
            ).fetchone()
            if old:
                cursor.execute(
                    "UPDATE account_close_mappings SET lead_sheet_group_id = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE account_id = ? AND client_id = ?",
                    (group_id, account_id, client_id),
                )
                action = "UPDATE"
            else:
                cursor.execute(
                    "INSERT INTO account_close_mappings "
                    "(account_id, client_id, lead_sheet_group_id) VALUES (?, ?, ?)",
                    (account_id, client_id, group_id),
                )
                action = "INSERT"
            AuditLog.write(
                cursor, client_id, "account_close_mappings", account_id, action,
                old_values=dict(old) if old else None,
                new_values={"group_id": group_id,
                            "required": (old["review_requirement"] == "required"
                                         if old else True)},
            )
    return len(unique_ids)


def save_mapping(client_id: int, account_id: int, group_id: Optional[int],
                 required: bool, exclusion_reason: str = "") -> None:
    reason = (exclusion_reason or "").strip()
    if not required and not reason:
        raise ValueError("Explain why this account does not require review.")
    with get_cursor(commit=True) as cursor:
        _account(cursor, client_id, account_id)
        if group_id is not None and not cursor.execute(
            "SELECT 1 FROM lead_sheet_groups WHERE id = ? AND client_id = ?",
            (group_id, client_id),
        ).fetchone():
            raise ValueError("Lead-sheet group not found for this client.")
        old = cursor.execute(
            "SELECT * FROM account_close_mappings WHERE account_id = ? AND client_id = ?",
            (account_id, client_id),
        ).fetchone()
        requirement = "required" if required else "not_required"
        if old:
            cursor.execute(
                "UPDATE account_close_mappings SET lead_sheet_group_id = ?, "
                "review_requirement = ?, exclusion_reason = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE account_id = ? AND client_id = ?",
                (group_id, requirement, None if required else reason,
                 account_id, client_id),
            )
            action = "UPDATE"
        else:
            cursor.execute(
                "INSERT INTO account_close_mappings "
                "(account_id, client_id, lead_sheet_group_id, review_requirement, exclusion_reason) "
                "VALUES (?, ?, ?, ?, ?)",
                (account_id, client_id, group_id, requirement,
                 None if required else reason),
            )
            action = "INSERT"
        AuditLog.write(
            cursor, client_id, "account_close_mappings", account_id, action,
            old_values=dict(old) if old else None,
            new_values={"group_id": group_id, "required": required,
                        "exclusion_reason": None if required else reason},
        )


def save_explanation(client_id: int, period_id: int, account_id: int,
                     explanation: str) -> int:
    explanation = (explanation or "").strip()
    with get_cursor(commit=True) as cursor:
        review_id = _ensure_review(cursor, client_id, period_id, account_id)
        old = cursor.execute(
            "SELECT explanation FROM account_close_reviews WHERE id = ?", (review_id,),
        ).fetchone()["explanation"]
        cursor.execute(
            "UPDATE account_close_reviews SET explanation = ?, updated_by = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ? AND client_id = ?",
            (explanation, current_actor(), review_id, client_id),
        )
        AuditLog.write(cursor, client_id, "account_close_reviews", review_id, "UPDATE",
                       old_values={"explanation": old},
                       new_values={"explanation": explanation})
    return review_id


def add_evidence(client_id: int, period_id: int, account_id: int,
                 evidence_type: str, reference: str, description: str = "") -> int:
    if evidence_type not in {"workpaper", "ledgerpdf", "external", "reconciliation"}:
        raise ValueError("Unknown evidence type.")
    reference = (reference or "").strip()
    if not reference:
        raise ValueError("An evidence reference is required.")
    with get_cursor(commit=True) as cursor:
        review_id = _ensure_review(cursor, client_id, period_id, account_id)
        cursor.execute(
            "INSERT INTO account_close_evidence "
            "(client_id, review_id, evidence_type, reference, description, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (client_id, review_id, evidence_type, reference,
             (description or "").strip(), current_actor()),
        )
        evidence_id = cursor.lastrowid
        AuditLog.write(cursor, client_id, "account_close_evidence", evidence_id, "INSERT",
                       new_values={"review_id": review_id, "type": evidence_type,
                                   "reference": reference})
    return evidence_id


def remove_evidence(client_id: int, evidence_id: int) -> None:
    with get_cursor(commit=True) as cursor:
        row = cursor.execute(
            "SELECT * FROM account_close_evidence WHERE id = ? AND client_id = ?",
            (evidence_id, client_id),
        ).fetchone()
        if not row:
            raise ValueError("Evidence reference not found.")
        cursor.execute("DELETE FROM account_close_evidence WHERE id = ?", (evidence_id,))
        AuditLog.write(cursor, client_id, "account_close_evidence", evidence_id, "DELETE",
                       old_values=dict(row))


def add_note(client_id: int, period_id: int, account_id: int, body: str) -> int:
    body = (body or "").strip()
    if not body:
        raise ValueError("A review note cannot be empty.")
    with get_cursor(commit=True) as cursor:
        review_id = _ensure_review(cursor, client_id, period_id, account_id)
        cursor.execute(
            "INSERT INTO account_review_notes "
            "(client_id, review_id, body, created_by) VALUES (?, ?, ?, ?)",
            (client_id, review_id, body, current_actor()),
        )
        note_id = cursor.lastrowid
        AuditLog.write(cursor, client_id, "account_review_notes", note_id, "INSERT",
                       new_values={"review_id": review_id, "body": body})
    return note_id


def resolve_note(client_id: int, note_id: int, resolution: str) -> None:
    resolution = (resolution or "").strip()
    if not resolution:
        raise ValueError("Explain how the review note was resolved.")
    with get_cursor(commit=True) as cursor:
        row = cursor.execute(
            "SELECT * FROM account_review_notes WHERE id = ? AND client_id = ?",
            (note_id, client_id),
        ).fetchone()
        if not row or row["status"] != "open":
            raise ValueError("Open review note not found.")
        cursor.execute(
            "UPDATE account_review_notes SET status = 'resolved', resolution = ?, "
            "resolved_by = ?, resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
            (resolution, current_actor(), note_id),
        )
        AuditLog.write(cursor, client_id, "account_review_notes", note_id, "UPDATE",
                       old_values={"status": "open"},
                       new_values={"status": "resolved", "resolution": resolution})


def _normal_balance(account_type: str, debit_minus_credit_cents: int) -> int:
    return (debit_minus_credit_cents if AccountType.is_debit_normal(account_type)
            else -debit_minus_credit_cents)


def _fingerprint_with_cursor(cursor, client_id: int, period_id: int,
                             account_id: int) -> tuple[str, dict]:
    period = _period(cursor, client_id, period_id)
    account = _account(cursor, client_id, account_id)
    review = cursor.execute(
        "SELECT * FROM account_close_reviews WHERE client_id = ? "
        "AND fiscal_period_id = ? AND account_id = ?",
        (client_id, period_id, account_id),
    ).fetchone()
    mapping = cursor.execute(
        "SELECT m.*, g.code group_code, g.name group_name "
        "FROM account_close_mappings m LEFT JOIN lead_sheet_groups g "
        "ON g.id = m.lead_sheet_group_id WHERE m.client_id = ? AND m.account_id = ?",
        (client_id, account_id),
    ).fetchone()
    lines = cursor.execute(
        "SELECT jel.id line_id, je.id entry_id, je.entry_date, je.entry_type, "
        "je.description, je.source_reference, jel.debit, jel.credit, jel.memo "
        "FROM journal_entry_lines jel JOIN journal_entries je "
        "ON je.id = jel.journal_entry_id WHERE je.client_id = ? "
        "AND jel.account_id = ? AND je.entry_date <= ? "
        "ORDER BY je.entry_date, je.id, jel.id",
        (client_id, account_id, period["end_date"]),
    ).fetchall()
    recs = cursor.execute(
        "SELECT br.id, br.statement_start_date, br.statement_end_date, "
        "br.statement_ending_balance, br.status, br.completed_at, br.updated_at "
        "FROM bank_reconciliations br WHERE br.client_id = ? AND br.account_id = ? "
        "AND br.statement_end_date <= ? ORDER BY br.statement_end_date, br.id",
        (client_id, account_id, period["end_date"]),
    ).fetchall()
    evidence, notes = [], []
    if review:
        evidence = cursor.execute(
            "SELECT id, evidence_type, reference, description, created_by, created_at "
            "FROM account_close_evidence WHERE review_id = ? ORDER BY id",
            (review["id"],),
        ).fetchall()
        notes = cursor.execute(
            "SELECT id, body, status, created_by, created_at, resolved_by, "
            "resolved_at, resolution FROM account_review_notes WHERE review_id = ? "
            "ORDER BY id", (review["id"],),
        ).fetchall()
    # The signed balance must be the figure the Close Map row shows: period
    # ordinary activity for income-statement accounts, life-to-date for
    # balance-sheet accounts (docs/EARNINGS-ATTRIBUTION.md). The ledger_lines
    # payload keeps the full history for context either way.
    if account["type"] in ("Revenue", "Expense"):
        raw_balance = sum(
            int(row["debit"] or 0) - int(row["credit"] or 0) for row in lines
            if period["start_date"] <= row["entry_date"] <= period["end_date"]
            and row["entry_type"] not in ("Beginning Balance", "Closing")
        )
    else:
        raw_balance = sum(
            int(row["debit"] or 0) - int(row["credit"] or 0) for row in lines
        )
    aje_raw = sum(
        int(row["debit"] or 0) - int(row["credit"] or 0) for row in lines
        if row["entry_type"] == "Adjusting"
        and period["start_date"] <= row["entry_date"] <= period["end_date"]
    )
    balance = _normal_balance(account["type"], raw_balance)
    mapping_payload = {
        "lead_sheet_group_id": mapping["lead_sheet_group_id"] if mapping else None,
        "group_code": (mapping["group_code"] or "") if mapping else "",
        "group_name": (mapping["group_name"] or "") if mapping else "",
        "review_requirement": (mapping["review_requirement"] if mapping else "required"),
        "exclusion_reason": (mapping["exclusion_reason"] or "") if mapping else "",
    }
    payload = {
        "period": {"id": period_id, "start": period["start_date"], "end": period["end_date"]},
        "account": {"id": account_id, "number": account["account_number"],
                    "name": account["name"], "type": account["type"]},
        "mapping": mapping_payload,
        "explanation": review["explanation"] if review else "",
        "ledger_lines": [dict(row) for row in lines],
        "reconciliations": [dict(row) for row in recs],
        "evidence": [dict(row) for row in evidence],
        "notes": [dict(row) for row in notes],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    snapshot = {
        "balance_cents": balance,
        "aje_cents": _normal_balance(account["type"], aje_raw),
        "ledger_line_count": len(lines),
        "evidence_count": len(evidence),
        "open_note_count": sum(row["status"] == "open" for row in notes),
    }
    return hashlib.sha256(canonical.encode()).hexdigest(), snapshot


def fingerprint(client_id: int, period_id: int, account_id: int) -> tuple[str, dict]:
    with get_cursor() as cursor:
        return _fingerprint_with_cursor(cursor, client_id, period_id, account_id)


def signoff(client_id: int, period_id: int, account_id: int, role: str) -> int:
    if role not in {"preparer", "reviewer"}:
        raise ValueError("Signoff role must be preparer or reviewer.")
    actor = current_actor()
    if actor.endswith("(AI)"):
        raise PermissionError("An assistant cannot sign off its own work.")
    with get_cursor(commit=True) as cursor:
        review_id = _ensure_review(cursor, client_id, period_id, account_id)
        open_notes = cursor.execute(
            "SELECT COUNT(*) n FROM account_review_notes WHERE review_id = ? "
            "AND status = 'open'", (review_id,),
        ).fetchone()["n"]
        if open_notes:
            raise ValueError("Resolve the open review notes before signing off.")
        explanation = cursor.execute(
            "SELECT explanation FROM account_close_reviews WHERE id = ?",
            (review_id,),
        ).fetchone()["explanation"]
        if not (explanation or "").strip():
            raise ValueError("Explain the balance and its prior-year change before signing off.")
        evidence_count = cursor.execute(
            "SELECT COUNT(*) n FROM account_close_evidence WHERE review_id = ?",
            (review_id,),
        ).fetchone()["n"]
        if not evidence_count:
            raise ValueError("Add current-year evidence before signing off.")
        current_hash, snapshot = _fingerprint_with_cursor(
            cursor, client_id, period_id, account_id
        )
        if role == "reviewer":
            prepared = cursor.execute(
                "SELECT content_fingerprint FROM account_close_signoffs "
                "WHERE review_id = ? AND role = 'preparer' ORDER BY id DESC LIMIT 1",
                (review_id,),
            ).fetchone()
            if not prepared or prepared["content_fingerprint"] != current_hash:
                raise ValueError("A current preparer signoff is required first.")
        cursor.execute(
            "INSERT INTO account_close_signoffs "
            "(client_id, review_id, role, content_fingerprint, balance_cents, "
            "snapshot_json, signed_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (client_id, review_id, role, current_hash, snapshot["balance_cents"],
             json.dumps(snapshot, sort_keys=True), actor),
        )
        signoff_id = cursor.lastrowid
        AuditLog.write(cursor, client_id, "account_close_signoffs", signoff_id, "REVIEW",
                       new_values={"review_id": review_id, "role": role,
                                   "fingerprint": current_hash,
                                   "balance": to_dollars(snapshot["balance_cents"])})
    return signoff_id


def _latest_signoffs(cursor, review_id: Optional[int]) -> dict:
    found = {}
    if review_id:
        for row in cursor.execute(
            "SELECT * FROM account_close_signoffs WHERE review_id = ? ORDER BY id DESC",
            (review_id,),
        ).fetchall():
            found.setdefault(row["role"], row)
    return found


def _readiness_with_cursor(cursor, client_id: int, period_id: int) -> dict:
    """Build the period Close Map using one caller-owned connection."""
    period = _period(cursor, client_id, period_id)
    prior_end = prior_year_date(date.fromisoformat(period["end_date"])).isoformat()
    prior_start = prior_year_date(date.fromisoformat(period["start_date"])).isoformat()
    # Balance-sheet accounts carry life-to-date balances through each period
    # end. Revenue and expense accounts report the fiscal period's ordinary
    # activity instead — the same figure the income statement publishes
    # (docs/EARNINGS-ATTRIBUTION.md) — never a life-to-date accumulation.
    accounts = cursor.execute(
        "SELECT a.*, COALESCE(SUM(CASE WHEN je.entry_date <= ? "
        "THEN jel.debit - jel.credit ELSE 0 END), 0) current_raw, "
        "COALESCE(SUM(CASE WHEN je.entry_date <= ? "
        "THEN jel.debit - jel.credit ELSE 0 END), 0) prior_raw, "
        "COALESCE(SUM(CASE WHEN je.entry_date >= ? AND je.entry_date <= ? "
        "AND je.entry_type NOT IN ('Beginning Balance', 'Closing') "
        "THEN jel.debit - jel.credit ELSE 0 END), 0) current_period_raw, "
        "COALESCE(SUM(CASE WHEN je.entry_date >= ? AND je.entry_date <= ? "
        "AND je.entry_type NOT IN ('Beginning Balance', 'Closing') "
        "THEN jel.debit - jel.credit ELSE 0 END), 0) prior_period_raw "
        "FROM accounts a LEFT JOIN journal_entry_lines jel ON jel.account_id = a.id "
        "LEFT JOIN journal_entries je ON je.id = jel.journal_entry_id "
        "WHERE a.client_id = ? GROUP BY a.id ORDER BY a.account_number",
        (period["end_date"], prior_end,
         period["start_date"], period["end_date"],
         prior_start, prior_end, client_id),
    ).fetchall()
    rows = []
    for account in accounts:
        if account["type"] in ("Revenue", "Expense"):
            current_cents = _normal_balance(
                account["type"], int(account["current_period_raw"] or 0))
            prior_cents = _normal_balance(
                account["type"], int(account["prior_period_raw"] or 0))
        else:
            current_cents = _normal_balance(account["type"], int(account["current_raw"] or 0))
            prior_cents = _normal_balance(account["type"], int(account["prior_raw"] or 0))
        if current_cents == 0 and prior_cents == 0:
            continue
        mapping = cursor.execute(
            "SELECT m.*, g.code group_code, g.name group_name "
            "FROM account_close_mappings m LEFT JOIN lead_sheet_groups g "
            "ON g.id = m.lead_sheet_group_id WHERE m.client_id = ? AND m.account_id = ?",
            (client_id, account["id"]),
        ).fetchone()
        review = cursor.execute(
            "SELECT * FROM account_close_reviews WHERE client_id = ? "
            "AND fiscal_period_id = ? AND account_id = ?",
            (client_id, period_id, account["id"]),
        ).fetchone()
        review_id = review["id"] if review else None
        evidence_rows = (cursor.execute(
            "SELECT evidence_type, reference FROM account_close_evidence "
            "WHERE review_id = ? ORDER BY id", (review_id,),
        ).fetchall() if review_id else [])
        open_note_rows = (cursor.execute(
            "SELECT body FROM account_review_notes WHERE review_id = ? "
            "AND status = 'open' ORDER BY id", (review_id,),
        ).fetchall() if review_id else [])
        evidence_count = len(evidence_rows)
        open_notes = len(open_note_rows)
        proposals = cursor.execute(
            "SELECT COUNT(*) n FROM close_review_proposals WHERE client_id = ? "
            "AND fiscal_period_id = ? AND account_id = ? AND status = 'pending'",
            (client_id, period_id, account["id"]),
        ).fetchone()["n"]
        required = not mapping or mapping["review_requirement"] == "required"
        signoffs = _latest_signoffs(cursor, review_id)
        prep, reviewer = signoffs.get("preparer"), signoffs.get("reviewer")
        # Unsigned accounts do not need an expensive ledger fingerprint just
        # to say "Not started". Fingerprints exist to prove whether a recorded
        # signature is still current, so calculate one only when there is a
        # signature to compare.
        current_hash = None
        if required and not open_notes and (prep or reviewer):
            current_hash, _ = _fingerprint_with_cursor(
                cursor, client_id, period_id, account["id"]
            )
        if not required:
            status = NOT_REQUIRED
        elif open_notes:
            status = EXCEPTION
        elif reviewer and reviewer["content_fingerprint"] == current_hash:
            status = REVIEWED
        elif prep and prep["content_fingerprint"] == current_hash:
            status = PREPARED
        elif prep or reviewer:
            status = CHANGED
        elif review or proposals:
            status = IN_PROGRESS
        else:
            status = NOT_STARTED
        current, prior = to_dollars(current_cents), to_dollars(prior_cents)
        change = round(current - prior, 2)
        rows.append(CloseMapRow(
            account_id=account["id"], account_number=account["account_number"],
            account_name=account["name"], account_type=account["type"],
            current_balance=current, prior_balance=prior, change=change,
            change_percent=(None if prior == 0 else round(change / abs(prior) * 100, 2)),
            group_id=mapping["lead_sheet_group_id"] if mapping else None,
            group_code=(mapping["group_code"] or "") if mapping else "",
            group_name=(mapping["group_name"] or "") if mapping else "",
            required=required,
            exclusion_reason=(mapping["exclusion_reason"] or "") if mapping else "",
            review_id=review_id, explanation=(review["explanation"] or "") if review else "",
            evidence_count=int(evidence_count),
            evidence_references="; ".join(
                f"{item['reference']} ({item['evidence_type']})" for item in evidence_rows
            ),
            open_note_count=int(open_notes),
            open_notes="; ".join(item["body"] for item in open_note_rows),
            pending_proposal_count=int(proposals), status=status,
            prepared_by=prep["signed_by"] if prep else "",
            prepared_at=prep["signed_at"] if prep else "",
            reviewed_by=reviewer["signed_by"] if reviewer else "",
            reviewed_at=reviewer["signed_at"] if reviewer else "",
        ))
    statuses = (NOT_STARTED, IN_PROGRESS, PREPARED, REVIEWED, CHANGED,
                EXCEPTION, NOT_REQUIRED)
    counts = {status: sum(row.status == status for row in rows) for status in statuses}
    required_rows = [row for row in rows if row.required]
    incomplete = [row for row in required_rows if row.status != REVIEWED]
    return {
        "client_id": client_id,
        "period_id": period_id,
        "period_start": period["start_date"],
        "period_end": period["end_date"],
        "rows": rows,
        "counts": counts,
        "required_count": len(required_rows),
        "reviewed_count": sum(row.status == REVIEWED for row in required_rows),
        "incomplete_count": len(incomplete),
        "ready": not incomplete,
    }


def readiness(client_id: int, period_id: int) -> dict:
    with get_cursor() as cursor:
        return _readiness_with_cursor(cursor, client_id, period_id)


def _prior_year_context_with_cursor(cursor, client_id: int, period,
                                    account_id: int) -> Optional[dict]:
    """Return the immediately preceding year's work as read-only context.

    Review work remains period-specific: this deliberately does not create a
    current-year review, copy evidence, or copy signoffs. The adjacent-period
    check also prevents an old review from silently jumping across a missing
    fiscal year.
    """
    previous_end = (
        date.fromisoformat(period["start_date"]) - timedelta(days=1)
    ).isoformat()
    previous_period = cursor.execute(
        "SELECT * FROM fiscal_periods WHERE client_id = ? "
        "AND period_type = 'Year' AND end_date = ? "
        "ORDER BY start_date DESC, id DESC LIMIT 1",
        (client_id, previous_end),
    ).fetchone()
    if not previous_period:
        return None

    review = cursor.execute(
        "SELECT * FROM account_close_reviews WHERE client_id = ? "
        "AND fiscal_period_id = ? AND account_id = ?",
        (client_id, previous_period["id"], account_id),
    ).fetchone()
    evidence, notes, signoffs = [], [], {}
    if review:
        evidence = [dict(item) for item in cursor.execute(
            "SELECT * FROM account_close_evidence WHERE review_id = ? ORDER BY id",
            (review["id"],),
        ).fetchall()]
        notes = [dict(item) for item in cursor.execute(
            "SELECT * FROM account_review_notes WHERE review_id = ? ORDER BY id",
            (review["id"],),
        ).fetchall()]
        signoffs = _latest_signoffs(cursor, review["id"])

    preparer = signoffs.get("preparer")
    reviewer = signoffs.get("reviewer")
    return {
        "period_id": previous_period["id"],
        "period_name": previous_period["period_name"],
        "period_start": previous_period["start_date"],
        "period_end": previous_period["end_date"],
        "had_review": bool(review),
        "explanation": (review["explanation"] or "") if review else "",
        "evidence": evidence,
        "notes": notes,
        "prepared_by": preparer["signed_by"] if preparer else "",
        "prepared_at": preparer["signed_at"] if preparer else "",
        "reviewed_by": reviewer["signed_by"] if reviewer else "",
        "reviewed_at": reviewer["signed_at"] if reviewer else "",
    }


def account_detail(client_id: int, period_id: int, account_id: int) -> dict:
    with get_cursor() as cursor:
        summary = _readiness_with_cursor(cursor, client_id, period_id)
        row = next((item for item in summary["rows"] if item.account_id == account_id), None)
        if not row:
            raise ValueError("This account has no current or prior-year balance.")
        evidence = ([dict(item) for item in cursor.execute(
            "SELECT * FROM account_close_evidence WHERE review_id = ? ORDER BY id",
            (row.review_id,),
        ).fetchall()] if row.review_id else [])
        notes = ([dict(item) for item in cursor.execute(
            "SELECT * FROM account_review_notes WHERE review_id = ? ORDER BY id",
            (row.review_id,),
        ).fetchall()] if row.review_id else [])
        proposals = [dict(item) for item in cursor.execute(
            "SELECT * FROM close_review_proposals WHERE client_id = ? "
            "AND fiscal_period_id = ? AND account_id = ? AND status = 'pending' "
            "ORDER BY id", (client_id, period_id, account_id),
        ).fetchall()]
        reconciliations = [dict(item) for item in cursor.execute(
            "SELECT id, statement_start_date, statement_end_date, "
            "statement_ending_balance, status, completed_at FROM bank_reconciliations "
            "WHERE client_id = ? AND account_id = ? AND statement_end_date <= ? "
            "ORDER BY statement_end_date DESC", (client_id, account_id, summary["period_end"]),
        ).fetchall()]
        _, snapshot = _fingerprint_with_cursor(cursor, client_id, period_id, account_id)
        period = _period(cursor, client_id, period_id)
        prior_year_context = _prior_year_context_with_cursor(
            cursor, client_id, period, account_id
        )
    return {"row": row, "evidence": evidence, "notes": notes,
            "proposals": proposals, "reconciliations": reconciliations,
            "snapshot": snapshot, "prior_year_context": prior_year_context}


def propose_explanation(client_id: int, period_id: int, account_id: int,
                        explanation: str, rationale: str = "") -> int:
    explanation = (explanation or "").strip()
    if not explanation:
        raise ValueError("A proposed explanation is required.")
    with get_cursor(commit=True) as cursor:
        _period(cursor, client_id, period_id)
        _account(cursor, client_id, account_id)
        cursor.execute(
            "INSERT INTO close_review_proposals "
            "(client_id, fiscal_period_id, account_id, explanation, rationale, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (client_id, period_id, account_id, explanation,
             (rationale or "").strip(), current_actor()),
        )
        proposal_id = cursor.lastrowid
        AuditLog.write(cursor, client_id, "close_review_proposals", proposal_id, "INSERT",
                       new_values={"period_id": period_id, "account_id": account_id,
                                   "explanation": explanation})
    return proposal_id


def resolve_proposal(client_id: int, proposal_id: int, accept: bool) -> None:
    with get_cursor(commit=True) as cursor:
        proposal = cursor.execute(
            "SELECT * FROM close_review_proposals WHERE id = ? AND client_id = ? "
            "AND status = 'pending'", (proposal_id, client_id),
        ).fetchone()
        if not proposal:
            raise ValueError("Pending proposal not found.")
        if accept:
            review_id = _ensure_review(cursor, client_id, proposal["fiscal_period_id"],
                                       proposal["account_id"])
            old = cursor.execute(
                "SELECT explanation FROM account_close_reviews WHERE id = ?",
                (review_id,),
            ).fetchone()["explanation"]
            cursor.execute(
                "UPDATE account_close_reviews SET explanation = ?, updated_by = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (proposal["explanation"], current_actor(), review_id),
            )
            AuditLog.write(cursor, client_id, "account_close_reviews", review_id, "UPDATE",
                           old_values={"explanation": old},
                           new_values={"explanation": proposal["explanation"],
                                       "source_proposal_id": proposal_id})
        status = "accepted" if accept else "dismissed"
        cursor.execute(
            "UPDATE close_review_proposals SET status = ?, resolved_by = ?, "
            "resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, current_actor(), proposal_id),
        )
        AuditLog.write(cursor, client_id, "close_review_proposals", proposal_id, "UPDATE",
                       old_values={"status": "pending"}, new_values={"status": status})
