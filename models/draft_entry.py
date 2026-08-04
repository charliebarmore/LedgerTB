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

from database.connection import get_cursor
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
    status: str = "pending"
    posted_entry_id: Optional[int] = None
    proposed_at: str = ""

    # ---------------------------------------------------------------- checks
    def validate(self) -> None:
        from models.account import Account

        if not self.description.strip():
            raise ValueError("A draft needs a description.")
        try:
            datetime.strptime(self.entry_date, "%Y-%m-%d")
        except (TypeError, ValueError):
            raise ValueError("entry_date must be an ISO date (YYYY-MM-DD).")
        if self.entry_type not in ("Regular", "Adjusting"):
            raise ValueError("entry_type must be Regular or Adjusting.")
        if len(self.lines) < 2:
            raise ValueError("A draft needs at least two lines.")
        debits = credits = 0
        numbers = {a.account_number for a in Account.get_all(self.client_id, active_only=False)}
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

    # ---------------------------------------------------------------- io
    def save(self) -> int:
        self.validate()
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                """INSERT INTO draft_entries
                   (client_id, proposed_by, entry_date, entry_type,
                    description, rationale, lines_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (self.client_id, self.proposed_by, self.entry_date,
                 self.entry_type, self.description, self.rationale,
                 json.dumps([line.__dict__ for line in self.lines])),
            )
            self.id = cursor.lastrowid
        return self.id

    @staticmethod
    def _from_row(row) -> "DraftEntry":
        return DraftEntry(
            id=row["id"], client_id=row["client_id"],
            proposed_by=row["proposed_by"], entry_date=row["entry_date"],
            entry_type=row["entry_type"], description=row["description"],
            rationale=row["rationale"] or "",
            lines=[DraftLine(**l) for l in json.loads(row["lines_json"])],
            status=row["status"], posted_entry_id=row["posted_entry_id"],
            proposed_at=row["proposed_at"] or "",
        )

    @staticmethod
    def get_by_id(draft_id: int, client_id: int) -> Optional["DraftEntry"]:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM draft_entries WHERE id = ? AND client_id = ?",
                (draft_id, client_id))
            row = cursor.fetchone()
        return DraftEntry._from_row(row) if row else None

    @staticmethod
    def get_pending(client_id: int) -> List["DraftEntry"]:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM draft_entries WHERE client_id = ? AND "
                "status = 'pending' ORDER BY proposed_at, id", (client_id,))
            rows = cursor.fetchall()
        return [DraftEntry._from_row(r) for r in rows]

    @staticmethod
    def pending_count(client_id: int) -> int:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS n FROM draft_entries WHERE client_id = ? "
                "AND status = 'pending'", (client_id,))
            return cursor.fetchone()["n"]

    # ---------------------------------------------------------------- review
    def approve(self) -> int:
        """Post the draft as a real journal entry (normal validation, audit,
        actor) and mark it approved. Returns the new journal entry id."""
        from models.account import Account
        from models.journal_entry import JournalEntry, JournalEntryLine
        from utils.actor import current_actor

        if self.status != "pending":
            raise ValueError("Only a pending draft can be approved.")
        self.validate()  # accounts may have changed since it was filed
        by_number = {a.account_number: a.id
                     for a in Account.get_all(self.client_id, active_only=False)}
        from datetime import date as _date

        entry = JournalEntry(
            client_id=self.client_id,
            entry_date=_date.fromisoformat(self.entry_date),
            description=self.description,
            entry_type=self.entry_type,
            source_reference=f"Draft #{self.id} · proposed by {self.proposed_by}",
            lines=[JournalEntryLine(
                account_id=by_number[str(l.account_number)],
                debit=to_dollars(l.debit_cents),
                credit=to_dollars(l.credit_cents),
                memo=l.memo or None,
            ) for l in self.lines],
        )
        entry_id = entry.save()
        self._resolve("approved", current_actor(), entry_id)
        return entry_id

    def reject(self) -> None:
        from utils.actor import current_actor

        if self.status != "pending":
            raise ValueError("Only a pending draft can be rejected.")
        self._resolve("rejected", current_actor(), None)

    def _resolve(self, status: str, actor: str, posted_entry_id) -> None:
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                """UPDATE draft_entries
                   SET status = ?, resolved_at = CURRENT_TIMESTAMP,
                       resolved_by = ?, posted_entry_id = ?
                   WHERE id = ? AND client_id = ?""",
                (status, actor, posted_entry_id, self.id, self.client_id))
        self.status = status
        self.posted_entry_id = posted_entry_id
