import sqlite3
from dataclasses import dataclass
from typing import Optional, List
from database.connection import get_connection


@dataclass
class Account:
    id: Optional[int] = None
    client_id: int = 0
    account_number: str = ""
    name: str = ""
    type: str = ""  # Asset, Liability, Equity, Revenue, Expense
    subtype: Optional[str] = None
    description: Optional[str] = None  # Memo/notes to identify the account
    is_active: bool = True

    @staticmethod
    def get_all(client_id: int, active_only: bool = True) -> List['Account']:
        """Get all accounts for a client, optionally filtered by active status."""
        conn = get_connection()
        cursor = conn.cursor()

        if active_only:
            cursor.execute(
                "SELECT * FROM accounts WHERE client_id = ? AND is_active = 1 ORDER BY account_number",
                (client_id,)
            )
        else:
            cursor.execute(
                "SELECT * FROM accounts WHERE client_id = ? ORDER BY account_number",
                (client_id,)
            )

        rows = cursor.fetchall()
        conn.close()

        return [Account(
            id=row['id'],
            client_id=row['client_id'],
            account_number=row['account_number'],
            name=row['name'],
            type=row['type'],
            subtype=row['subtype'],
            description=row['description'] if 'description' in row.keys() else None,
            is_active=bool(row['is_active'])
        ) for row in rows]

    @staticmethod
    def get_by_id(account_id: int) -> Optional['Account']:
        """Get an account by its ID."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return Account(
                id=row['id'],
                client_id=row['client_id'],
                account_number=row['account_number'],
                name=row['name'],
                type=row['type'],
                subtype=row['subtype'],
                description=row['description'] if 'description' in row.keys() else None,
                is_active=bool(row['is_active'])
            )
        return None

    @staticmethod
    def get_by_type(client_id: int, account_type: str) -> List['Account']:
        """Get all active accounts of a specific type for a client."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM accounts WHERE client_id = ? AND type = ? AND is_active = 1 ORDER BY account_number",
            (client_id, account_type)
        )
        rows = cursor.fetchall()
        conn.close()

        return [Account(
            id=row['id'],
            client_id=row['client_id'],
            account_number=row['account_number'],
            name=row['name'],
            type=row['type'],
            subtype=row['subtype'],
            description=row['description'] if 'description' in row.keys() else None,
            is_active=bool(row['is_active'])
        ) for row in rows]

    def save(self) -> int:
        """Save or update the account."""
        conn = get_connection()
        cursor = conn.cursor()

        if self.id is None:
            cursor.execute(
                """
                INSERT INTO accounts (client_id, account_number, name, type, subtype, description, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (self.client_id, self.account_number, self.name, self.type, self.subtype, self.description, int(self.is_active))
            )
            self.id = cursor.lastrowid
        else:
            cursor.execute(
                """
                UPDATE accounts
                SET account_number = ?, name = ?, type = ?, subtype = ?, description = ?, is_active = ?
                WHERE id = ?
                """,
                (self.account_number, self.name, self.type, self.subtype, self.description, int(self.is_active), self.id)
            )

        conn.commit()
        conn.close()
        return self.id

    def deactivate(self):
        """Soft delete - mark account as inactive."""
        self.is_active = False
        self.save()

    @staticmethod
    def has_transactions(account_id: int) -> bool:
        """Check if an account has any journal entry lines."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM journal_entry_lines WHERE account_id = ?",
            (account_id,)
        )
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0

    def display_name(self) -> str:
        """Return formatted display name with account number."""
        return f"{self.account_number} - {self.name}"

    @staticmethod
    def get_balance(account_id: int, as_of_date: Optional[str] = None) -> float:
        """
        Calculate the balance for an account.
        For Asset/Expense: Debits increase, Credits decrease
        For Liability/Equity/Revenue: Credits increase, Debits decrease
        """
        conn = get_connection()
        cursor = conn.cursor()

        # Get account type
        cursor.execute("SELECT type FROM accounts WHERE id = ?", (account_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return 0.0

        account_type = row['type']

        # Build query
        if as_of_date:
            cursor.execute(
                """
                SELECT COALESCE(SUM(jel.debit), 0) as total_debits,
                       COALESCE(SUM(jel.credit), 0) as total_credits
                FROM journal_entry_lines jel
                JOIN journal_entries je ON jel.journal_entry_id = je.id
                WHERE jel.account_id = ? AND je.entry_date <= ?
                """,
                (account_id, as_of_date)
            )
        else:
            cursor.execute(
                """
                SELECT COALESCE(SUM(debit), 0) as total_debits,
                       COALESCE(SUM(credit), 0) as total_credits
                FROM journal_entry_lines
                WHERE account_id = ?
                """,
                (account_id,)
            )

        row = cursor.fetchone()
        conn.close()

        total_debits = row['total_debits']
        total_credits = row['total_credits']

        # Calculate balance based on account type
        if account_type in ('Asset', 'Expense'):
            return total_debits - total_credits
        else:  # Liability, Equity, Revenue
            return total_credits - total_debits
