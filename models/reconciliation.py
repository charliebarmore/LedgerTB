"""Bank and credit-card statement reconciliation domain model."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from database.connection import get_connection, get_cursor
from models.audit_log import AuditLog
from money import to_cents, to_dollars
from utils.fiscal_dates import fiscal_year_bounds


@dataclass
class ReconciliationLine:
    line_id: int
    entry_id: int
    entry_date: date
    description: str
    source_reference: Optional[str]
    debit: float
    credit: float
    amount: float
    selected: bool = False


@dataclass
class BankReconciliation:
    id: Optional[int] = None
    client_id: int = 0
    account_id: int = 0
    statement_start_date: Optional[date] = None
    statement_end_date: Optional[date] = None
    statement_ending_balance: float = 0.0
    status: str = "Draft"
    completed_at: Optional[datetime] = None
    account_name: Optional[str] = None
    account_number: Optional[str] = None
    account_type: Optional[str] = None

    @staticmethod
    def _from_row(row) -> "BankReconciliation":
        return BankReconciliation(
            id=row["id"], client_id=row["client_id"], account_id=row["account_id"],
            statement_start_date=date.fromisoformat(row["statement_start_date"]),
            statement_end_date=date.fromisoformat(row["statement_end_date"]),
            statement_ending_balance=to_dollars(row["statement_ending_balance"]),
            status=row["status"],
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            account_name=row["account_name"] if "account_name" in row.keys() else None,
            account_number=row["account_number"] if "account_number" in row.keys() else None,
            account_type=row["account_type"] if "account_type" in row.keys() else None,
        )

    @staticmethod
    def _validate_account(cursor, client_id: int, account_id: int):
        cursor.execute(
            "SELECT id, type FROM accounts WHERE id = ? AND client_id = ?",
            (account_id, client_id),
        )
        account = cursor.fetchone()
        if not account:
            raise ValueError("The selected account does not belong to this client.")
        if account["type"] not in ("Asset", "Liability"):
            raise ValueError("Only asset and liability accounts can be reconciled.")
        return account["type"]

    @staticmethod
    def _validate_dates(start_date: date, end_date: date):
        if not start_date or not end_date:
            raise ValueError("Statement start and end dates are required.")
        if start_date > end_date:
            raise ValueError("Statement start date cannot be after the end date.")

    @staticmethod
    def _audit(cursor, client_id, record_id, action, old_values=None, new_values=None):
        """Write the reconciliation audit row in the same transaction."""
        AuditLog.write(
            cursor, client_id, "bank_reconciliations", record_id, action,
            old_values=old_values, new_values=new_values,
        )

    @classmethod
    def create(cls, client_id, account_id, start_date, end_date, ending_balance):
        cls._validate_dates(start_date, end_date)
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cls._validate_account(cursor, client_id, account_id)
            cursor.execute(
                """
                SELECT id FROM bank_reconciliations
                WHERE client_id = ? AND account_id = ? AND status = 'Draft'
                """,
                (client_id, account_id),
            )
            if cursor.fetchone():
                raise ValueError("This account already has a draft reconciliation.")
            cursor.execute(
                """
                SELECT id FROM bank_reconciliations
                WHERE client_id = ? AND account_id = ?
                  AND NOT(statement_end_date < ? OR statement_start_date > ?)
                """,
                (client_id, account_id, start_date.isoformat(), end_date.isoformat()),
            )
            if cursor.fetchone():
                raise ValueError("This statement period overlaps an existing reconciliation.")
            cursor.execute(
                """
                INSERT INTO bank_reconciliations
                    (client_id, account_id, statement_start_date, statement_end_date,
                     statement_ending_balance)
                VALUES (?, ?, ?, ?, ?)
                """,
                (client_id, account_id, start_date.isoformat(), end_date.isoformat(),
                 to_cents(ending_balance)),
            )
            reconciliation_id = cursor.lastrowid
            cls._audit(cursor, client_id, reconciliation_id, "INSERT", new_values={
                "account_id": account_id,
                "statement_start_date": start_date.isoformat(),
                "statement_end_date": end_date.isoformat(),
                "statement_ending_balance": ending_balance,
                "status": "Draft",
            })
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return cls.get_by_id(reconciliation_id, client_id)

    @classmethod
    def get_by_id(cls, reconciliation_id: int, client_id: int):
        with get_cursor() as cursor:
            cursor.execute(
                """
                SELECT br.*, a.name account_name, a.account_number, a.type account_type
                FROM bank_reconciliations br
                JOIN accounts a ON a.id = br.account_id
                WHERE br.id = ? AND br.client_id = ?
                """,
                (reconciliation_id, client_id),
            )
            row = cursor.fetchone()
        return cls._from_row(row) if row else None

    @classmethod
    def get_draft(cls, client_id: int, account_id: int):
        with get_cursor() as cursor:
            cursor.execute(
                """
                SELECT br.*, a.name account_name, a.account_number, a.type account_type
                FROM bank_reconciliations br
                JOIN accounts a ON a.id = br.account_id
                WHERE br.client_id = ? AND br.account_id = ? AND br.status = 'Draft'
                """,
                (client_id, account_id),
            )
            row = cursor.fetchone()
        return cls._from_row(row) if row else None

    @classmethod
    def get_all(cls, client_id: int, account_id: Optional[int] = None):
        query = """
            SELECT br.*, a.name account_name, a.account_number, a.type account_type
            FROM bank_reconciliations br
            JOIN accounts a ON a.id = br.account_id
            WHERE br.client_id = ?
        """
        params = [client_id]
        if account_id:
            query += " AND br.account_id = ?"
            params.append(account_id)
        query += " ORDER BY br.statement_end_date DESC, br.id DESC"
        with get_cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
        return [cls._from_row(row) for row in rows]

    @classmethod
    def suggested_start_date(cls, client_id: int, account_id: int):
        with get_cursor() as cursor:
            cursor.execute(
                """
                SELECT MAX(statement_end_date) latest
                FROM bank_reconciliations
                WHERE client_id = ? AND account_id = ? AND status = 'Completed'
                """,
                (client_id, account_id),
            )
            latest = cursor.fetchone()["latest"]
            cursor.execute(
                "SELECT fiscal_year_end_month FROM clients WHERE id = ?",
                (client_id,),
            )
            client = cursor.fetchone()
        if latest:
            return date.fromisoformat(latest) + timedelta(days=1)
        fiscal_year_end_month = client["fiscal_year_end_month"] if client else 12
        return fiscal_year_bounds(date.today(), fiscal_year_end_month)[0]

    def update_statement(self, start_date, end_date, ending_balance):
        self._validate_dates(start_date, end_date)
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM bank_reconciliations WHERE id = ? AND client_id = ?",
                (self.id, self.client_id),
            )
            old = cursor.fetchone()
            if not old or old["status"] != "Draft":
                raise ValueError("Only a draft reconciliation can be edited.")
            cursor.execute(
                """
                SELECT id FROM bank_reconciliations
                WHERE client_id = ? AND account_id = ? AND id != ?
                  AND NOT(statement_end_date < ? OR statement_start_date > ?)
                """,
                (self.client_id, self.account_id, self.id,
                 start_date.isoformat(), end_date.isoformat()),
            )
            if cursor.fetchone():
                raise ValueError("This statement period overlaps an existing reconciliation.")
            # Lines after a shortened statement end are no longer eligible.
            cursor.execute(
                """
                DELETE FROM bank_reconciliation_items
                WHERE reconciliation_id = ? AND journal_entry_line_id IN (
                    SELECT jel.id FROM journal_entry_lines jel
                    JOIN journal_entries je ON je.id = jel.journal_entry_id
                    WHERE je.entry_date > ?
                )
                """,
                (self.id, end_date.isoformat()),
            )
            cursor.execute(
                """
                UPDATE bank_reconciliations
                SET statement_start_date = ?, statement_end_date = ?,
                    statement_ending_balance = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND client_id = ?
                """,
                (start_date.isoformat(), end_date.isoformat(), to_cents(ending_balance),
                 self.id, self.client_id),
            )
            self._audit(cursor, self.client_id, self.id, "UPDATE", old_values={
                "statement_start_date": old["statement_start_date"],
                "statement_end_date": old["statement_end_date"],
                "statement_ending_balance": to_dollars(old["statement_ending_balance"]),
            }, new_values={
                "statement_start_date": start_date.isoformat(),
                "statement_end_date": end_date.isoformat(),
                "statement_ending_balance": ending_balance,
            })
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def lines(self):
        with get_cursor() as cursor:
            cursor.execute(
                """
                SELECT jel.id line_id, je.id entry_id, je.entry_date, je.description,
                       je.source_reference, jel.debit, jel.credit,
                       CASE WHEN current_item.id IS NULL THEN 0 ELSE 1 END selected
                FROM journal_entry_lines jel
                JOIN journal_entries je ON je.id = jel.journal_entry_id
                LEFT JOIN bank_reconciliation_items any_item
                    ON any_item.journal_entry_line_id = jel.id
                LEFT JOIN bank_reconciliation_items current_item
                    ON current_item.journal_entry_line_id = jel.id
                   AND current_item.reconciliation_id = ?
                WHERE je.client_id = ? AND jel.account_id = ? AND je.entry_date <= ?
                  AND (any_item.id IS NULL OR current_item.id IS NOT NULL)
                ORDER BY je.entry_date, je.id, jel.id
                """,
                (self.id, self.client_id, self.account_id, self.statement_end_date.isoformat()),
            )
            rows = cursor.fetchall()
        liability = self.account_type == "Liability"
        return [ReconciliationLine(
            line_id=row["line_id"], entry_id=row["entry_id"],
            entry_date=date.fromisoformat(row["entry_date"]),
            description=row["description"] or "", source_reference=row["source_reference"],
            debit=to_dollars(row["debit"]), credit=to_dollars(row["credit"]),
            amount=to_dollars(row["credit"] - row["debit"] if liability else row["debit"] - row["credit"]),
            selected=bool(row["selected"]),
        ) for row in rows]

    def save_selected_lines(self, line_ids):
        if self.status != "Draft":
            raise ValueError("Completed reconciliations cannot be changed. Reopen it first.")
        requested = {int(line_id) for line_id in line_ids}
        eligible = {line.line_id for line in self.lines()}
        if not requested.issubset(eligible):
            raise ValueError("One or more selected entries are not eligible for this reconciliation.")
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status FROM bank_reconciliations WHERE id = ? AND client_id = ?",
                (self.id, self.client_id),
            )
            row = cursor.fetchone()
            if not row or row["status"] != "Draft":
                raise ValueError("Only a draft reconciliation can be changed.")
            cursor.execute(
                """
                SELECT journal_entry_line_id
                FROM bank_reconciliation_items
                WHERE reconciliation_id = ?
                ORDER BY journal_entry_line_id
                """,
                (self.id,),
            )
            previous = [item["journal_entry_line_id"] for item in cursor.fetchall()]
            cursor.execute("DELETE FROM bank_reconciliation_items WHERE reconciliation_id = ?", (self.id,))
            cursor.executemany(
                "INSERT INTO bank_reconciliation_items (reconciliation_id, journal_entry_line_id) VALUES (?, ?)",
                [(self.id, line_id) for line_id in sorted(requested)],
            )
            cursor.execute(
                "UPDATE bank_reconciliations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (self.id,),
            )
            selected = sorted(requested)
            if previous != selected:
                self._audit(
                    cursor, self.client_id, self.id, "UPDATE",
                    old_values={"cleared_line_ids": previous, "cleared_line_count": len(previous)},
                    new_values={"cleared_line_ids": selected, "cleared_line_count": len(selected)},
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _balance_cents(self, selection="cleared"):
        sign = "jel.credit - jel.debit" if self.account_type == "Liability" else "jel.debit - jel.credit"
        if selection == "ledger":
            query = f"""
                SELECT COALESCE(SUM({sign}), 0) balance
                FROM journal_entry_lines jel
                JOIN journal_entries je ON je.id = jel.journal_entry_id
                WHERE je.client_id = ? AND jel.account_id = ? AND je.entry_date <= ?
            """
            params = (self.client_id, self.account_id, self.statement_end_date.isoformat())
        else:
            query = f"""
                SELECT COALESCE(SUM({sign}), 0) balance
                FROM journal_entry_lines jel
                JOIN journal_entries je ON je.id = jel.journal_entry_id
                JOIN bank_reconciliation_items bri ON bri.journal_entry_line_id = jel.id
                JOIN bank_reconciliations br ON br.id = bri.reconciliation_id
                WHERE je.client_id = ? AND jel.account_id = ? AND je.entry_date <= ?
                  AND (br.status = 'Completed' OR br.id = ?)
            """
            params = (self.client_id, self.account_id, self.statement_end_date.isoformat(), self.id)
        with get_cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchone()["balance"]

    def cleared_balance(self):
        return to_dollars(self._balance_cents("cleared"))

    def ledger_balance(self):
        return to_dollars(self._balance_cents("ledger"))

    def difference(self):
        return to_dollars(to_cents(self.statement_ending_balance) - self._balance_cents("cleared"))

    def complete(self):
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM bank_reconciliations WHERE id = ? AND client_id = ?",
                (self.id, self.client_id),
            )
            row = cursor.fetchone()
            if not row or row["status"] != "Draft":
                raise ValueError("Only a draft reconciliation can be completed.")
            # Recompute inside this transaction so completion cannot race a stale UI value.
            sign = "jel.credit - jel.debit" if self.account_type == "Liability" else "jel.debit - jel.credit"
            cursor.execute(f"""
                SELECT COALESCE(SUM({sign}), 0) balance
                FROM journal_entry_lines jel
                JOIN journal_entries je ON je.id = jel.journal_entry_id
                JOIN bank_reconciliation_items bri ON bri.journal_entry_line_id = jel.id
                JOIN bank_reconciliations br ON br.id = bri.reconciliation_id
                WHERE je.client_id = ? AND jel.account_id = ? AND je.entry_date <= ?
                  AND (br.status = 'Completed' OR br.id = ?)
            """, (self.client_id, self.account_id, self.statement_end_date.isoformat(), self.id))
            cleared_cents = cursor.fetchone()["balance"]
            if cleared_cents != row["statement_ending_balance"]:
                raise ValueError(
                    f"Reconciliation is out of balance by "
                    f"${abs(to_dollars(row['statement_ending_balance'] - cleared_cents)):,.2f}."
                )
            cursor.execute(
                """
                UPDATE bank_reconciliations
                SET status = 'Completed', completed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (self.id,),
            )
            self._audit(cursor, self.client_id, self.id, "CLOSE",
                        old_values={"status": "Draft"},
                        new_values={"status": "Completed", "cleared_balance": to_dollars(cleared_cents)})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def reopen(self):
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM bank_reconciliations WHERE id = ? AND client_id = ?",
                (self.id, self.client_id),
            )
            row = cursor.fetchone()
            if not row or row["status"] != "Completed":
                raise ValueError("Only a completed reconciliation can be reopened.")
            cursor.execute(
                """
                SELECT id FROM bank_reconciliations
                WHERE client_id = ? AND account_id = ? AND id != ?
                  AND (status = 'Draft' OR statement_end_date > ?)
                """,
                (self.client_id, self.account_id, self.id, self.statement_end_date.isoformat()),
            )
            if cursor.fetchone():
                raise ValueError("Reopen newer reconciliations first.")
            cursor.execute(
                """
                UPDATE bank_reconciliations
                SET status = 'Draft', completed_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (self.id,),
            )
            self._audit(cursor, self.client_id, self.id, "REOPEN",
                        old_values={"status": "Completed"}, new_values={"status": "Draft"})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
