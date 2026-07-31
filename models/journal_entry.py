import sqlite3
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import date
from database.connection import get_connection, get_cursor
from money import to_cents, to_dollars
from utils.fiscal_dates import require_valid_range


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
            if line.debit < 0 or line.credit < 0:
                errors.append(f"Line {i+1}: Debit and credit cannot be negative")
            if line.debit == 0 and line.credit == 0:
                errors.append(f"Line {i+1}: Must have either debit or credit")
            if line.debit > 0 and line.credit > 0:
                errors.append(f"Line {i+1}: Cannot have both debit and credit")

        return errors

    def save(self, conn=None) -> int:
        """Save the journal entry and its lines.

        If ``conn`` is provided, this method participates in the caller's
        transaction: it uses that connection and does not commit or close it.
        The audit row is always written on that same connection.
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
                cursor.execute(
                    "SELECT 1 FROM imported_transactions "
                    "WHERE journal_entry_id = ? AND client_id = ? LIMIT 1",
                    (self.id, self.client_id),
                )
                if cursor.fetchone():
                    raise ValueError(
                        "Imported postings cannot be edited in place. "
                        "Use Correct category so the source and ledger history stay intact."
                    )
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
                    SELECT account_id, debit, credit, memo
                    FROM journal_entry_lines WHERE journal_entry_id = ? ORDER BY id
                    """,
                    (self.id,),
                )
                old_values["lines"] = [
                    {
                        "account_id": row["account_id"],
                        "debit": to_dollars(row["debit"]),
                        "credit": to_dollars(row["credit"]),
                        "memo": row["memo"],
                    }
                    for row in cursor.fetchall()
                ]

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

            new_values = {
                'entry_date': self.entry_date.isoformat(),
                'description': self.description,
                'source_reference': self.source_reference,
                'entry_type': self.entry_type,
                'aje_reference': self.aje_reference,
                'total_debits': self.total_debits(),
                'total_credits': self.total_credits(),
                'lines': [
                    {
                        'account_id': line.account_id,
                        'debit': line.debit,
                        'credit': line.credit,
                        'memo': line.memo,
                    }
                    for line in self.lines
                ],
            }
            AuditLog.write(
                cursor, self.client_id, 'journal_entries', self.id,
                'INSERT' if is_new else 'UPDATE',
                old_values=old_values, new_values=new_values,
            )

            if owns_conn:
                conn.commit()

        except Exception as e:
            if owns_conn:
                conn.rollback()
            raise e
        finally:
            if owns_conn:
                conn.close()

        return self.id

    @staticmethod
    def get_hand_keyed_recent(client_id: int, limit: int = 10) -> List[dict]:
        """Recent journal entries a person actually keyed, newest first.

        Excludes entries created by an import: ``post_transaction`` stamps those
        with a "Import batch <id>" source_reference, and on the activity feed
        they belong to the one import event that produced them rather than as
        dozens of separate lines.

        ``created_at`` is stored UTC (CURRENT_TIMESTAMP), so it is converted to
        local time here — the activity feed merges these with timestamps from
        other tables and has to compare like with like.
        """
        query = """
            SELECT je.id, je.entry_date, je.description, je.entry_type,
                   je.aje_reference,
                   datetime(je.created_at, 'localtime') created_at_local,
                   COALESCE(SUM(jel.debit), 0) total_debits
            FROM journal_entries je
            LEFT JOIN journal_entry_lines jel ON jel.journal_entry_id = je.id
            WHERE je.client_id = ?
              AND (je.source_reference IS NULL
                   OR je.source_reference NOT LIKE 'Import batch %')
            GROUP BY je.id
            ORDER BY je.created_at DESC, je.id DESC
            LIMIT ?
        """
        with get_cursor() as cursor:
            cursor.execute(query, (client_id, max(1, int(limit))))
            rows = cursor.fetchall()

        return [{
            "id": row["id"],
            "entry_date": date.fromisoformat(row["entry_date"]) if row["entry_date"] else None,
            "description": row["description"],
            "entry_type": row["entry_type"],
            "aje_reference": row["aje_reference"],
            "created_at": row["created_at_local"],
            "total_debits": to_dollars(row["total_debits"] or 0),
        } for row in rows]

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
        limit: int = 100,
        offset: int = 0,
    ) -> List['JournalEntry']:
        """Get journal entries for a client with optional filters."""
        require_valid_range(start_date, end_date, "Journal entry filter")
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

            query += " ORDER BY entry_date DESC, id DESC LIMIT ? OFFSET ?"
            params.extend([max(1, int(limit)), max(0, int(offset))])

            cursor.execute(query, params)
            rows = cursor.fetchall()

            entries = [JournalEntry._entry_from_row(row) for row in rows]
            if entries:
                entry_by_id = {entry.id: entry for entry in entries}
                placeholders = ", ".join("?" for _ in entries)
                cursor.execute(
                    f"""
                    SELECT jel.*, a.name as account_name, a.account_number
                    FROM journal_entry_lines jel
                    JOIN accounts a ON jel.account_id = a.id
                    WHERE jel.journal_entry_id IN ({placeholders})
                    ORDER BY jel.journal_entry_id, jel.id
                    """,
                    list(entry_by_id),
                )
                for line_row in cursor.fetchall():
                    entry_by_id[line_row["journal_entry_id"]].lines.append(
                        JournalEntry._line_from_row(line_row)
                    )
        return entries

    @staticmethod
    def get_filtered_summary(
        client_id: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        entry_type: Optional[str] = None,
    ) -> dict:
        """Return SQL-backed counts and totals for all matching entries."""
        require_valid_range(start_date, end_date, "Journal entry filter")
        clauses = ["client_id = ?"]
        params = [client_id]
        if start_date:
            clauses.append("entry_date >= ?")
            params.append(start_date.isoformat())
        if end_date:
            clauses.append("entry_date <= ?")
            params.append(end_date.isoformat())
        if entry_type:
            clauses.append("entry_type = ?")
            params.append(entry_type)

        with get_cursor() as cursor:
            cursor.execute(
                """
                WITH filtered AS (
                    SELECT id, entry_type FROM journal_entries
                    WHERE
                """ + " AND ".join(clauses) + """
                ), line_totals AS (
                    SELECT journal_entry_id, SUM(debit) debits, SUM(credit) credits
                    FROM journal_entry_lines
                    WHERE journal_entry_id IN (SELECT id FROM filtered)
                    GROUP BY journal_entry_id
                )
                SELECT COUNT(f.id) total_count,
                       COALESCE(SUM(lt.debits), 0) total_debits,
                       COALESCE(SUM(lt.credits), 0) total_credits,
                       SUM(CASE WHEN f.entry_type = 'Regular' THEN 1 ELSE 0 END) regular_count,
                       SUM(CASE WHEN f.entry_type = 'Adjusting' THEN 1 ELSE 0 END) adjusting_count,
                       SUM(CASE WHEN f.entry_type = 'Beginning Balance' THEN 1 ELSE 0 END) beginning_count
                FROM filtered f
                LEFT JOIN line_totals lt ON lt.journal_entry_id = f.id
                """,
                params,
            )
            row = cursor.fetchone()
        return {
            "total_count": int(row["total_count"] or 0),
            "total_debits": to_dollars(row["total_debits"] or 0),
            "total_credits": to_dollars(row["total_credits"] or 0),
            "regular_count": int(row["regular_count"] or 0),
            "adjusting_count": int(row["adjusting_count"] or 0),
            "beginning_count": int(row["beginning_count"] or 0),
        }

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
                cursor.execute(
                    """
                    SELECT account_id, debit, credit, memo
                    FROM journal_entry_lines WHERE journal_entry_id = ? ORDER BY id
                    """,
                    (entry_id,),
                )
                old_values["lines"] = [
                    {
                        "account_id": line["account_id"],
                        "debit": to_dollars(line["debit"]),
                        "credit": to_dollars(line["credit"]),
                        "memo": line["memo"],
                    }
                    for line in cursor.fetchall()
                ]

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

                cursor.execute(
                    "SELECT 1 FROM imported_transactions WHERE journal_entry_id = ? LIMIT 1",
                    (entry_id,),
                )
                if cursor.fetchone():
                    raise ValueError(
                        "This entry was created from an imported transaction. "
                        "Reverse it instead so the source history remains intact."
                    )

                cursor.execute("DELETE FROM journal_entries WHERE id = ?", (entry_id,))
                AuditLog.write(
                    cursor, client_id, 'journal_entries', entry_id, 'DELETE',
                    old_values=old_values,
                )
                conn.commit()
        finally:
            conn.close()

    @staticmethod
    def reverse(entry_id: int, client_id: int, reversal_date: date) -> 'JournalEntry':
        """Post an equal-and-opposite entry without altering accounting history."""
        from models.audit_log import AuditLog

        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM journal_entries WHERE id = ? AND client_id = ?",
                (entry_id, client_id),
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError("Journal entry not found for the selected client.")
            reference = f"Reversal of JE #{entry_id}"
            cursor.execute(
                "SELECT id FROM journal_entries WHERE client_id = ? AND source_reference = ? LIMIT 1",
                (client_id, reference),
            )
            existing = cursor.fetchone()
            if existing:
                raise ValueError(f"This entry was already reversed by JE #{existing['id']}.")
            cursor.execute(
                """
                SELECT account_id, debit, credit, memo
                FROM journal_entry_lines WHERE journal_entry_id = ? ORDER BY id
                """,
                (entry_id,),
            )
            source_lines = cursor.fetchall()
            reversal = JournalEntry(
                client_id=client_id,
                entry_date=reversal_date,
                description=f"Reversal: {row['description'] or f'Journal Entry #{entry_id}'}"[:200],
                source_reference=reference,
                entry_type="Regular",
                lines=[
                    JournalEntryLine(
                        account_id=line["account_id"],
                        debit=to_dollars(line["credit"]),
                        credit=to_dollars(line["debit"]),
                        memo=f"Reversal of JE #{entry_id}",
                    )
                    for line in source_lines
                ],
            )
            reversal.save(conn=conn)
            AuditLog.write(
                cursor, client_id, "journal_entries", entry_id, "REVERSE",
                old_values={"reversed": False},
                new_values={
                    "reversed": True,
                    "reversal_entry_id": reversal.id,
                    "reversal_date": reversal_date.isoformat(),
                },
            )
            conn.commit()
            return reversal
        except Exception:
            conn.rollback()
            raise
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
        require_valid_range(period_start, period_end, "AJE period")
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
