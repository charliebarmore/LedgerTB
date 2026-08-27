from dataclasses import dataclass
from typing import Optional, List
from datetime import date
from calendar import monthrange
from database.connection import get_connection, get_cursor
from models.audit_log import AuditLog
from money import to_dollars
from utils.fiscal_dates import require_valid_range


@dataclass
class FiscalPeriod:
    id: Optional[int] = None
    client_id: int = 0
    period_name: str = ""
    period_type: str = "Year"  # Year, Quarter, Month, Custom
    start_date: date = None
    end_date: date = None
    is_closed: bool = False

    def save(self, conn=None) -> int:
        """Save the period, optionally in the caller's transaction."""
        require_valid_range(self.start_date, self.end_date, "Fiscal period")
        owns_conn = conn is None
        if owns_conn:
            conn = get_connection()
        try:
            cursor = conn.cursor()
            if self.id is None:
                cursor.execute(
                    """
                    INSERT INTO fiscal_periods (client_id, period_name, period_type, start_date, end_date, is_closed)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (self.client_id, self.period_name, self.period_type,
                     self.start_date.isoformat(), self.end_date.isoformat(),
                     1 if self.is_closed else 0)
                )
                self.id = cursor.lastrowid
                AuditLog.write(
                    cursor, self.client_id, "fiscal_periods", self.id, "INSERT",
                    new_values={
                        "period_name": self.period_name,
                        "period_type": self.period_type,
                        "start_date": self.start_date,
                        "end_date": self.end_date,
                        "is_closed": self.is_closed,
                    },
                )
            else:
                cursor.execute(
                    "SELECT * FROM fiscal_periods WHERE id = ? AND client_id = ?",
                    (self.id, self.client_id),
                )
                old = cursor.fetchone()
                if not old:
                    raise ValueError("Fiscal period not found for this client.")
                cursor.execute(
                    """
                    UPDATE fiscal_periods
                    SET period_name = ?, period_type = ?, start_date = ?, end_date = ?, is_closed = ?
                    WHERE id = ? AND client_id = ?
                    """,
                    (self.period_name, self.period_type,
                     self.start_date.isoformat(), self.end_date.isoformat(),
                     1 if self.is_closed else 0, self.id, self.client_id)
                )
                AuditLog.write(
                    cursor, self.client_id, "fiscal_periods", self.id, "UPDATE",
                    old_values={
                        "period_name": old["period_name"],
                        "period_type": old["period_type"],
                        "start_date": old["start_date"],
                        "end_date": old["end_date"],
                        "is_closed": bool(old["is_closed"]),
                    },
                    new_values={
                        "period_name": self.period_name,
                        "period_type": self.period_type,
                        "start_date": self.start_date,
                        "end_date": self.end_date,
                        "is_closed": self.is_closed,
                    },
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
    def set_closed(
        period_id: int,
        is_closed: bool,
        client_id: Optional[int] = None,
        confirmation: Optional[dict] = None,
    ):
        """Close or reopen a fiscal period."""
        with get_cursor(commit=True) as cursor:
            query = "SELECT * FROM fiscal_periods WHERE id = ?"
            params = [period_id]
            if client_id is not None:
                query += " AND client_id = ?"
                params.append(client_id)
            cursor.execute(query, params)
            period = cursor.fetchone()
            if not period:
                raise ValueError("Fiscal period not found for this client.")

            if bool(period["is_closed"]) == bool(is_closed):
                return

            checklist = None
            if is_closed and period["period_type"] == "Year":
                checklist = FiscalPeriod._close_checklist(
                    cursor, period["client_id"], period["start_date"], period["end_date"]
                )
                if not confirmation or not confirmation.get("explicit_confirmation"):
                    raise ValueError("Explicit year-close confirmation is required.")
                if not checklist["trial_balance_balanced"]:
                    raise ValueError("The fiscal year cannot close while the trial balance is out of balance.")
                if checklist["warning_count"] and not confirmation.get("warnings_acknowledged"):
                    raise ValueError(
                        "Review and acknowledge the outstanding close-checklist warnings first."
                    )
                if (checklist["close_map_incomplete"] and
                        not str(confirmation.get("close_map_exception_reason", "")).strip()):
                    raise ValueError(
                        "Explain why the fiscal year is being closed with incomplete "
                        "Close Map reviews."
                    )
            cursor.execute(
                "UPDATE fiscal_periods SET is_closed = ? WHERE id = ? AND client_id = ?",
                (1 if is_closed else 0, period_id, period["client_id"])
            )
            AuditLog.write(
                cursor,
                period["client_id"],
                "fiscal_periods",
                period_id,
                "CLOSE" if is_closed else "REOPEN",
                old_values={
                    "period_name": period["period_name"],
                    "start_date": period["start_date"],
                    "end_date": period["end_date"],
                    "is_closed": bool(period["is_closed"]),
                },
                new_values={
                    "period_name": period["period_name"],
                    "start_date": period["start_date"],
                    "end_date": period["end_date"],
                    "is_closed": bool(is_closed),
                    "close_checklist": checklist,
                    "explicit_confirmation": bool(
                        confirmation and confirmation.get("explicit_confirmation")
                    ),
                    "warnings_acknowledged": bool(
                        confirmation and confirmation.get("warnings_acknowledged")
                    ),
                    "close_map_exception_reason": (
                        str(confirmation.get("close_map_exception_reason", "")).strip()
                        if confirmation else ""
                    ),
                },
            )

    @staticmethod
    def _close_checklist(cursor, client_id: int, start_date, end_date) -> dict:
        """Compute close controls using the caller's transaction."""
        start = start_date.isoformat() if hasattr(start_date, "isoformat") else str(start_date)
        end = end_date.isoformat() if hasattr(end_date, "isoformat") else str(end_date)
        cursor.execute(
            """
            SELECT COUNT(DISTINCT je.id) entry_count,
                   COALESCE(SUM(jel.debit), 0) total_debits,
                   COALESCE(SUM(jel.credit), 0) total_credits
            FROM journal_entries je
            LEFT JOIN journal_entry_lines jel ON jel.journal_entry_id = je.id
            WHERE je.client_id = ? AND je.entry_date BETWEEN ? AND ?
            """,
            (client_id, start, end),
        )
        trial_balance = cursor.fetchone()
        cursor.execute(
            """
            SELECT SUM(CASE WHEN status = 'Pending' AND dismissed_at IS NULL
                            THEN 1 ELSE 0 END) pending_imports,
                   SUM(CASE WHEN suggested_account_id IS NULL AND status != 'Posted'
                                  AND dismissed_at IS NULL
                            THEN 1 ELSE 0 END) uncategorized_items
            FROM imported_transactions
            WHERE client_id = ? AND transaction_date BETWEEN ? AND ?
            """,
            (client_id, start, end),
        )
        imports = cursor.fetchone()
        cursor.execute(
            """
            SELECT COUNT(*) unresolved_duplicates
            FROM (
                SELECT duplicate_override,
                       ROW_NUMBER() OVER (
                           PARTITION BY transaction_date, amount, UPPER(TRIM(description)),
                                        COALESCE(bank_account_id, -1)
                           ORDER BY id
                       ) duplicate_number
                FROM imported_transactions
                WHERE client_id = ? AND transaction_date BETWEEN ? AND ?
                  AND dismissed_at IS NULL
            ) duplicates
            WHERE duplicate_number > 1 AND duplicate_override = 0
            """,
            (client_id, start, end),
        )
        duplicates = int(cursor.fetchone()["unresolved_duplicates"] or 0)
        debits = int(trial_balance["total_debits"] or 0)
        credits = int(trial_balance["total_credits"] or 0)
        pending = int(imports["pending_imports"] or 0)
        uncategorized = int(imports["uncategorized_items"] or 0)
        period_row = cursor.execute(
            "SELECT id FROM fiscal_periods WHERE client_id = ? AND period_type = 'Year' "
            "AND start_date = ? AND end_date = ? ORDER BY id DESC LIMIT 1",
            (client_id, start, end),
        ).fetchone()
        close_map = {
            "required_count": 0, "reviewed_count": 0,
            "incomplete_count": 0, "ready": True, "counts": {},
        }
        if period_row:
            # Use the caller's transaction so the readiness recorded in the
            # close audit event describes the same committed ledger snapshot.
            from models.close_map import _readiness_with_cursor
            close_map = _readiness_with_cursor(cursor, client_id, period_row["id"])
        warning_count = pending + uncategorized + duplicates + close_map["incomplete_count"]
        return {
            "period_start": start,
            "period_end": end,
            "entry_count": int(trial_balance["entry_count"] or 0),
            "total_debits": to_dollars(debits),
            "total_credits": to_dollars(credits),
            "trial_balance_balanced": debits == credits,
            "pending_imports": pending,
            "uncategorized_items": uncategorized,
            "unresolved_duplicates": duplicates,
            "close_map_required": close_map["required_count"],
            "close_map_reviewed": close_map["reviewed_count"],
            "close_map_incomplete": close_map["incomplete_count"],
            "close_map_ready": close_map["ready"],
            "close_map_status_counts": close_map["counts"],
            "warning_count": warning_count,
        }

    @staticmethod
    def get_close_checklist(period_id: int, client_id: int) -> dict:
        """Return the current close checklist for a client-owned year period."""
        with get_cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM fiscal_periods
                WHERE id = ? AND client_id = ? AND period_type = 'Year'
                """,
                (period_id, client_id),
            )
            period = cursor.fetchone()
            if not period:
                raise ValueError("Fiscal year not found for this client.")
            return FiscalPeriod._close_checklist(
                cursor, client_id, period["start_date"], period["end_date"]
            )

    @staticmethod
    def get_closed_period_for_date(client_id: int, entry_date: date) -> Optional['FiscalPeriod']:
        """Return the CLOSED 'Year' period that contains entry_date, if any.

        Used to lock journal-entry posting/editing/deletion within a closed
        fiscal year. Only Year-type periods gate edits; Quarter/Month periods
        are reporting views and do not lock.
        """
        with get_cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM fiscal_periods
                WHERE client_id = ? AND is_closed = 1 AND period_type = 'Year'
                  AND start_date <= ? AND end_date >= ?
                ORDER BY end_date DESC
                LIMIT 1
                """,
                (client_id, entry_date.isoformat(), entry_date.isoformat())
            )
            row = cursor.fetchone()

        if not row:
            return None

        return FiscalPeriod(
            id=row['id'],
            client_id=row['client_id'],
            period_name=row['period_name'],
            period_type=row['period_type'],
            start_date=date.fromisoformat(row['start_date']),
            end_date=date.fromisoformat(row['end_date']),
            is_closed=bool(row['is_closed'])
        )

    @staticmethod
    def get_by_id(period_id: int) -> Optional['FiscalPeriod']:
        """Get a fiscal period by ID."""
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM fiscal_periods WHERE id = ?", (period_id,))
            row = cursor.fetchone()

        if not row:
            return None

        period = FiscalPeriod(
            id=row['id'],
            client_id=row['client_id'],
            period_name=row['period_name'],
            period_type=row['period_type'],
            start_date=date.fromisoformat(row['start_date']),
            end_date=date.fromisoformat(row['end_date']),
            is_closed=bool(row['is_closed'])
        )

        return period

    @staticmethod
    def get_all(client_id: int, period_type: Optional[str] = None) -> List['FiscalPeriod']:
        """Get all fiscal periods for a client, optionally filtered by type."""
        query = "SELECT * FROM fiscal_periods WHERE client_id = ?"
        params = [client_id]

        if period_type:
            query += " AND period_type = ?"
            params.append(period_type)

        query += " ORDER BY start_date DESC"

        with get_cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        periods = []
        for row in rows:
            periods.append(FiscalPeriod(
                id=row['id'],
                client_id=row['client_id'],
                period_name=row['period_name'],
                period_type=row['period_type'],
                start_date=date.fromisoformat(row['start_date']),
                end_date=date.fromisoformat(row['end_date']),
                is_closed=bool(row['is_closed'])
            ))

        return periods

    @staticmethod
    def get_current(client_id: int) -> Optional['FiscalPeriod']:
        """Get the current open period for a client (most recent non-closed period)."""
        with get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM fiscal_periods
                WHERE client_id = ? AND is_closed = 0
                ORDER BY end_date DESC
                LIMIT 1
            """, (client_id,))

            row = cursor.fetchone()

        if not row:
            return None

        period = FiscalPeriod(
            id=row['id'],
            client_id=row['client_id'],
            period_name=row['period_name'],
            period_type=row['period_type'],
            start_date=date.fromisoformat(row['start_date']),
            end_date=date.fromisoformat(row['end_date']),
            is_closed=bool(row['is_closed'])
        )

        return period

    @staticmethod
    def delete(period_id: int, client_id: Optional[int] = None):
        """Delete a fiscal period."""
        with get_cursor(commit=True) as cursor:
            query = "SELECT * FROM fiscal_periods WHERE id = ?"
            params = [period_id]
            if client_id is not None:
                query += " AND client_id = ?"
                params.append(client_id)
            cursor.execute(query, params)
            period = cursor.fetchone()
            if not period:
                raise ValueError("Fiscal period not found for this client.")
            cursor.execute(
                "DELETE FROM fiscal_periods WHERE id = ? AND client_id = ?",
                (period_id, period["client_id"]),
            )
            AuditLog.write(
                cursor, period["client_id"], "fiscal_periods", period_id, "DELETE",
                old_values={
                    "period_name": period["period_name"],
                    "period_type": period["period_type"],
                    "start_date": period["start_date"],
                    "end_date": period["end_date"],
                    "is_closed": bool(period["is_closed"]),
                },
            )

    @staticmethod
    def _calendar(client_id: int, year: int,
                  fiscal_year_end_month: int) -> List['FiscalPeriod']:
        """Build the canonical Year + 4 Quarters + 12 Months in memory."""
        end_month = int(fiscal_year_end_month)
        if not 1 <= end_month <= 12:
            raise ValueError("Fiscal year-end month must be between 1 and 12.")

        if end_month == 12:
            fy_start = date(year, 1, 1)
        else:
            fy_start = date(year - 1, end_month + 1, 1)
        fy_end = date(year, end_month, monthrange(year, end_month)[1])

        def month_at(offset: int) -> tuple[int, int]:
            absolute = fy_start.year * 12 + fy_start.month - 1 + offset
            return absolute // 12, absolute % 12 + 1

        periods = [FiscalPeriod(
            client_id=client_id, period_name=f"FY {year}", period_type="Year",
            start_date=fy_start, end_date=fy_end, is_closed=False,
        )]
        for quarter in range(4):
            start_year, start_month = month_at(quarter * 3)
            end_year, quarter_end_month = month_at(quarter * 3 + 2)
            periods.append(FiscalPeriod(
                client_id=client_id,
                period_name=f"FY {year} - Q{quarter + 1}",
                period_type="Quarter",
                start_date=date(start_year, start_month, 1),
                end_date=date(
                    end_year, quarter_end_month,
                    monthrange(end_year, quarter_end_month)[1],
                ),
                is_closed=False,
            ))

        month_names = [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        ]
        for offset in range(12):
            month_year, month = month_at(offset)
            periods.append(FiscalPeriod(
                client_id=client_id,
                period_name=f"FY {year} - {month_names[month - 1]}",
                period_type="Month",
                start_date=date(month_year, month, 1),
                end_date=date(
                    month_year, month, monthrange(month_year, month)[1]
                ),
                is_closed=False,
            ))
        return periods

    @staticmethod
    def generate_periods(client_id: int, year: int,
                         fiscal_year_end_month: int = 12,
                         conn=None) -> List['FiscalPeriod']:
        """Atomically create or repair the canonical calendar for one FY."""
        expected = FiscalPeriod._calendar(
            client_id, int(year), fiscal_year_end_month
        )
        label = f"FY {int(year)}"
        owns_conn = conn is None
        if owns_conn:
            conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT * FROM fiscal_periods
                   WHERE client_id = ?
                     AND (period_name = ? OR period_name LIKE ?)
                   ORDER BY id""",
                (client_id, label, f"{label} - %"),
            )
            existing = {}
            for row in cursor.fetchall():
                existing.setdefault(row["period_name"], row)

            complete = []
            for period in expected:
                row = existing.get(period.period_name)
                if row:
                    complete.append(FiscalPeriod(
                        id=row["id"], client_id=row["client_id"],
                        period_name=row["period_name"],
                        period_type=row["period_type"],
                        start_date=date.fromisoformat(row["start_date"]),
                        end_date=date.fromisoformat(row["end_date"]),
                        is_closed=bool(row["is_closed"]),
                    ))
                else:
                    period.save(conn=conn)
                    complete.append(period)
            if owns_conn:
                conn.commit()
            return complete
        except Exception:
            if owns_conn:
                conn.rollback()
            raise
        finally:
            if owns_conn:
                conn.close()

    @staticmethod
    def ensure_periods_exist(client_id: int, year: int,
                             fiscal_year_end_month: int = 12,
                             conn=None) -> List['FiscalPeriod']:
        """Idempotently create missing pieces of one canonical FY calendar."""
        return FiscalPeriod.generate_periods(
            client_id, year, fiscal_year_end_month, conn=conn
        )
