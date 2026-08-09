import json
import logging
import uuid
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any
from datetime import datetime
from database.connection import get_cursor
from utils.fiscal_dates import require_valid_range

logger = logging.getLogger(__name__)

AUDIT_ACTIONS = {
    "INSERT", "UPDATE", "DELETE", "EXPORT", "BACKUP", "RESTORE",
    "CLOSE", "REOPEN", "REVERSE", "OVERRIDE", "REVIEW",
}


@dataclass
class AuditLog:
    id: Optional[int] = None
    client_id: int = 0
    table_name: str = ""
    record_id: int = 0
    action: str = ""  # INSERT, UPDATE, DELETE
    old_values: Optional[Dict[str, Any]] = None
    new_values: Optional[Dict[str, Any]] = None
    changed_at: Optional[datetime] = None
    session_id: Optional[str] = None
    performed_by: Optional[str] = None

    @staticmethod
    def get_session_id() -> str:
        """Get or create a session ID for tracking related changes."""
        import streamlit as st
        if 'audit_session_id' not in st.session_state:
            st.session_state.audit_session_id = str(uuid.uuid4())
        return st.session_state.audit_session_id

    @staticmethod
    def _current_session_id() -> str:
        """Return the Streamlit session id when in-app, otherwise a request id."""
        try:
            from streamlit.runtime.scriptrunner import get_script_run_ctx
            if get_script_run_ctx(suppress_warning=True) is not None:
                return AuditLog.get_session_id()
        except Exception:
            pass
        return str(uuid.uuid4())

    @staticmethod
    def _json(values):
        if values is None:
            return None
        return json.dumps(
            values,
            default=lambda value: value.isoformat()
            if isinstance(value, (datetime,)) or hasattr(value, "isoformat")
            else str(value),
            sort_keys=True,
        )

    @staticmethod
    def write(
        cursor,
        client_id: int,
        table_name: str,
        record_id: int,
        action: str,
        old_values: Optional[Dict[str, Any]] = None,
        new_values: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> int:
        """Insert an audit row using the caller's transaction.

        Accounting mutations must call this before commit. If the audit insert
        fails, the caller rolls the business change back as well, preventing an
        unaudited successful mutation.
        """
        if action not in AUDIT_ACTIONS:
            raise ValueError(f"Unsupported audit action: {action}")
        # Stamp local time explicitly rather than leaning on SQLite's
        # CURRENT_TIMESTAMP default, which is UTC. ProBooks is a single-user
        # local desktop app whose audit filter and every other date use the
        # machine's local clock (date.today()); a UTC default made evening
        # entries (past UTC midnight) appear dated "tomorrow", inverting the
        # audit page's default date range and mislabeling when the user acted.
        changed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        from utils.actor import current_actor
        cursor.execute(
            """
            INSERT INTO audit_log
                (client_id, table_name, record_id, action, old_values, new_values,
                 session_id, changed_at, performed_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                client_id, table_name, record_id, action,
                AuditLog._json(old_values), AuditLog._json(new_values),
                session_id or AuditLog._current_session_id(), changed_at,
                current_actor(),
            ),
        )
        return cursor.lastrowid

    @staticmethod
    def log_change(
        client_id: int,
        table_name: str,
        record_id: int,
        action: str,
        old_values: Optional[Dict[str, Any]] = None,
        new_values: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Log a change to the audit log.

        Args:
            client_id: The client ID
            table_name: Name of the table being modified
            record_id: ID of the record being modified
            action: 'INSERT', 'UPDATE', or 'DELETE'
            old_values: Dictionary of values before the change (for UPDATE/DELETE)
            new_values: Dictionary of values after the change (for INSERT/UPDATE)

        Returns:
            The ID of the created audit log entry
        """
        with get_cursor(commit=True) as cursor:
            return AuditLog.write(
                cursor=cursor, client_id=client_id, table_name=table_name,
                record_id=record_id, action=action, old_values=old_values,
                new_values=new_values,
            )

    @staticmethod
    def log_event(
        client_id: int,
        action: str,
        event_name: str,
        details: Optional[Dict[str, Any]] = None,
        record_id: int = 0,
    ) -> int:
        """Record a non-CRUD event such as an export or backup."""
        return AuditLog.log_change(
            client_id=client_id,
            table_name=event_name,
            record_id=record_id,
            action=action,
            new_values=details,
        )

    @staticmethod
    def log_change_safe(
        client_id: int,
        table_name: str,
        record_id: int,
        action: str,
        old_values: Optional[Dict[str, Any]] = None,
        new_values: Optional[Dict[str, Any]] = None
    ) -> Optional[int]:
        """Best-effort audit log write for use inside a mutating operation.

        A failing audit write must not fail the underlying operation (losing a
        user's save to an audit hiccup is worse than a missing audit row), but
        the failure is logged via the logging module rather than silently
        swallowed, so it is visible rather than invisible. Returns the log id, or
        None if logging failed.
        """
        try:
            return AuditLog.log_change(
                client_id=client_id,
                table_name=table_name,
                record_id=record_id,
                action=action,
                old_values=old_values,
                new_values=new_values,
            )
        except Exception:
            logger.warning(
                "Audit log write failed for %s on %s id=%s",
                action, table_name, record_id, exc_info=True,
            )
            return None

    @staticmethod
    def get_by_id(log_id: int) -> Optional['AuditLog']:
        """Get an audit log entry by ID."""
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM audit_log WHERE id = ?", (log_id,))
            row = cursor.fetchone()

            if not row:
                return None

            log = AuditLog(
                id=row['id'],
                client_id=row['client_id'],
                table_name=row['table_name'],
                record_id=row['record_id'],
                action=row['action'],
                old_values=json.loads(row['old_values']) if row['old_values'] else None,
                new_values=json.loads(row['new_values']) if row['new_values'] else None,
                changed_at=datetime.fromisoformat(row['changed_at']) if row['changed_at'] else None,
                session_id=row['session_id']
            )

        return log

    @staticmethod
    def get_earliest_date(client_id: int) -> Optional[datetime]:
        """Get the timestamp of the client's earliest audit log entry, if any."""
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT MIN(changed_at) as earliest FROM audit_log WHERE client_id = ?",
                (client_id,)
            )
            row = cursor.fetchone()

        if row and row['earliest']:
            return datetime.fromisoformat(row['earliest'])
        return None

    @staticmethod
    def get_history(
        table_name: str,
        record_id: int
    ) -> List['AuditLog']:
        """
        Get the change history for a specific record.

        Args:
            table_name: Name of the table
            record_id: ID of the record

        Returns:
            List of AuditLog entries, ordered by time descending
        """
        with get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM audit_log
                WHERE table_name = ? AND record_id = ?
                ORDER BY changed_at DESC, id DESC
            """, (table_name, record_id))

            logs = []
            for row in cursor.fetchall():
                logs.append(AuditLog(
                    id=row['id'],
                    client_id=row['client_id'],
                    table_name=row['table_name'],
                    record_id=row['record_id'],
                    action=row['action'],
                    old_values=json.loads(row['old_values']) if row['old_values'] else None,
                    new_values=json.loads(row['new_values']) if row['new_values'] else None,
                    changed_at=datetime.fromisoformat(row['changed_at']) if row['changed_at'] else None,
                    session_id=row['session_id'],
                    performed_by=row['performed_by'] if 'performed_by' in row.keys() else None,
                ))

        return logs

    @staticmethod
    def get_all(
        client_id: int,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        table_name: Optional[str] = None,
        action: Optional[str] = None,
        search_term: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List['AuditLog']:
        """
        Get audit log entries with optional filters.

        Args:
            client_id: The client ID
            start_date: Filter for changes after this time
            end_date: Filter for changes before this time
            table_name: Filter by table name
            action: Filter by action type (INSERT, UPDATE, DELETE)
            search_term: Search in old_values and new_values JSON
            limit: Maximum number of entries to return

        Returns:
            List of AuditLog entries, ordered by time descending
        """
        where, params = AuditLog._filter_sql(
            client_id, start_date, end_date, table_name, action, search_term,
        )
        query = "SELECT * FROM audit_log" + where
        query += " ORDER BY changed_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([max(1, int(limit)), max(0, int(offset))])

        with get_cursor() as cursor:
            cursor.execute(query, params)

            logs = []
            for row in cursor.fetchall():
                logs.append(AuditLog(
                    id=row['id'],
                    client_id=row['client_id'],
                    table_name=row['table_name'],
                    record_id=row['record_id'],
                    action=row['action'],
                    old_values=json.loads(row['old_values']) if row['old_values'] else None,
                    new_values=json.loads(row['new_values']) if row['new_values'] else None,
                    changed_at=datetime.fromisoformat(row['changed_at']) if row['changed_at'] else None,
                    session_id=row['session_id'],
                    performed_by=row['performed_by'] if 'performed_by' in row.keys() else None,
                ))

        return logs

    @staticmethod
    def _filter_sql(
        client_id: int,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        table_name: Optional[str] = None,
        action: Optional[str] = None,
        search_term: Optional[str] = None,
    ):
        require_valid_range(start_date, end_date, "Audit filter")
        clauses = ["client_id = ?"]
        params: List[Any] = [client_id]
        # SQLite CURRENT_TIMESTAMP uses a space separator, not ISO's "T".
        if start_date:
            clauses.append("changed_at >= ?")
            params.append(start_date.strftime("%Y-%m-%d %H:%M:%S"))
        if end_date:
            clauses.append("changed_at <= ?")
            params.append(end_date.strftime("%Y-%m-%d %H:%M:%S"))
        if table_name:
            clauses.append("table_name = ?")
            params.append(table_name)
        if action:
            clauses.append("action = ?")
            params.append(action)
        if search_term:
            clauses.append("(old_values LIKE ? OR new_values LIKE ?)")
            search_pattern = f"%{search_term}%"
            params.extend([search_pattern, search_pattern])
        return " WHERE " + " AND ".join(clauses), params

    @staticmethod
    def get_filtered_counts(
        client_id: int,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        table_name: Optional[str] = None,
        action: Optional[str] = None,
        search_term: Optional[str] = None,
    ) -> Dict[str, int]:
        """Return SQL-backed counts for the complete filtered audit result."""
        where, params = AuditLog._filter_sql(
            client_id, start_date, end_date, table_name, action, search_term,
        )
        with get_cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) total,
                       SUM(CASE WHEN action = 'INSERT' THEN 1 ELSE 0 END) inserts,
                       SUM(CASE WHEN action = 'UPDATE' THEN 1 ELSE 0 END) updates,
                       SUM(CASE WHEN action = 'DELETE' THEN 1 ELSE 0 END) deletes,
                       SUM(CASE WHEN action NOT IN ('INSERT', 'UPDATE', 'DELETE')
                                THEN 1 ELSE 0 END) events
                FROM audit_log
                """ + where,
                params,
            )
            row = cursor.fetchone()
        return {key: int(row[key] or 0) for key in ("total", "inserts", "updates", "deletes", "events")}

    @staticmethod
    def get_entry_changes(
        client_id: int,
        entry_id: int
    ) -> List['AuditLog']:
        """
        Get all audit log entries for a specific journal entry.

        Args:
            client_id: The client ID
            entry_id: The journal entry ID

        Returns:
            List of AuditLog entries related to this journal entry
        """
        with get_cursor() as cursor:
            # Get changes to the journal entry itself and its lines
            cursor.execute("""
                SELECT * FROM audit_log
                WHERE client_id = ?
                  AND ((table_name = 'journal_entries' AND record_id = ?)
                       OR (table_name = 'journal_entry_lines' AND new_values LIKE ?))
                ORDER BY changed_at DESC, id DESC
            """, (client_id, entry_id, f'%"journal_entry_id": {entry_id}%'))

            logs = []
            for row in cursor.fetchall():
                logs.append(AuditLog(
                    id=row['id'],
                    client_id=row['client_id'],
                    table_name=row['table_name'],
                    record_id=row['record_id'],
                    action=row['action'],
                    old_values=json.loads(row['old_values']) if row['old_values'] else None,
                    new_values=json.loads(row['new_values']) if row['new_values'] else None,
                    changed_at=datetime.fromisoformat(row['changed_at']) if row['changed_at'] else None,
                    session_id=row['session_id'],
                    performed_by=row['performed_by'] if 'performed_by' in row.keys() else None,
                ))

        return logs

    def format_changes(self) -> str:
        """Format the changes for display."""
        if self.action == 'INSERT':
            if self.new_values:
                return f"Created with: {self._format_values(self.new_values)}"
            return "Created"
        elif self.action == 'DELETE':
            if self.old_values:
                return f"Deleted: {self._format_values(self.old_values)}"
            return "Deleted"
        elif self.action == 'UPDATE':
            changes = []
            if self.old_values and self.new_values:
                for key in set(list(self.old_values.keys()) + list(self.new_values.keys())):
                    old_val = self.old_values.get(key)
                    new_val = self.new_values.get(key)
                    if old_val != new_val:
                        changes.append(f"{key}: {old_val} -> {new_val}")
            return "; ".join(changes) if changes else "Updated"
        return ""

    def _format_values(self, values: Dict[str, Any]) -> str:
        """Format a dictionary of values for display."""
        formatted = []
        for key, value in values.items():
            if key not in ('id', 'created_at', 'client_id'):
                formatted.append(f"{key}={value}")
        return ", ".join(formatted)
