import sqlite3
from dataclasses import dataclass
from typing import Optional, List
from database.connection import get_connection, get_cursor
from constants import AccountType
from money import to_dollars


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
    def _from_row(row) -> 'Account':
        """Build an Account from a DB row (single source of the row mapping)."""
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

    @staticmethod
    def count(client_id: int, active_only: bool = True) -> int:
        """Count a client's accounts (cheap; no object hydration)."""
        with get_cursor() as cursor:
            if active_only:
                cursor.execute(
                    "SELECT COUNT(*) FROM accounts WHERE client_id = ? AND is_active = 1",
                    (client_id,)
                )
            else:
                cursor.execute(
                    "SELECT COUNT(*) FROM accounts WHERE client_id = ?",
                    (client_id,)
                )
            return cursor.fetchone()[0]

    @staticmethod
    def get_all(client_id: int, active_only: bool = True) -> List['Account']:
        """Get all accounts for a client, optionally filtered by active status."""
        with get_cursor() as cursor:
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
        return [Account._from_row(row) for row in rows]

    @staticmethod
    def get_by_id(account_id: int, client_id: Optional[int] = None) -> Optional['Account']:
        """Get an account by its ID.

        If ``client_id`` is given, the account is returned only when it belongs
        to that client -- defense-in-depth against reading another client's data
        with a mismatched id. Returns None on a cross-client mismatch.
        """
        with get_cursor() as cursor:
            if client_id is None:
                cursor.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
            else:
                cursor.execute(
                    "SELECT * FROM accounts WHERE id = ? AND client_id = ?",
                    (account_id, client_id)
                )
            row = cursor.fetchone()
        return Account._from_row(row) if row else None

    @staticmethod
    def get_by_type(client_id: int, account_type: str) -> List['Account']:
        """Get all active accounts of a specific type for a client."""
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM accounts WHERE client_id = ? AND type = ? AND is_active = 1 ORDER BY account_number",
                (client_id, account_type)
            )
            rows = cursor.fetchall()
        return [Account._from_row(row) for row in rows]

    def save(self) -> int:
        """Save or update the account."""
        from models.audit_log import AuditLog

        is_new = self.id is None
        old_values = None
        with get_cursor(commit=True) as cursor:
            if is_new:
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
                    "SELECT account_number, name, type, subtype, is_active "
                    "FROM accounts WHERE id = ? AND client_id = ?",
                    (self.id, self.client_id),
                )
                prev = cursor.fetchone()
                if not prev:
                    raise ValueError("Account not found for the selected client.")
                old_values = {
                    'account_number': prev['account_number'],
                    'name': prev['name'],
                    'type': prev['type'],
                    'subtype': prev['subtype'],
                    'is_active': bool(prev['is_active']),
                }
                cursor.execute(
                    """
                    UPDATE accounts
                    SET account_number = ?, name = ?, type = ?, subtype = ?, description = ?, is_active = ?
                    WHERE id = ? AND client_id = ?
                    """,
                    (self.account_number, self.name, self.type, self.subtype,
                     self.description, int(self.is_active), self.id, self.client_id)
                )

        new_values = {
            'account_number': self.account_number,
            'name': self.name,
            'type': self.type,
            'subtype': self.subtype,
            'is_active': self.is_active,
        }
        AuditLog.log_change_safe(
            client_id=self.client_id,
            table_name='accounts',
            record_id=self.id,
            action='INSERT' if is_new else 'UPDATE',
            old_values=old_values,
            new_values=new_values,
        )
        return self.id

    def deactivate(self):
        """Soft delete - mark account as inactive."""
        self.is_active = False
        self.save()

    @staticmethod
    def has_transactions(account_id: int) -> bool:
        """Check if an account has any journal entry lines."""
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM journal_entry_lines WHERE account_id = ?",
                (account_id,)
            )
            return cursor.fetchone()[0] > 0

    @staticmethod
    def deletion_blockers(account_id: int, conn=None) -> dict:
        """Return why an account can't be hard-deleted, as ``{reason: count}``.

        Every table that references ``accounts`` is checked -- journal entry
        lines, categorization rules, and imported transactions -- not just
        journal entry lines. An empty dict means the account is safe to delete.
        (The FKs are RESTRICT, so any reference would otherwise make a raw
        DELETE raise IntegrityError.)
        """
        owns_conn = conn is None
        if owns_conn:
            conn = get_connection()
        try:
            cursor = conn.cursor()

            def count(sql, *params):
                return cursor.execute(sql, params).fetchone()[0]

            blockers = {}
            je = count("SELECT COUNT(*) FROM journal_entry_lines WHERE account_id = ?", account_id)
            if je:
                blockers["journal entry lines"] = je
            rules = count("SELECT COUNT(*) FROM categorization_rules WHERE default_account_id = ?", account_id)
            if rules:
                blockers["categorization rules"] = rules
            imp = count(
                "SELECT COUNT(*) FROM imported_transactions "
                "WHERE bank_account_id = ? OR suggested_account_id = ?",
                account_id, account_id,
            )
            if imp:
                blockers["imported transactions"] = imp
            return blockers
        finally:
            if owns_conn:
                conn.close()

    @staticmethod
    def delete(account_id: int, client_id: Optional[int] = None):
        """Hard-delete an account, with guards, audit logging, and leak safety.

        Raises ValueError if the account does not exist (or does not belong to
        ``client_id`` when given), or if it is still referenced by any journal
        entry, categorization rule, or imported transaction -- in which case it
        should be deactivated instead. Prefer this over a raw DELETE so the
        referential guard, audit trail, and connection handling always apply.
        """
        from models.audit_log import AuditLog

        conn = get_connection()
        try:
            cursor = conn.cursor()

            if client_id is None:
                cursor.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
            else:
                cursor.execute(
                    "SELECT * FROM accounts WHERE id = ? AND client_id = ?",
                    (account_id, client_id)
                )
            row = cursor.fetchone()
            if not row:
                raise ValueError("Account not found.")

            blockers = Account.deletion_blockers(account_id, conn=conn)
            if blockers:
                detail = ", ".join(f"{v} {k}" for k, v in blockers.items())
                raise ValueError(
                    f"Cannot delete this account — it is still referenced by {detail}. "
                    f"Deactivate it instead."
                )

            old_values = {
                "account_number": row["account_number"],
                "name": row["name"],
                "type": row["type"],
            }
            acct_client_id = row["client_id"]

            cursor.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            conn.commit()

            AuditLog.log_change_safe(
                client_id=acct_client_id,
                table_name="accounts",
                record_id=account_id,
                action="DELETE",
                old_values=old_values,
            )
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def display_name(self) -> str:
        """Return formatted display name with account number."""
        return f"{self.account_number} - {self.name}"

    @staticmethod
    def get_balance(account_id: int, as_of_date: Optional[str] = None,
                    client_id: Optional[int] = None) -> float:
        """
        Calculate the balance for an account.
        For Asset/Expense: Debits increase, Credits decrease
        For Liability/Equity/Revenue: Credits increase, Debits decrease

        If ``client_id`` is given, a balance is computed only when the account
        belongs to that client; a cross-client id returns 0.0.
        """
        with get_cursor() as cursor:
            # Get account type (scoped to the client when provided)
            if client_id is None:
                cursor.execute("SELECT type FROM accounts WHERE id = ?", (account_id,))
            else:
                cursor.execute(
                    "SELECT type FROM accounts WHERE id = ? AND client_id = ?",
                    (account_id, client_id)
                )
            row = cursor.fetchone()
            if not row:
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
            total_debits = row['total_debits']
            total_credits = row['total_credits']

        # Calculate balance based on account type (sums are integer cents).
        if AccountType.is_debit_normal(account_type):
            return to_dollars(total_debits - total_credits)
        else:  # Liability, Equity, Revenue
            return to_dollars(total_credits - total_debits)
