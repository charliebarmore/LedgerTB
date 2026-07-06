import sqlite3
from dataclasses import dataclass
from typing import Optional, List
from datetime import date
from database.connection import get_connection, get_cursor


@dataclass
class ImportedTransaction:
    id: Optional[int] = None
    client_id: int = 0
    import_batch: Optional[str] = None
    transaction_date: date = None
    description: str = ""
    amount: float = 0.0
    bank_account_id: Optional[int] = None
    suggested_account_id: Optional[int] = None
    status: str = "Pending"  # Pending, Categorized, Posted
    journal_entry_id: Optional[int] = None

    # Display fields (not stored)
    bank_account_name: Optional[str] = None
    suggested_account_name: Optional[str] = None

    def save(self, conn=None) -> int:
        """Save or update the imported transaction.

        If ``conn`` is provided, uses the caller's connection and does not
        commit or close it (the caller owns the transaction). When omitted it
        manages its own connection.
        """
        owns_conn = conn is None
        if owns_conn:
            conn = get_connection()
        cursor = conn.cursor()

        try:
            if self.id is None:
                cursor.execute(
                    """
                    INSERT INTO imported_transactions
                    (client_id, import_batch, transaction_date, description, amount, bank_account_id,
                     suggested_account_id, status, journal_entry_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.client_id,
                        self.import_batch,
                        self.transaction_date.isoformat() if self.transaction_date else None,
                        self.description,
                        self.amount,
                        self.bank_account_id,
                        self.suggested_account_id,
                        self.status,
                        self.journal_entry_id
                    )
                )
                self.id = cursor.lastrowid
            else:
                cursor.execute(
                    """
                    UPDATE imported_transactions
                    SET import_batch = ?, transaction_date = ?, description = ?, amount = ?,
                        bank_account_id = ?, suggested_account_id = ?, status = ?, journal_entry_id = ?
                    WHERE id = ?
                    """,
                    (
                        self.import_batch,
                        self.transaction_date.isoformat() if self.transaction_date else None,
                        self.description,
                        self.amount,
                        self.bank_account_id,
                        self.suggested_account_id,
                        self.status,
                        self.journal_entry_id,
                        self.id
                    )
                )

            if owns_conn:
                conn.commit()
        except Exception:
            if owns_conn:
                conn.rollback()
            raise
        finally:
            if owns_conn:
                conn.close()
        return self.id

    @staticmethod
    def get_by_status(client_id: int, status: str) -> List['ImportedTransaction']:
        """Get all transactions for a client with a specific status."""
        with get_cursor() as cursor:
            cursor.execute(
                """
                SELECT it.*,
                       ba.name as bank_account_name,
                       sa.name as suggested_account_name
                FROM imported_transactions it
                LEFT JOIN accounts ba ON it.bank_account_id = ba.id
                LEFT JOIN accounts sa ON it.suggested_account_id = sa.id
                WHERE it.client_id = ? AND it.status = ?
                ORDER BY it.transaction_date DESC
                """,
                (client_id, status)
            )

            rows = cursor.fetchall()

        return [ImportedTransaction(
            id=row['id'],
            client_id=row['client_id'],
            import_batch=row['import_batch'],
            transaction_date=date.fromisoformat(row['transaction_date']) if row['transaction_date'] else None,
            description=row['description'],
            amount=row['amount'],
            bank_account_id=row['bank_account_id'],
            suggested_account_id=row['suggested_account_id'],
            status=row['status'],
            journal_entry_id=row['journal_entry_id'],
            bank_account_name=row['bank_account_name'],
            suggested_account_name=row['suggested_account_name']
        ) for row in rows]

    @staticmethod
    def get_pending_count(client_id: int) -> int:
        """Get count of pending transactions for a client."""
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM imported_transactions WHERE client_id = ? AND status = 'Pending'",
                (client_id,)
            )
            count = cursor.fetchone()[0]
        return count

    @staticmethod
    def bulk_insert(transactions: List['ImportedTransaction']):
        """Insert multiple transactions at once."""
        data = [
            (
                t.client_id,
                t.import_batch,
                t.transaction_date.isoformat() if t.transaction_date else None,
                t.description,
                t.amount,
                t.bank_account_id,
                t.suggested_account_id,
                t.status,
                t.journal_entry_id
            )
            for t in transactions
        ]

        with get_cursor(commit=True) as cursor:
            cursor.executemany(
                """
                INSERT INTO imported_transactions
                (client_id, import_batch, transaction_date, description, amount, bank_account_id,
                 suggested_account_id, status, journal_entry_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                data
            )

    @staticmethod
    def delete(transaction_id: int):
        """Delete an imported transaction."""
        with get_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM imported_transactions WHERE id = ?", (transaction_id,))

    @staticmethod
    def delete_batch(batch_id: str):
        """Delete all transactions from a batch."""
        with get_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM imported_transactions WHERE import_batch = ?", (batch_id,))

    @staticmethod
    def get_all(
        client_id: int,
        start_date: date = None,
        end_date: date = None,
        status: str = None,
        bank_account_id: int = None,
        limit: int = 500
    ) -> List['ImportedTransaction']:
        """Get all imported transactions for a client with optional filters."""
        query = """
            SELECT it.*,
                   ba.name as bank_account_name,
                   ba.account_number as bank_account_number,
                   sa.name as suggested_account_name,
                   sa.account_number as suggested_account_number
            FROM imported_transactions it
            LEFT JOIN accounts ba ON it.bank_account_id = ba.id
            LEFT JOIN accounts sa ON it.suggested_account_id = sa.id
            WHERE it.client_id = ?
        """
        params = [client_id]

        if start_date:
            query += " AND it.transaction_date >= ?"
            params.append(start_date.isoformat())

        if end_date:
            query += " AND it.transaction_date <= ?"
            params.append(end_date.isoformat())

        if status:
            query += " AND it.status = ?"
            params.append(status)

        if bank_account_id:
            query += " AND it.bank_account_id = ?"
            params.append(bank_account_id)

        query += " ORDER BY it.transaction_date DESC, it.id DESC LIMIT ?"
        params.append(limit)

        with get_cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        return [ImportedTransaction(
            id=row['id'],
            client_id=row['client_id'],
            import_batch=row['import_batch'],
            transaction_date=date.fromisoformat(row['transaction_date']) if row['transaction_date'] else None,
            description=row['description'],
            amount=row['amount'],
            bank_account_id=row['bank_account_id'],
            suggested_account_id=row['suggested_account_id'],
            status=row['status'],
            journal_entry_id=row['journal_entry_id'],
            bank_account_name=row['bank_account_name'],
            suggested_account_name=row['suggested_account_name']
        ) for row in rows]

    @staticmethod
    def get_count(client_id: int) -> int:
        """Get total count of imported transactions for a client."""
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM imported_transactions WHERE client_id = ?",
                (client_id,)
            )
            count = cursor.fetchone()[0]
        return count
