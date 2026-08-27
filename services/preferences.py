"""Book-level display preferences used throughout the desktop app."""

from dataclasses import dataclass

from database.connection import get_cursor
from models.audit_log import AuditLog


DATE_FORMAT_LABELS = {
    "MM/DD/YYYY": "Month/day/year (08/27/2026)",
    "DD/MM/YYYY": "Day/month/year (27/08/2026)",
    "YYYY/MM/DD": "Year/month/day (2026/08/27)",
}
DEFAULT_DATE_FORMAT = "MM/DD/YYYY"


@dataclass(frozen=True)
class AppPreferences:
    date_format: str = DEFAULT_DATE_FORMAT


def normalize_date_format(value: str | None) -> str:
    return value if value in DATE_FORMAT_LABELS else DEFAULT_DATE_FORMAT


def get_preferences() -> AppPreferences:
    with get_cursor() as cursor:
        row = cursor.execute(
            "SELECT date_format FROM app_preferences WHERE id = 1"
        ).fetchone()
    return AppPreferences(
        date_format=normalize_date_format(row["date_format"] if row else None)
    )


def get_date_format() -> str:
    return get_preferences().date_format


def save_date_format(value: str) -> AppPreferences:
    date_format = normalize_date_format(value)
    if value != date_format:
        raise ValueError("Choose one of the available date formats.")

    with get_cursor(commit=True) as cursor:
        existing = cursor.execute(
            "SELECT date_format FROM app_preferences WHERE id = 1"
        ).fetchone()
        old_format = normalize_date_format(
            existing["date_format"] if existing else None
        )
        cursor.execute(
            """
            INSERT INTO app_preferences (id, date_format, updated_at)
            VALUES (1, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                date_format = excluded.date_format,
                updated_at = excluded.updated_at
            """,
            (date_format,),
        )
        if not existing or old_format != date_format:
            AuditLog.write(
                cursor,
                client_id=None,
                table_name="app_preferences",
                record_id=1,
                action="UPDATE" if existing else "INSERT",
                old_values={"date_format": old_format} if existing else None,
                new_values={"date_format": date_format},
            )
    return AppPreferences(date_format=date_format)
