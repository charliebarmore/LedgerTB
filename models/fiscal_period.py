import sqlite3
from dataclasses import dataclass
from typing import Optional, List
from datetime import date
from calendar import monthrange
from database.connection import get_connection, get_cursor


@dataclass
class FiscalPeriod:
    id: Optional[int] = None
    client_id: int = 0
    period_name: str = ""
    period_type: str = "Year"  # Year, Quarter, Month, Custom
    start_date: date = None
    end_date: date = None
    is_closed: bool = False

    def save(self) -> int:
        """Save the fiscal period to the database."""
        conn = get_connection()
        cursor = conn.cursor()

        try:
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
            else:
                cursor.execute(
                    """
                    UPDATE fiscal_periods
                    SET period_name = ?, period_type = ?, start_date = ?, end_date = ?, is_closed = ?
                    WHERE id = ?
                    """,
                    (self.period_name, self.period_type,
                     self.start_date.isoformat(), self.end_date.isoformat(),
                     1 if self.is_closed else 0, self.id)
                )
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

        return self.id

    @staticmethod
    def set_closed(period_id: int, is_closed: bool):
        """Close or reopen a fiscal period."""
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE fiscal_periods SET is_closed = ? WHERE id = ?",
                (1 if is_closed else 0, period_id)
            )
            conn.commit()
        finally:
            conn.close()

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
    def delete(period_id: int):
        """Delete a fiscal period."""
        with get_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM fiscal_periods WHERE id = ?", (period_id,))

    @staticmethod
    def generate_periods(client_id: int, year: int, fiscal_year_end_month: int = 12) -> List['FiscalPeriod']:
        """
        Generate fiscal periods for a given year based on the client's fiscal year end month.
        Creates Year, Quarter, and Month periods.

        Args:
            client_id: The client ID
            year: The calendar year
            fiscal_year_end_month: The month the fiscal year ends (1-12, default 12 for December)

        Returns:
            List of created FiscalPeriod objects
        """
        periods = []

        # Delete existing periods for this year to avoid duplicates
        # Check if periods already exist
        with get_cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*) as cnt FROM fiscal_periods
                WHERE client_id = ? AND period_name LIKE ?
            """, (client_id, f"FY {year}%"))
            cnt = cursor.fetchone()['cnt']

        if cnt > 0:
            # Return existing periods
            return FiscalPeriod.get_all(client_id)

        # Calculate fiscal year start and end
        if fiscal_year_end_month == 12:
            # Calendar year fiscal year
            fy_start = date(year, 1, 1)
            fy_end = date(year, 12, 31)
        else:
            # Fiscal year ends in a different month
            # FY 2024 ending June 2024 starts July 2023
            fy_start = date(year - 1, fiscal_year_end_month + 1, 1)
            fy_end = date(year, fiscal_year_end_month, monthrange(year, fiscal_year_end_month)[1])

        # Create Year period
        year_period = FiscalPeriod(
            client_id=client_id,
            period_name=f"FY {year}",
            period_type="Year",
            start_date=fy_start,
            end_date=fy_end,
            is_closed=False
        )
        year_period.save()
        periods.append(year_period)

        # Create Quarter periods
        quarter_names = ["Q1", "Q2", "Q3", "Q4"]
        for q_idx in range(4):
            q_start_month = (fiscal_year_end_month % 12) + 1 + (q_idx * 3)
            q_start_year = year - 1 if q_start_month <= fiscal_year_end_month and fiscal_year_end_month != 12 else year

            if fiscal_year_end_month == 12:
                # Calendar year quarters
                q_start_month = 1 + (q_idx * 3)
                q_start_year = year
                q_end_month = q_start_month + 2
                q_end_year = year
            else:
                # Calculate for non-calendar fiscal year
                q_start_month = ((fiscal_year_end_month % 12) + 1 + (q_idx * 3))
                if q_start_month > 12:
                    q_start_month -= 12
                    q_start_year = year
                else:
                    q_start_year = year - 1

                q_end_month = q_start_month + 2
                q_end_year = q_start_year
                if q_end_month > 12:
                    q_end_month -= 12
                    q_end_year += 1

            if fiscal_year_end_month == 12:
                q_start = date(q_start_year, q_start_month, 1)
                q_end = date(q_end_year, q_end_month, monthrange(q_end_year, q_end_month)[1])
            else:
                q_start = date(q_start_year, q_start_month, 1)
                q_end = date(q_end_year, q_end_month, monthrange(q_end_year, q_end_month)[1])

            quarter_period = FiscalPeriod(
                client_id=client_id,
                period_name=f"FY {year} - {quarter_names[q_idx]}",
                period_type="Quarter",
                start_date=q_start,
                end_date=q_end,
                is_closed=False
            )
            quarter_period.save()
            periods.append(quarter_period)

        # Create Month periods
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

        if fiscal_year_end_month == 12:
            # Calendar year - months are simple
            for m in range(1, 13):
                m_start = date(year, m, 1)
                m_end = date(year, m, monthrange(year, m)[1])

                month_period = FiscalPeriod(
                    client_id=client_id,
                    period_name=f"FY {year} - {month_names[m-1]}",
                    period_type="Month",
                    start_date=m_start,
                    end_date=m_end,
                    is_closed=False
                )
                month_period.save()
                periods.append(month_period)
        else:
            # Non-calendar fiscal year
            current_month = fiscal_year_end_month + 1
            current_year = year - 1
            if current_month > 12:
                current_month = 1
                current_year = year

            for _ in range(12):
                m_start = date(current_year, current_month, 1)
                m_end = date(current_year, current_month, monthrange(current_year, current_month)[1])

                month_period = FiscalPeriod(
                    client_id=client_id,
                    period_name=f"FY {year} - {month_names[current_month-1]}",
                    period_type="Month",
                    start_date=m_start,
                    end_date=m_end,
                    is_closed=False
                )
                month_period.save()
                periods.append(month_period)

                current_month += 1
                if current_month > 12:
                    current_month = 1
                    current_year += 1

        return periods

    @staticmethod
    def ensure_periods_exist(client_id: int, year: int, fiscal_year_end_month: int = 12) -> List['FiscalPeriod']:
        """
        Ensure fiscal periods exist for a given year, creating them if needed.

        Args:
            client_id: The client ID
            year: The calendar year
            fiscal_year_end_month: The month the fiscal year ends

        Returns:
            List of FiscalPeriod objects for the year
        """
        # Check if periods exist for this year
        with get_cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*) as cnt FROM fiscal_periods
                WHERE client_id = ? AND period_name LIKE ?
            """, (client_id, f"FY {year}%"))

            count = cursor.fetchone()['cnt']

        if count == 0:
            return FiscalPeriod.generate_periods(client_id, year, fiscal_year_end_month)

        return FiscalPeriod.get_all(client_id)
