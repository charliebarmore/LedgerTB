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
        errors = self.validate()
        if errors:
            raise ValueError("; ".join(errors))

        conn = get_connection()
        cursor = conn.cursor()

        try:
            if self.id is None:
                # Insert new entry
                cursor.execute(
                    """
                    INSERT INTO journal_entries (client_id, entry_date, description, source_reference, entry_type)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (self.client_id, self.entry_date.isoformat(), self.description, self.source_reference, self.entry_type)
                )
                self.id = cursor.lastrowid
            else:
                # Update existing entry
                cursor.execute(
                    """
                    UPDATE journal_entries
                    SET entry_date = ?, description = ?, source_reference = ?, entry_type = ?
                    WHERE id = ?
                    """,
                    (self.entry_date.isoformat(), self.description, self.source_reference, self.entry_type, self.id)
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
            entry_type=row['entry_type']
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
                entry_type=row['entry_type']
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
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM journal_entries WHERE id = ?", (entry_id,))
        conn.commit()
        conn.close()

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
