from dataclasses import dataclass
from typing import Optional, List
from datetime import date
from database.connection import get_connection, get_cursor
from money import to_cents, to_dollars
from utils.fiscal_dates import require_valid_range


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
    source_id: Optional[str] = None
    source_filename: Optional[str] = None
    source_row_number: Optional[int] = None
    row_fingerprint: Optional[str] = None
    idempotency_key: Optional[str] = None
    duplicate_override: bool = False
    duplicate_override_reason: Optional[str] = None
    duplicate_of_id: Optional[int] = None

    # Display fields (not stored)
    bank_account_name: Optional[str] = None
    suggested_account_name: Optional[str] = None
    is_cleared: bool = False
    reconciliation_id: Optional[int] = None
    reconciliation_status: Optional[str] = None
    statement_end_date: Optional[date] = None

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
        from models.audit_log import AuditLog
        is_new = self.id is None
        old_values = None

        try:
            if is_new:
                cursor.execute(
                    """
                    INSERT INTO imported_transactions
                    (client_id, import_batch, transaction_date, description, amount, bank_account_id,
                     suggested_account_id, status, journal_entry_id, source_id, source_filename,
                     source_row_number, row_fingerprint, idempotency_key, duplicate_override,
                     duplicate_override_reason, duplicate_of_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.client_id,
                        self.import_batch,
                        self.transaction_date.isoformat() if self.transaction_date else None,
                        self.description,
                        to_cents(self.amount),
                        self.bank_account_id,
                        self.suggested_account_id,
                        self.status,
                        self.journal_entry_id,
                        self.source_id,
                        self.source_filename,
                        self.source_row_number,
                        self.row_fingerprint,
                        self.idempotency_key,
                        int(self.duplicate_override),
                        self.duplicate_override_reason,
                        self.duplicate_of_id,
                    )
                )
                self.id = cursor.lastrowid
            else:
                cursor.execute(
                    "SELECT * FROM imported_transactions WHERE id = ? AND client_id = ?",
                    (self.id, self.client_id),
                )
                old = cursor.fetchone()
                if not old:
                    raise ValueError("Imported transaction not found for the selected client.")
                old_values = {
                    "import_batch": old["import_batch"],
                    "transaction_date": old["transaction_date"],
                    "description": old["description"],
                    "amount": to_dollars(old["amount"]),
                    "bank_account_id": old["bank_account_id"],
                    "suggested_account_id": old["suggested_account_id"],
                    "status": old["status"],
                    "journal_entry_id": old["journal_entry_id"],
                    "source_id": old["source_id"],
                    "source_filename": old["source_filename"],
                    "source_row_number": old["source_row_number"],
                    "row_fingerprint": old["row_fingerprint"],
                    "idempotency_key": old["idempotency_key"],
                    "duplicate_override": bool(old["duplicate_override"]),
                    "duplicate_override_reason": old["duplicate_override_reason"],
                    "duplicate_of_id": old["duplicate_of_id"],
                }
                cursor.execute(
                    """
                    UPDATE imported_transactions
                    SET import_batch = ?, transaction_date = ?, description = ?, amount = ?,
                        bank_account_id = ?, suggested_account_id = ?, status = ?, journal_entry_id = ?,
                        source_id = ?, source_filename = ?, source_row_number = ?, row_fingerprint = ?,
                        idempotency_key = ?, duplicate_override = ?, duplicate_override_reason = ?,
                        duplicate_of_id = ?
                    WHERE id = ? AND client_id = ?
                    """,
                    (
                        self.import_batch,
                        self.transaction_date.isoformat() if self.transaction_date else None,
                        self.description,
                        to_cents(self.amount),
                        self.bank_account_id,
                        self.suggested_account_id,
                        self.status,
                        self.journal_entry_id,
                        self.source_id,
                        self.source_filename,
                        self.source_row_number,
                        self.row_fingerprint,
                        self.idempotency_key,
                        int(self.duplicate_override),
                        self.duplicate_override_reason,
                        self.duplicate_of_id,
                        self.id, self.client_id
                    )
                )

            new_values = {
                "import_batch": self.import_batch,
                "transaction_date": self.transaction_date.isoformat() if self.transaction_date else None,
                "description": self.description,
                "amount": self.amount,
                "bank_account_id": self.bank_account_id,
                "suggested_account_id": self.suggested_account_id,
                "status": self.status,
                "journal_entry_id": self.journal_entry_id,
                "source_id": self.source_id,
                "source_filename": self.source_filename,
                "source_row_number": self.source_row_number,
                "row_fingerprint": self.row_fingerprint,
                "idempotency_key": self.idempotency_key,
                "duplicate_override": self.duplicate_override,
                "duplicate_override_reason": self.duplicate_override_reason,
                "duplicate_of_id": self.duplicate_of_id,
            }
            AuditLog.write(
                cursor, self.client_id, "imported_transactions", self.id,
                "INSERT" if is_new else "UPDATE",
                old_values=old_values, new_values=new_values,
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
            amount=to_dollars(row['amount']),
            bank_account_id=row['bank_account_id'],
            suggested_account_id=row['suggested_account_id'],
            status=row['status'],
            journal_entry_id=row['journal_entry_id'],
            source_id=row['source_id'],
            source_filename=row['source_filename'],
            source_row_number=row['source_row_number'],
            row_fingerprint=row['row_fingerprint'],
            idempotency_key=row['idempotency_key'],
            duplicate_override=bool(row['duplicate_override']),
            duplicate_override_reason=row['duplicate_override_reason'],
            duplicate_of_id=row['duplicate_of_id'],
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
        from models.audit_log import AuditLog
        with get_cursor(commit=True) as cursor:
            for transaction in transactions:
                cursor.execute(
                    """
                    INSERT INTO imported_transactions
                    (client_id, import_batch, transaction_date, description, amount, bank_account_id,
                     suggested_account_id, status, journal_entry_id, source_id, source_filename,
                     source_row_number, row_fingerprint, idempotency_key, duplicate_override,
                     duplicate_override_reason, duplicate_of_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        transaction.client_id, transaction.import_batch,
                        transaction.transaction_date.isoformat() if transaction.transaction_date else None,
                        transaction.description, to_cents(transaction.amount),
                        transaction.bank_account_id, transaction.suggested_account_id,
                        transaction.status, transaction.journal_entry_id,
                        transaction.source_id, transaction.source_filename,
                        transaction.source_row_number, transaction.row_fingerprint,
                        transaction.idempotency_key, int(transaction.duplicate_override),
                        transaction.duplicate_override_reason, transaction.duplicate_of_id,
                    ),
                )
                transaction.id = cursor.lastrowid
                AuditLog.write(
                    cursor, transaction.client_id, "imported_transactions", transaction.id, "INSERT",
                    new_values={
                        "import_batch": transaction.import_batch,
                        "transaction_date": transaction.transaction_date,
                        "description": transaction.description,
                        "amount": transaction.amount,
                        "bank_account_id": transaction.bank_account_id,
                        "suggested_account_id": transaction.suggested_account_id,
                        "status": transaction.status,
                        "journal_entry_id": transaction.journal_entry_id,
                        "source_id": transaction.source_id,
                        "source_filename": transaction.source_filename,
                        "source_row_number": transaction.source_row_number,
                        "row_fingerprint": transaction.row_fingerprint,
                        "idempotency_key": transaction.idempotency_key,
                        "duplicate_override": transaction.duplicate_override,
                        "duplicate_override_reason": transaction.duplicate_override_reason,
                        "duplicate_of_id": transaction.duplicate_of_id,
                    },
                )

    @staticmethod
    def delete(transaction_id: int, client_id: Optional[int] = None):
        """Delete an imported transaction."""
        from models.audit_log import AuditLog
        with get_cursor(commit=True) as cursor:
            query = "SELECT * FROM imported_transactions WHERE id = ?"
            params = [transaction_id]
            if client_id is not None:
                query += " AND client_id = ?"
                params.append(client_id)
            cursor.execute(query, params)
            row = cursor.fetchone()
            if not row:
                raise ValueError("Imported transaction not found.")
            cursor.execute("DELETE FROM imported_transactions WHERE id = ?", (transaction_id,))
            AuditLog.write(
                cursor, row["client_id"], "imported_transactions", transaction_id, "DELETE",
                old_values={
                    "import_batch": row["import_batch"], "transaction_date": row["transaction_date"],
                    "description": row["description"], "amount": to_dollars(row["amount"]),
                    "bank_account_id": row["bank_account_id"], "status": row["status"],
                    "journal_entry_id": row["journal_entry_id"],
                },
            )

    @staticmethod
    def delete_batch(batch_id: str, client_id: Optional[int] = None):
        """Delete all transactions from a batch."""
        from models.audit_log import AuditLog
        with get_cursor(commit=True) as cursor:
            query = "SELECT * FROM imported_transactions WHERE import_batch = ?"
            params = [batch_id]
            if client_id is not None:
                query += " AND client_id = ?"
                params.append(client_id)
            cursor.execute(query, params)
            rows = cursor.fetchall()
            for row in rows:
                cursor.execute("DELETE FROM imported_transactions WHERE id = ?", (row["id"],))
                AuditLog.write(
                    cursor, row["client_id"], "imported_transactions", row["id"], "DELETE",
                    old_values={
                        "import_batch": row["import_batch"],
                        "transaction_date": row["transaction_date"],
                        "description": row["description"],
                        "amount": to_dollars(row["amount"]),
                        "status": row["status"],
                    },
                )

    @staticmethod
    def get_all(
        client_id: int,
        start_date: date = None,
        end_date: date = None,
        status: str = None,
        bank_account_id: int = None,
        limit: int = 500,
        cleared: Optional[bool] = None,
        offset: int = 0,
    ) -> List['ImportedTransaction']:
        """Get all imported transactions for a client with optional filters."""
        require_valid_range(start_date, end_date, "Transaction filter")
        query = """
            WITH clearance AS (
                SELECT jel.journal_entry_id, jel.account_id,
                       MAX(br.id) reconciliation_id,
                       MAX(br.status) reconciliation_status,
                       MAX(br.statement_end_date) statement_end_date
                FROM journal_entry_lines jel
                JOIN bank_reconciliation_items bri ON bri.journal_entry_line_id = jel.id
                JOIN bank_reconciliations br ON br.id = bri.reconciliation_id
                GROUP BY jel.journal_entry_id, jel.account_id
            )
            SELECT it.*,
                   ba.name as bank_account_name,
                   ba.account_number as bank_account_number,
                   sa.name as suggested_account_name,
                   sa.account_number as suggested_account_number,
                   c.reconciliation_id, c.reconciliation_status, c.statement_end_date
            FROM imported_transactions it
            LEFT JOIN accounts ba ON it.bank_account_id = ba.id
            LEFT JOIN accounts sa ON it.suggested_account_id = sa.id
            LEFT JOIN clearance c
              ON c.journal_entry_id = it.journal_entry_id
             AND c.account_id = it.bank_account_id
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

        if cleared is True:
            query += " AND c.reconciliation_id IS NOT NULL"
        elif cleared is False:
            query += " AND c.reconciliation_id IS NULL"

        query += " ORDER BY it.transaction_date DESC, it.id DESC LIMIT ? OFFSET ?"
        params.extend([max(1, int(limit)), max(0, int(offset))])

        with get_cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        return [ImportedTransaction(
            id=row['id'],
            client_id=row['client_id'],
            import_batch=row['import_batch'],
            transaction_date=date.fromisoformat(row['transaction_date']) if row['transaction_date'] else None,
            description=row['description'],
            amount=to_dollars(row['amount']),
            bank_account_id=row['bank_account_id'],
            suggested_account_id=row['suggested_account_id'],
            status=row['status'],
            journal_entry_id=row['journal_entry_id'],
            source_id=row['source_id'],
            source_filename=row['source_filename'],
            source_row_number=row['source_row_number'],
            row_fingerprint=row['row_fingerprint'],
            idempotency_key=row['idempotency_key'],
            duplicate_override=bool(row['duplicate_override']),
            duplicate_override_reason=row['duplicate_override_reason'],
            duplicate_of_id=row['duplicate_of_id'],
            bank_account_name=row['bank_account_name'],
            suggested_account_name=row['suggested_account_name'],
            is_cleared=row['reconciliation_id'] is not None,
            reconciliation_id=row['reconciliation_id'],
            reconciliation_status=row['reconciliation_status'],
            statement_end_date=date.fromisoformat(row['statement_end_date']) if row['statement_end_date'] else None,
        ) for row in rows]

    @staticmethod
    def get_filtered_summary(
        client_id: int,
        start_date: date = None,
        end_date: date = None,
        status: str = None,
        bank_account_id: int = None,
        cleared: Optional[bool] = None,
    ) -> dict:
        """Return counts and money totals across the entire filtered result."""
        require_valid_range(start_date, end_date, "Transaction filter")
        clauses = ["it.client_id = ?"]
        params = [client_id]
        if start_date:
            clauses.append("it.transaction_date >= ?")
            params.append(start_date.isoformat())
        if end_date:
            clauses.append("it.transaction_date <= ?")
            params.append(end_date.isoformat())
        if status:
            clauses.append("it.status = ?")
            params.append(status)
        if bank_account_id:
            clauses.append("it.bank_account_id = ?")
            params.append(bank_account_id)

        clearance_exists = """
            EXISTS (
                SELECT 1
                FROM journal_entry_lines jel
                JOIN bank_reconciliation_items bri ON bri.journal_entry_line_id = jel.id
                WHERE jel.journal_entry_id = it.journal_entry_id
                  AND jel.account_id = it.bank_account_id
            )
        """
        if cleared is True:
            clauses.append(clearance_exists)
        elif cleared is False:
            clauses.append("NOT " + clearance_exists)

        query = """
            SELECT COUNT(*) total_count,
                   COALESCE(SUM(CASE WHEN it.amount > 0 THEN it.amount ELSE 0 END), 0) deposits,
                   COALESCE(SUM(CASE WHEN it.amount < 0 THEN it.amount ELSE 0 END), 0) withdrawals,
                   SUM(CASE WHEN it.status = 'Posted' THEN 1 ELSE 0 END) posted_count,
                   SUM(CASE WHEN it.status = 'Pending' THEN 1 ELSE 0 END) pending_count
            FROM imported_transactions it
            WHERE
        """ + " AND ".join(clauses)
        with get_cursor() as cursor:
            cursor.execute(query, params)
            row = cursor.fetchone()
        return {
            "total_count": int(row["total_count"] or 0),
            "total_deposits": to_dollars(row["deposits"] or 0),
            "total_withdrawals": to_dollars(row["withdrawals"] or 0),
            "posted_count": int(row["posted_count"] or 0),
            "pending_count": int(row["pending_count"] or 0),
        }

    @staticmethod
    def get_batch_summaries(client_id: int) -> List[dict]:
        """Summarize every import batch for a client, newest import first.

        One row per import_batch: where it came from, which account it landed
        in, how many rows, what they total, and how far along posting is.
        Backs the Import History view.

        A batch can legitimately span several accounts or files (the
        "CSV contains transactions from multiple accounts" path), so the
        counts are reported alongside the representative value and callers
        should say "Multiple" when a count exceeds one.
        """
        query = """
            SELECT b.*,
                   a.name account_name,
                   a.account_number account_number
            FROM (
                SELECT it.import_batch,
                       MIN(it.source_filename) source_filename,
                       COUNT(DISTINCT it.source_filename) filename_count,
                       MIN(it.bank_account_id) bank_account_id,
                       COUNT(DISTINCT it.bank_account_id) account_count,
                       COUNT(*) row_count,
                       COALESCE(SUM(it.amount), 0) net_amount,
                       COALESCE(SUM(CASE WHEN it.amount > 0 THEN it.amount ELSE 0 END), 0) deposits,
                       COALESCE(SUM(CASE WHEN it.amount < 0 THEN it.amount ELSE 0 END), 0) withdrawals,
                       SUM(CASE WHEN it.status = 'Posted' THEN 1 ELSE 0 END) posted_count,
                       SUM(CASE WHEN it.status = 'Categorized' THEN 1 ELSE 0 END) categorized_count,
                       SUM(CASE WHEN it.status = 'Pending' THEN 1 ELSE 0 END) pending_count,
                       MIN(it.transaction_date) first_date,
                       MAX(it.transaction_date) last_date,
                       -- created_at is stored UTC (CURRENT_TIMESTAMP), so convert
                       -- for display or evening imports show tomorrow's date.
                       MIN(datetime(it.created_at, 'localtime')) imported_at,
                       MIN(it.created_at) sort_key
                FROM imported_transactions it
                WHERE it.client_id = ? AND it.import_batch IS NOT NULL
                GROUP BY it.import_batch
            ) b
            LEFT JOIN accounts a ON a.id = b.bank_account_id
            ORDER BY b.sort_key DESC, b.import_batch DESC
        """
        with get_cursor() as cursor:
            cursor.execute(query, (client_id,))
            rows = cursor.fetchall()

        return [{
            "import_batch": row["import_batch"],
            "source_filename": row["source_filename"],
            "filename_count": int(row["filename_count"] or 0),
            "bank_account_id": row["bank_account_id"],
            "account_count": int(row["account_count"] or 0),
            "account_name": row["account_name"],
            "account_number": row["account_number"],
            "row_count": int(row["row_count"] or 0),
            "net_amount": to_dollars(row["net_amount"] or 0),
            "deposits": to_dollars(row["deposits"] or 0),
            "withdrawals": to_dollars(row["withdrawals"] or 0),
            "posted_count": int(row["posted_count"] or 0),
            "categorized_count": int(row["categorized_count"] or 0),
            "pending_count": int(row["pending_count"] or 0),
            "first_date": date.fromisoformat(row["first_date"]) if row["first_date"] else None,
            "last_date": date.fromisoformat(row["last_date"]) if row["last_date"] else None,
            "imported_at": row["imported_at"],
        } for row in rows]

    @staticmethod
    def get_by_batch(client_id: int, batch_id: str) -> List['ImportedTransaction']:
        """Return one batch's rows in source-file order.

        Ordered by source_row_number so the list lines up line-for-line with
        the file that was uploaded — that alignment is what makes a
        source-vs-posted check readable. Rows imported before
        source_row_number existed have NULL and sort last, by id.
        """
        query = """
            SELECT it.*,
                   ba.name as bank_account_name,
                   sa.name as suggested_account_name,
                   NULL as reconciliation_id,
                   NULL as reconciliation_status,
                   NULL as statement_end_date
            FROM imported_transactions it
            LEFT JOIN accounts ba ON it.bank_account_id = ba.id
            LEFT JOIN accounts sa ON it.suggested_account_id = sa.id
            WHERE it.client_id = ? AND it.import_batch = ?
            ORDER BY it.source_row_number IS NULL, it.source_row_number, it.id
        """
        with get_cursor() as cursor:
            cursor.execute(query, (client_id, batch_id))
            rows = cursor.fetchall()

        return [ImportedTransaction(
            id=row['id'],
            client_id=row['client_id'],
            import_batch=row['import_batch'],
            transaction_date=date.fromisoformat(row['transaction_date']) if row['transaction_date'] else None,
            description=row['description'],
            amount=to_dollars(row['amount']),
            bank_account_id=row['bank_account_id'],
            suggested_account_id=row['suggested_account_id'],
            status=row['status'],
            journal_entry_id=row['journal_entry_id'],
            source_id=row['source_id'],
            source_filename=row['source_filename'],
            source_row_number=row['source_row_number'],
            row_fingerprint=row['row_fingerprint'],
            idempotency_key=row['idempotency_key'],
            duplicate_override=bool(row['duplicate_override']),
            duplicate_override_reason=row['duplicate_override_reason'],
            duplicate_of_id=row['duplicate_of_id'],
            bank_account_name=row['bank_account_name'],
            suggested_account_name=row['suggested_account_name'],
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
