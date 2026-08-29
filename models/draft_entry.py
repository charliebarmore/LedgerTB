"""Draft journal entries: assistant proposals awaiting human review.

A draft is NOT a journal entry. It lives in its own table (the only table the
MCP server's connections may write), stores its lines as JSON in integer
cents, and touches the ledger exactly once — when a person approves it in the
app, which posts a real journal entry through the normal model (validation,
audit trail, actor stamping) and links the draft to what it became.
"""
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from constants import EntryType
from database.connection import get_connection, get_cursor
from money import to_dollars


@dataclass
class DraftLine:
    account_number: str
    debit_cents: int = 0
    credit_cents: int = 0
    memo: str = ""


@dataclass
class DraftEntry:
    id: Optional[int] = None
    client_id: int = 0
    proposed_by: str = "Assistant"
    entry_date: str = ""          # ISO date
    entry_type: str = "Regular"
    description: str = ""
    rationale: str = ""
    lines: List[DraftLine] = field(default_factory=list)
    original_entry_id: Optional[int] = None
    status: str = "pending"
    posted_entry_id: Optional[int] = None
    proposed_at: str = ""
    resolved_at: str = ""
    resolved_by: str = ""

    def _audit_values(self) -> dict:
        return {
            "proposed_by": self.proposed_by,
            "proposed_at": self.proposed_at,
            "entry_date": self.entry_date,
            "entry_type": self.entry_type,
            "description": self.description,
            "rationale": self.rationale,
            "lines": [line.__dict__ for line in self.lines],
            "original_entry_id": self.original_entry_id,
            "status": self.status,
            "resolved_at": self.resolved_at or None,
            "resolved_by": self.resolved_by or None,
            "posted_entry_id": self.posted_entry_id,
        }

    # ---------------------------------------------------------------- checks
    def validate(self, conn=None) -> None:
        if not self.description.strip():
            raise ValueError("A draft needs a description.")
        try:
            datetime.strptime(self.entry_date, "%Y-%m-%d")
        except (TypeError, ValueError):
            raise ValueError("entry_date must be an ISO date (YYYY-MM-DD).")
        if self.entry_type not in EntryType.ALL:
            raise ValueError(
                "entry_type must be one of: " + ", ".join(EntryType.ALL) + ".")
        owns_conn = conn is None
        if owns_conn:
            conn = get_connection()
        try:
            cursor = conn.cursor()
            if self.original_entry_id is not None:
                cursor.execute(
                    "SELECT id FROM journal_entries WHERE id = ? AND client_id = ?",
                    (self.original_entry_id, self.client_id),
                )
                if not cursor.fetchone():
                    raise ValueError(
                        "The original journal entry must belong to the selected client."
                    )
            if len(self.lines) < 2:
                raise ValueError("A draft needs at least two lines.")
            debits = credits = 0
            cursor.execute(
                "SELECT account_number FROM accounts WHERE client_id = ?",
                (self.client_id,),
            )
            numbers = {row["account_number"] for row in cursor.fetchall()}
            for line in self.lines:
                if line.debit_cents < 0 or line.credit_cents < 0:
                    raise ValueError("Line amounts cannot be negative.")
                if bool(line.debit_cents) == bool(line.credit_cents):
                    raise ValueError("Each line needs a debit or a credit, not both.")
                if str(line.account_number) not in numbers:
                    raise ValueError(f"No account numbered {line.account_number} for this client.")
                debits += line.debit_cents
                credits += line.credit_cents
            if debits != credits or debits == 0:
                raise ValueError(
                    f"Draft does not balance: debits {to_dollars(debits):,.2f} vs "
                    f"credits {to_dollars(credits):,.2f}."
                )
        finally:
            if owns_conn:
                conn.close()

    # ---------------------------------------------------------------- io
    def save(self, conn=None) -> int:
        from models.audit_log import AuditLog

        self.validate(conn=conn)
        owns_conn = conn is None
        if owns_conn:
            conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO draft_entries
                   (client_id, proposed_by, entry_date, entry_type,
                    description, rationale, lines_json, original_entry_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (self.client_id, self.proposed_by, self.entry_date,
                 self.entry_type, self.description, self.rationale,
                 json.dumps([line.__dict__ for line in self.lines]),
                 self.original_entry_id),
            )
            self.id = cursor.lastrowid
            cursor.execute(
                "SELECT datetime(proposed_at, 'localtime') proposed_at_local "
                "FROM draft_entries WHERE id = ?",
                (self.id,),
            )
            self.proposed_at = cursor.fetchone()["proposed_at_local"] or ""
            AuditLog.write(
                cursor, self.client_id, "draft_entries", self.id, "INSERT",
                new_values=self._audit_values(),
            )
            if owns_conn:
                conn.commit()
            return self.id
        except Exception:
            if owns_conn:
                conn.rollback()
            raise
        finally:
            if owns_conn:
                conn.close()

    @staticmethod
    def _from_row(row) -> "DraftEntry":
        return DraftEntry(
            id=row["id"], client_id=row["client_id"],
            proposed_by=row["proposed_by"], entry_date=row["entry_date"],
            entry_type=row["entry_type"], description=row["description"],
            rationale=row["rationale"] or "",
            lines=[DraftLine(**l) for l in json.loads(row["lines_json"])],
            original_entry_id=(row["original_entry_id"]
                               if "original_entry_id" in row.keys() else None),
            status=row["status"], posted_entry_id=row["posted_entry_id"],
            proposed_at=(row["proposed_at_local"]
                         if "proposed_at_local" in row.keys()
                         else row["proposed_at"]) or "",
            resolved_at=row["resolved_at"] or "",
            resolved_by=row["resolved_by"] or "",
        )

    @staticmethod
    def get_by_id(draft_id: int, client_id: int) -> Optional["DraftEntry"]:
        with get_cursor() as cursor:
            cursor.execute(
                """SELECT draft_entries.*,
                          datetime(proposed_at, 'localtime') proposed_at_local
                   FROM draft_entries WHERE id = ? AND client_id = ?""",
                (draft_id, client_id))
            row = cursor.fetchone()
        return DraftEntry._from_row(row) if row else None

    @staticmethod
    def get_pending(client_id: int) -> List["DraftEntry"]:
        with get_cursor() as cursor:
            cursor.execute(
                """SELECT draft_entries.*,
                          datetime(proposed_at, 'localtime') proposed_at_local
                   FROM draft_entries WHERE client_id = ? AND status = 'pending'
                   ORDER BY proposed_at, id""", (client_id,))
            rows = cursor.fetchall()
        return [DraftEntry._from_row(r) for r in rows]

    @staticmethod
    def get_resolved(client_id: int, limit: int = 20) -> List["DraftEntry"]:
        """Recently approved or rejected proposals, newest review first."""
        with get_cursor() as cursor:
            cursor.execute(
                """SELECT draft_entries.*,
                          datetime(proposed_at, 'localtime') proposed_at_local
                   FROM draft_entries
                   WHERE client_id = ? AND status != 'pending'
                   ORDER BY resolved_at DESC, id DESC LIMIT ?""",
                (client_id, max(1, int(limit))),
            )
            rows = cursor.fetchall()
        return [DraftEntry._from_row(row) for row in rows]

    @staticmethod
    def pending_count(client_id: int) -> int:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS n FROM draft_entries WHERE client_id = ? "
                "AND status = 'pending'", (client_id,))
            return cursor.fetchone()["n"]

    @staticmethod
    def get_for_originals(client_id: int, entry_ids: List[int]) -> dict:
        """Correction proposals grouped by the original journal entry."""
        if not entry_ids:
            return {}
        placeholders = ", ".join("?" for _ in entry_ids)
        with get_cursor() as cursor:
            cursor.execute(
                f"""SELECT draft_entries.*,
                           datetime(proposed_at, 'localtime') proposed_at_local
                    FROM draft_entries
                    WHERE client_id = ?
                      AND original_entry_id IN ({placeholders})
                    ORDER BY proposed_at, id""",
                [client_id, *entry_ids],
            )
            rows = cursor.fetchall()
        grouped = {}
        for row in rows:
            draft = DraftEntry._from_row(row)
            grouped.setdefault(draft.original_entry_id, []).append(draft)
        return grouped

    # ---------------------------------------------------------------- review
    def approve(self) -> int:
        """Post the draft as a real journal entry (normal validation, audit,
        actor) and mark it approved. Returns the new journal entry id."""
        from models.audit_log import AuditLog
        from models.journal_entry import JournalEntry, JournalEntryLine
        from utils.actor import current_actor

        from datetime import date as _date
        from utils.fiscal_dates import fiscal_year_bounds
        actor = current_actor()
        conn = get_connection()
        cursor = conn.cursor()
        try:
            self.validate(conn=conn)  # accounts may have changed since filing
            # The conditional update is the claim.  It is deliberately the
            # first write in this transaction: concurrent/stale DraftEntry
            # objects cannot both claim the same pending row.
            cursor.execute(
                """UPDATE draft_entries
                   SET status = 'approved',
                       resolved_at = datetime('now', 'localtime'),
                       resolved_by = ?
                   WHERE id = ? AND client_id = ? AND status = 'pending'""",
                (actor, self.id, self.client_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Only a pending draft can be approved.")

            from services.recurring_entries import recurring_draft_context
            recurring = recurring_draft_context(conn, self.id, self.client_id)
            if recurring and recurring["role"] == "Primary":
                source_reference = (
                    f"Recurring · {recurring['template_name']} · "
                    f"{recurring['period_name']} · Draft #{self.id}"
                )
                template_reference = (
                    recurring.get("template_source_reference") or ""
                ).strip()
                if template_reference:
                    source_reference += f" · {template_reference}"
            elif recurring and recurring["role"] == "Reversal":
                primary_entry_id = recurring.get("primary_posted_entry_id")
                if not primary_entry_id:
                    raise ValueError(
                        "The scheduled primary entry must post before its reversal."
                    )
                source_reference = (
                    f"Scheduled reversal of JE #{primary_entry_id} · "
                    f"{recurring['template_name']} · Draft #{self.id}"
                )
            elif self.original_entry_id is not None:
                source_reference = (
                    f"Correction of JE #{self.original_entry_id} · Draft #{self.id} · "
                    f"proposed by {self.proposed_by}"
                )
            else:
                source_reference = f"Draft #{self.id} · proposed by {self.proposed_by}"

            cursor.execute(
                "SELECT account_number, id FROM accounts WHERE client_id = ?",
                (self.client_id,),
            )
            by_number = {row["account_number"]: row["id"] for row in cursor.fetchall()}
            entry_date = _date.fromisoformat(self.entry_date)
            aje_reference = None
            if self.entry_type == "Adjusting":
                cursor.execute(
                    "SELECT fiscal_year_end_month FROM clients WHERE id = ?",
                    (self.client_id,),
                )
                client = cursor.fetchone()
                if not client:
                    raise ValueError("Client not found for this draft.")
                fy_start, fy_end = fiscal_year_bounds(
                    entry_date, client["fiscal_year_end_month"]
                )
                aje_reference = JournalEntry.get_next_aje_reference(
                    self.client_id, fy_start, fy_end, conn=conn
                )

            entry = JournalEntry(
                client_id=self.client_id,
                entry_date=entry_date,
                description=self.description,
                entry_type=self.entry_type,
                source_reference=source_reference,
                aje_reference=aje_reference,
                lines=[JournalEntryLine(
                    account_id=by_number[str(line.account_number)],
                    debit=to_dollars(line.debit_cents),
                    credit=to_dollars(line.credit_cents),
                    memo=line.memo or None,
                ) for line in self.lines],
            )
            entry_id = entry.save(conn=conn)
            cursor.execute(
                """UPDATE draft_entries SET posted_entry_id = ?
                   WHERE id = ? AND client_id = ? AND status = 'approved'""",
                (entry_id, self.id, self.client_id),
            )
            cursor.execute(
                """SELECT resolved_at, resolved_by FROM draft_entries
                   WHERE id = ? AND client_id = ?""",
                (self.id, self.client_id),
            )
            resolved = cursor.fetchone()

            old_values = self._audit_values()
            new_values = dict(old_values)
            new_values.update({
                "status": "approved",
                "resolved_at": resolved["resolved_at"],
                "resolved_by": resolved["resolved_by"],
                "posted_entry_id": entry_id,
            })
            AuditLog.write(
                cursor, self.client_id, "draft_entries", self.id, "UPDATE",
                old_values=old_values, new_values=new_values,
            )
            if recurring and recurring["role"] == "Primary":
                from services.recurring_entries import (
                    create_reversal_after_primary_approval,
                )
                create_reversal_after_primary_approval(
                    conn, self, entry_id, context=recurring
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        self.status = "approved"
        self.posted_entry_id = entry_id
        self.resolved_at = resolved["resolved_at"]
        self.resolved_by = resolved["resolved_by"]
        return entry_id

    def reject(self) -> None:
        from models.audit_log import AuditLog
        from utils.actor import current_actor

        actor = current_actor()
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """UPDATE draft_entries
                   SET status = 'rejected',
                       resolved_at = datetime('now', 'localtime'),
                       resolved_by = ?, posted_entry_id = NULL
                   WHERE id = ? AND client_id = ? AND status = 'pending'""",
                (actor, self.id, self.client_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Only a pending draft can be rejected.")
            cursor.execute(
                """SELECT resolved_at, resolved_by FROM draft_entries
                   WHERE id = ? AND client_id = ?""",
                (self.id, self.client_id),
            )
            resolved = cursor.fetchone()

            old_values = self._audit_values()
            new_values = dict(old_values)
            new_values.update({
                "status": "rejected",
                "resolved_at": resolved["resolved_at"],
                "resolved_by": resolved["resolved_by"],
                "posted_entry_id": None,
            })
            AuditLog.write(
                cursor, self.client_id, "draft_entries", self.id, "UPDATE",
                old_values=old_values, new_values=new_values,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        self.status = "rejected"
        self.posted_entry_id = None
        self.resolved_at = resolved["resolved_at"]
        self.resolved_by = resolved["resolved_by"]
