import sqlite3
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import date
from database.connection import get_connection


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
        """Check if total debits equal total credits."""
        total_debits = sum(line.debit for line in self.lines)
        total_credits = sum(line.credit for line in self.lines)
        return abs(total_debits - total_credits) < 0.01  # Allow for floating point

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

    def save(self) -> int:
        """Save the journal entry and its lines."""
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

        conn = get_connection()
        cursor = conn.cursor()

        is_new = self.id is None
        old_values = None

        try:
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
                cursor.execute("SELECT * FROM journal_entries WHERE id = ?", (self.id,))
                old_row = cursor.fetchone()
                if old_row:
                    old_values = {
                        'entry_date': old_row['entry_date'],
                        'description': old_row['description'],
                        'source_reference': old_row['source_reference'],
                        'entry_type': old_row['entry_type'],
                        'aje_reference': old_row['aje_reference'] if 'aje_reference' in old_row.keys() else None
                    }

                # Update existing entry
                cursor.execute(
                    """
                    UPDATE journal_entries
                    SET entry_date = ?, description = ?, source_reference = ?, entry_type = ?, aje_reference = ?
                    WHERE id = ?
                    """,
                    (self.entry_date.isoformat(), self.description, self.source_reference,
                     self.entry_type, self.aje_reference, self.id)
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
                    (self.id, line.account_id, line.debit, line.credit, line.memo)
                )
                line.id = cursor.lastrowid
                line.journal_entry_id = self.id

            conn.commit()

            # Log to audit trail
            new_values = {
                'entry_date': self.entry_date.isoformat(),
                'description': self.description,
                'source_reference': self.source_reference,
                'entry_type': self.entry_type,
                'aje_reference': self.aje_reference,
                'total_debits': self.total_debits(),
                'total_credits': self.total_credits()
            }

            try:
                if is_new:
                    AuditLog.log_change(
                        client_id=self.client_id,
                        table_name='journal_entries',
                        record_id=self.id,
                        action='INSERT',
                        new_values=new_values
                    )
                else:
                    AuditLog.log_change(
                        client_id=self.client_id,
                        table_name='journal_entries',
                        record_id=self.id,
                        action='UPDATE',
                        old_values=old_values,
                        new_values=new_values
                    )
            except Exception:
                pass  # Don't fail the save if audit logging fails

        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

        return self.id

    @staticmethod
    def get_by_id(entry_id: int) -> Optional['JournalEntry']:
        """Get a journal entry with its lines."""
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM journal_entries WHERE id = ?", (entry_id,))
        row = cursor.fetchone()

        if not row:
            conn.close()
            return None

        entry = JournalEntry(
            id=row['id'],
            client_id=row['client_id'],
            entry_date=date.fromisoformat(row['entry_date']),
            description=row['description'],
            source_reference=row['source_reference'],
            entry_type=row['entry_type'],
            aje_reference=row['aje_reference'] if 'aje_reference' in row.keys() else None
        )

        # Get lines with account info
        cursor.execute(
            """
            SELECT jel.*, a.name as account_name, a.account_number
            FROM journal_entry_lines jel
            JOIN accounts a ON jel.account_id = a.id
            WHERE jel.journal_entry_id = ?
            ORDER BY jel.id
            """,
            (entry_id,)
        )

        for line_row in cursor.fetchall():
            entry.lines.append(JournalEntryLine(
                id=line_row['id'],
                journal_entry_id=line_row['journal_entry_id'],
                account_id=line_row['account_id'],
                debit=line_row['debit'],
                credit=line_row['credit'],
                memo=line_row['memo'],
                account_name=line_row['account_name'],
                account_number=line_row['account_number']
            ))

        conn.close()
        return entry

    @staticmethod
    def get_all(
        client_id: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        entry_type: Optional[str] = None,
        limit: int = 100
    ) -> List['JournalEntry']:
        """Get journal entries for a client with optional filters."""
        conn = get_connection()
        cursor = conn.cursor()

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
            entry = JournalEntry(
                id=row['id'],
                client_id=row['client_id'],
                entry_date=date.fromisoformat(row['entry_date']),
                description=row['description'],
                source_reference=row['source_reference'],
                entry_type=row['entry_type'],
                aje_reference=row['aje_reference'] if 'aje_reference' in row.keys() else None
            )

            # Get lines
            cursor.execute(
                """
                SELECT jel.*, a.name as account_name, a.account_number
                FROM journal_entry_lines jel
                JOIN accounts a ON jel.account_id = a.id
                WHERE jel.journal_entry_id = ?
                ORDER BY jel.id
                """,
                (entry.id,)
            )

            for line_row in cursor.fetchall():
                entry.lines.append(JournalEntryLine(
                    id=line_row['id'],
                    journal_entry_id=line_row['journal_entry_id'],
                    account_id=line_row['account_id'],
                    debit=line_row['debit'],
                    credit=line_row['credit'],
                    memo=line_row['memo'],
                    account_name=line_row['account_name'],
                    account_number=line_row['account_number']
                ))

            entries.append(entry)

        conn.close()
        return entries

    @staticmethod
    def delete(entry_id: int):
        """Delete a journal entry and its lines."""
        from models.audit_log import AuditLog

        conn = get_connection()
        cursor = conn.cursor()

        # Get the entry info for audit logging
        cursor.execute("SELECT * FROM journal_entries WHERE id = ?", (entry_id,))
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
                conn.close()
                raise ValueError(
                    f"{closed.period_name} is closed. Reopen the year before deleting "
                    f"entries dated {entry_date.isoformat()}."
                )

            cursor.execute("DELETE FROM journal_entries WHERE id = ?", (entry_id,))
            conn.commit()

            # Log to audit trail
            try:
                AuditLog.log_change(
                    client_id=client_id,
                    table_name='journal_entries',
                    record_id=entry_id,
                    action='DELETE',
                    old_values=old_values
                )
            except Exception:
                pass  # Don't fail the delete if audit logging fails

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
        conn = get_connection()
        cursor = conn.cursor()

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
        conn.close()

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
        conn = get_connection()
        cursor = conn.cursor()

        # Look for journal entries on the same date with matching amount
        # Amount could be in either debit or credit depending on transaction type
        abs_amount = abs(amount)

        query = """
            SELECT DISTINCT je.id, je.entry_date, je.description, je.source_reference,
                   jel.debit, jel.credit, jel.memo
            FROM journal_entries je
            JOIN journal_entry_lines jel ON je.id = jel.journal_entry_id
            WHERE je.client_id = ?
              AND je.entry_date = ?
              AND (ABS(jel.debit - ?) < 0.01 OR ABS(jel.credit - ?) < 0.01)
        """
        params = [client_id, entry_date.isoformat(), abs_amount, abs_amount]

        # Optionally filter by bank account
        if bank_account_id:
            query += " AND jel.account_id = ?"
            params.append(bank_account_id)

        cursor.execute(query, params)

        duplicates = []
        for row in cursor.fetchall():
            duplicates.append({
                'entry_id': row['id'],
                'entry_date': row['entry_date'],
                'description': row['description'],
                'source_reference': row['source_reference'],
                'amount': row['debit'] if row['debit'] > 0 else row['credit'],
                'memo': row['memo']
            })

        conn.close()
        return duplicates
