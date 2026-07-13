import sqlite3
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import date
from database.connection import get_connection, get_cursor
from money import to_cents, to_dollars


@dataclass
class JournalEntryLine:
    id: Optional[int] = None
    journal_entry_id: Optional[int] = None
    account_id: int = 0
    debit: float = 0.0
    credit: float = 0.0
    memo: Optional[str] = None
    account_name: Optional[str] = None  # For display purposes
    account_number: Optional[str] = None  # For display purposes


@dataclass
class JournalEntry:
    id: Optional[int] = None
    client_id: int = 0
    entry_date: date = None
    description: str = ""
    source_reference: Optional[str] = None
    entry_type: str = "Regular"  # Regular, Adjusting, Closing
    aje_reference: Optional[str] = None  # AJE-001, AJE-002, etc. for adjusting entries
    lines: List[JournalEntryLine] = field(default_factory=list)

    def is_balanced(self) -> bool:
        """Check if total debits equal total credits.

        Compared in integer cents so the check is exact -- no floating-point
        tolerance that would let a not-quite-balanced entry (off by < $0.01) pass.
        """
        total_debits = sum(to_cents(line.debit) for line in self.lines)
        total_credits = sum(to_cents(line.credit) for line in self.lines)
        return total_debits == total_credits

    def total_debits(self) -> float:
        return sum(line.debit for line in self.lines)

    def total_credits(self) -> float:
        return sum(line.credit for line in self.lines)

    def validate(self) -> List[str]:
        """Validate the journal entry. Returns list of error messages."""
        errors = []

        if not self.entry_date:
            errors.append("Entry date is required")

        if self.client_id == 0:
            errors.append("Client is required")

        if len(self.lines) < 2:
            errors.append("Journal entry must have at least two lines")

        if not self.is_balanced():
            errors.append(
                f"Entry is not balanced. Debits: ${self.total_debits():,.2f}, "
                f"Credits: ${self.total_credits():,.2f}"
            )

        for i, line in enumerate(self.lines):
            if line.account_id == 0:
                errors.append(f"Line {i+1}: Account is required")
            if line.debit == 0 and line.credit == 0:
                errors.append(f"Line {i+1}: Must have either debit or credit")
            if line.debit > 0 and line.credit > 0:
                errors.append(f"Line {i+1}: Cannot have both debit and credit")

        return errors

    def save(self, conn=None) -> int:
        """Save the journal entry and its lines.

        If ``conn`` is provided, this method participates in the caller's
        transaction: it uses that connection and does NOT commit, close, or
        write its own audit-log row (the caller — e.g. services.posting —
        coordinates the shared transaction and its audit logging). When ``conn``
        is omitted it manages its own connection exactly as before.
        """
        from models.audit_log import AuditLog

        errors = self.validate()
        if errors:
            raise ValueError("; ".join(errors))

        # Block posting/editing entries dated within a closed fiscal year
        from models.fiscal_period import FiscalPeriod
        closed = FiscalPeriod.get_closed_period_for_date(self.client_id, self.entry_date)
        if closed:
            raise ValueError(
                f"{closed.period_name} is closed. Reopen the year before posting or "
                f"editing entries dated {self.entry_date.isoformat()}."
            )

        owns_conn = conn is None
        if owns_conn:
            conn = get_connection()
        cursor = conn.cursor()

        is_new = self.id is None
        old_values = None

        try:
            # SQLite foreign keys ensure referenced accounts exist, but cannot
            # express that every line belongs to the same client as the entry.
            # Enforce that tenant boundary before inserting or replacing lines.
            account_ids = {line.account_id for line in self.lines}
            placeholders = ", ".join("?" for _ in account_ids)
            cursor.execute(
                f"SELECT id FROM accounts WHERE client_id = ? AND id IN ({placeholders})",
                [self.client_id, *account_ids],
            )
            owned_account_ids = {row["id"] for row in cursor.fetchall()}
            if owned_account_ids != account_ids:
                raise ValueError("Every journal entry account must belong to the selected client.")

            if is_new:
                # Insert new entry
                cursor.execute(
                    """
                    INSERT INTO journal_entries (client_id, entry_date, description, source_reference, entry_type, aje_reference)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (self.client_id, self.entry_date.isoformat(), self.description,
                     self.source_reference, self.entry_type, self.aje_reference)
                )
                self.id = cursor.lastrowid
            else:
                # Get old values for audit log
                cursor.execute(
                    "SELECT * FROM journal_entries WHERE id = ? AND client_id = ?",
                    (self.id, self.client_id),
                )
                old_row = cursor.fetchone()
                if not old_row:
                    raise ValueError("Journal entry not found for the selected client.")
                old_entry_date = date.fromisoformat(old_row['entry_date'])
                old_closed = FiscalPeriod.get_closed_period_for_date(
                    self.client_id, old_entry_date
                )
                if old_closed:
                    raise ValueError(
                        f"{old_closed.period_name} is closed. Reopen the year before "
                        f"editing entries dated {old_entry_date.isoformat()}."
                    )
                old_values = {
                    'entry_date': old_row['entry_date'],
                    'description': old_row['description'],
                    'source_reference': old_row['source_reference'],
                    'entry_type': old_row['entry_type'],
                    'aje_reference': old_row['aje_reference'] if 'aje_reference' in old_row.keys() else None
                }

                cursor.execute(
                    """
                    SELECT 1
                    FROM bank_reconciliation_items bri
                    JOIN journal_entry_lines jel ON jel.id = bri.journal_entry_line_id
                    WHERE jel.journal_entry_id = ?
                    LIMIT 1
                    """,
                    (self.id,),
                )
                if cursor.fetchone():
                    raise ValueError(
                        "This entry is selected in a bank reconciliation. "
                        "Unselect it (or reopen the completed reconciliation) before editing."
                    )

                # Update existing entry
                cursor.execute(
                    """
                    UPDATE journal_entries
                    SET entry_date = ?, description = ?, source_reference = ?, entry_type = ?, aje_reference = ?
                    WHERE id = ? AND client_id = ?
                    """,
                    (self.entry_date.isoformat(), self.description, self.source_reference,
                     self.entry_type, self.aje_reference, self.id, self.client_id)
                )
                # Delete existing lines
                cursor.execute("DELETE FROM journal_entry_lines WHERE journal_entry_id = ?", (self.id,))

            # Insert lines
            for line in self.lines:
                cursor.execute(
                    """
                    INSERT INTO journal_entry_lines (journal_entry_id, account_id, debit, credit, memo)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (self.id, line.account_id, to_cents(line.debit), to_cents(line.credit), line.memo)
                )
                line.id = cursor.lastrowid
                line.journal_entry_id = self.id

            if owns_conn:
                conn.commit()

                # Log to audit trail. Only when this method owns the transaction;
                # a caller that passes `conn` coordinates the shared transaction
                # and is responsible for its own audit logging.
                new_values = {
                    'entry_date': self.entry_date.isoformat(),
                    'description': self.description,
                    'source_reference': self.source_reference,
                    'entry_type': self.entry_type,
                    'aje_reference': self.aje_reference,
                    'total_debits': self.total_debits(),
                    'total_credits': self.total_credits()
                }

                AuditLog.log_change_safe(
                    client_id=self.client_id,
                    table_name='journal_entries',
                    record_id=self.id,
                    action='INSERT' if is_new else 'UPDATE',
                    old_values=None if is_new else old_values,
                    new_values=new_values,
                )

        except Exception as e:
            if owns_conn:
                conn.rollback()
            raise e
        finally:
            if owns_conn:
                conn.close()

        return self.id

    @staticmethod
    def _entry_from_row(row) -> 'JournalEntry':
        """Build a JournalEntry (header only) from a DB row."""
        return JournalEntry(
            id=row['id'],
            client_id=row['client_id'],
            entry_date=date.fromisoformat(row['entry_date']),
            description=row['description'],
            source_reference=row['source_reference'],
            entry_type=row['entry_type'],
            aje_reference=row['aje_reference'] if 'aje_reference' in row.keys() else None
        )

    @staticmethod
    def _line_from_row(row) -> 'JournalEntryLine':
        """Build a JournalEntryLine from a lines-query row (jel.* + account info)."""
        return JournalEntryLine(
            id=row['id'],
            journal_entry_id=row['journal_entry_id'],
            account_id=row['account_id'],
            debit=to_dollars(row['debit']),
            credit=to_dollars(row['credit']),
            memo=row['memo'],
            account_name=row['account_name'],
            account_number=row['account_number']
        )

    _LINES_SQL = """
        SELECT jel.*, a.name as account_name, a.account_number
        FROM journal_entry_lines jel
        JOIN accounts a ON jel.account_id = a.id
        WHERE jel.journal_entry_id = ?
        ORDER BY jel.id
    """

    @staticmethod
    def get_by_id(entry_id: int, client_id: Optional[int] = None) -> Optional['JournalEntry']:
        """Get a journal entry with its lines.

        If ``client_id`` is given, the entry is returned only when it belongs to
        that client -- defense-in-depth for id-based lookups (e.g. the entry
        search box). Returns None on a cross-client mismatch.
        """
        with get_cursor() as cursor:
            if client_id is None:
                cursor.execute("SELECT * FROM journal_entries WHERE id = ?", (entry_id,))
            else:
                cursor.execute(
                    "SELECT * FROM journal_entries WHERE id = ? AND client_id = ?",
                    (entry_id, client_id)
                )
            row = cursor.fetchone()
            if not row:
                return None

            entry = JournalEntry._entry_from_row(row)
            cursor.execute(JournalEntry._LINES_SQL, (entry_id,))
            entry.lines = [JournalEntry._line_from_row(r) for r in cursor.fetchall()]
        return entry

    @staticmethod
    def count(client_id: int) -> int:
        """Count all journal entries for a client (cheap; no object hydration)."""
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM journal_entries WHERE client_id = ?",
                (client_id,)
            )
            return cursor.fetchone()[0]

    @staticmethod
    def get_all(
        client_id: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        entry_type: Optional[str] = None,
        limit: int = 100
    ) -> List['JournalEntry']:
        """Get journal entries for a client with optional filters."""
        with get_cursor() as cursor:
            query = "SELECT * FROM journal_entries WHERE client_id = ?"
            params = [client_id]

            if start_date:
                query += " AND entry_date >= ?"
                params.append(start_date.isoformat())

            if end_date:
                query += " AND entry_date <= ?"
                params.append(end_date.isoformat())

            if entry_type:
                query += " AND entry_type = ?"
                params.append(entry_type)

            query += " ORDER BY entry_date DESC, id DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()

            entries = []
            for row in rows:
                entry = JournalEntry._entry_from_row(row)
                cursor.execute(JournalEntry._LINES_SQL, (entry.id,))
                entry.lines = [JournalEntry._line_from_row(r) for r in cursor.fetchall()]
                entries.append(entry)
        return entries

    @staticmethod
    def delete(entry_id: int, client_id: Optional[int] = None):
        """Delete a journal entry and its lines.

        If ``client_id`` is given, only an entry belonging to that client is
        deleted; a cross-client id is a no-op (the row is treated as not found).
        """
        from models.audit_log import AuditLog

        conn = get_connection()
        try:
            cursor = conn.cursor()

            # Get the entry info for audit logging (scoped to the client when given)
            if client_id is None:
                cursor.execute("SELECT * FROM journal_entries WHERE id = ?", (entry_id,))
            else:
                cursor.execute(
                    "SELECT * FROM journal_entries WHERE id = ? AND client_id = ?",
                    (entry_id, client_id)
                )
            row = cursor.fetchone()

            if row:
                old_values = {
                    'entry_date': row['entry_date'],
                    'description': row['description'],
                    'source_reference': row['source_reference'],
                    'entry_type': row['entry_type'],
                    'aje_reference': row['aje_reference'] if 'aje_reference' in row.keys() else None
                }
                client_id = row['client_id']

                # Block deleting entries dated within a closed fiscal year
                from models.fiscal_period import FiscalPeriod
                entry_date = date.fromisoformat(row['entry_date'])
                closed = FiscalPeriod.get_closed_period_for_date(client_id, entry_date)
                if closed:
                    raise ValueError(
                        f"{closed.period_name} is closed. Reopen the year before deleting "
                        f"entries dated {entry_date.isoformat()}."
                    )

                cursor.execute(
                    """
                    SELECT 1
                    FROM bank_reconciliation_items bri
                    JOIN journal_entry_lines jel ON jel.id = bri.journal_entry_line_id
                    WHERE jel.journal_entry_id = ?
                    LIMIT 1
                    """,
                    (entry_id,),
                )
                if cursor.fetchone():
                    raise ValueError(
                        "This entry is selected in a bank reconciliation. "
                        "Unselect it (or reopen the completed reconciliation) before deleting."
                    )

                # Unlink any imported transactions that reference this entry first.
                # imported_transactions.journal_entry_id is a RESTRICT foreign key
                # (no ON DELETE clause) and PRAGMA foreign_keys is ON, so deleting an
                # import-posted entry without this would raise IntegrityError. This
                # mirrors ON DELETE SET NULL semantics at the application layer.
                cursor.execute(
                    "UPDATE imported_transactions SET journal_entry_id = NULL "
                    "WHERE journal_entry_id = ?",
                    (entry_id,)
                )

                cursor.execute("DELETE FROM journal_entries WHERE id = ?", (entry_id,))
                conn.commit()

                # Log to audit trail (best-effort; logged if it fails)
                AuditLog.log_change_safe(
                    client_id=client_id,
                    table_name='journal_entries',
                    record_id=entry_id,
                    action='DELETE',
                    old_values=old_values
                )
        finally:
            conn.close()

    @staticmethod
    def get_next_aje_reference(client_id: int, period_start: date, period_end: date) -> str:
        """
        Generate the next AJE reference number for a client/period.
        Format: AJE-001, AJE-002, etc.

        Args:
            client_id: The client ID
            period_start: Start of the period
            period_end: End of the period

        Returns:
            Next available AJE reference (e.g., "AJE-001")
        """
        with get_cursor() as cursor:
            cursor.execute("""
                SELECT aje_reference FROM journal_entries
                WHERE client_id = ?
                  AND entry_type = 'Adjusting'
                  AND entry_date >= ? AND entry_date <= ?
                  AND aje_reference IS NOT NULL
                ORDER BY aje_reference DESC
                LIMIT 1
            """, (client_id, period_start.isoformat(), period_end.isoformat()))
            row = cursor.fetchone()

        if row and row['aje_reference']:
            # Extract number from AJE-XXX format
            try:
                current_num = int(row['aje_reference'].split('-')[1])
                return f"AJE-{current_num + 1:03d}"
            except (IndexError, ValueError):
                pass

        return "AJE-001"

    @staticmethod
    def find_potential_duplicates(
        client_id: int,
        entry_date: date,
        amount: float,
        description: str = None,
        bank_account_id: int = None
    ) -> List[dict]:
        """
        Find potential duplicate transactions based on date, amount, and optionally description.

        Returns list of dicts with matching journal entry info.
        """
        # Look for journal entries on the same date with matching amount.
        # Amounts are stored as integer cents, so match exactly on cents.
        abs_cents = to_cents(abs(amount))

        query = """
            SELECT DISTINCT je.id, je.entry_date, je.description, je.source_reference,
                   jel.debit, jel.credit, jel.memo
            FROM journal_entries je
            JOIN journal_entry_lines jel ON je.id = jel.journal_entry_id
            WHERE je.client_id = ?
              AND je.entry_date = ?
              AND (jel.debit = ? OR jel.credit = ?)
        """
        params = [client_id, entry_date.isoformat(), abs_cents, abs_cents]

        # Optionally filter by bank account
        if bank_account_id:
            query += " AND jel.account_id = ?"
            params.append(bank_account_id)

        with get_cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        return [{
            'entry_id': row['id'],
            'entry_date': row['entry_date'],
            'description': row['description'],
            'source_reference': row['source_reference'],
            'amount': to_dollars(row['debit'] if row['debit'] > 0 else row['credit']),
            'memo': row['memo']
        } for row in rows]
