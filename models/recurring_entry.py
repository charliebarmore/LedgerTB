"""Reusable journal-entry templates and their recurring schedules.

Templates store balanced integer-cent lines. A schedule supplies fiscal-period
timing, but generation lives in ``services.recurring_entries`` so persistence
and accounting-period orchestration remain separate concerns.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

from database.connection import get_connection, get_cursor
from models.audit_log import AuditLog
from utils.actor import current_actor


TEMPLATE_ENTRY_TYPES = ("Regular", "Adjusting")
SCHEDULE_FREQUENCIES = ("Monthly", "Quarterly", "Annually")
SCHEDULE_DATE_RULES = ("PeriodEnd", "PeriodStart", "DayOfMonth")
REVERSAL_RULES = ("None", "NextDay")


def _iso(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value else None


def _parse_date(value) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


@dataclass
class TemplateLine:
    account_id: int
    debit_cents: int = 0
    credit_cents: int = 0
    memo: str = ""
    sort_order: int = 0
    id: Optional[int] = None

    def snapshot(self) -> dict:
        return {
            "account_id": self.account_id,
            "debit_cents": self.debit_cents,
            "credit_cents": self.credit_cents,
            "memo": self.memo or "",
            "sort_order": self.sort_order,
        }


@dataclass
class JournalEntryTemplate:
    client_id: int
    name: str
    description: str
    entry_type: str = "Regular"
    source_reference: str = ""
    lines: List[TemplateLine] = field(default_factory=list)
    id: Optional[int] = None
    archived_at: str = ""
    archived_by: str = ""
    created_at: str = ""
    created_by: str = ""
    updated_at: str = ""
    updated_by: str = ""

    def _snapshot(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "source_reference": self.source_reference or None,
            "entry_type": self.entry_type,
            "archived_at": self.archived_at or None,
            "archived_by": self.archived_by or None,
            "lines": [line.snapshot() for line in self.lines],
        }

    def _normalize(self) -> None:
        self.name = str(self.name or "").strip()
        self.description = str(self.description or "").strip()
        self.source_reference = str(self.source_reference or "").strip()
        normalized = []
        for index, raw in enumerate(self.lines):
            line = raw if isinstance(raw, TemplateLine) else TemplateLine(**raw)
            line.account_id = int(line.account_id)
            line.debit_cents = int(line.debit_cents or 0)
            line.credit_cents = int(line.credit_cents or 0)
            line.memo = str(line.memo or "").strip()
            line.sort_order = index
            normalized.append(line)
        self.lines = normalized

    def validate(self, conn=None) -> None:
        self._normalize()
        if not self.name:
            raise ValueError("A template needs a name.")
        if not self.description:
            raise ValueError("A template needs a description.")
        if self.entry_type not in TEMPLATE_ENTRY_TYPES:
            raise ValueError("A template entry type must be Regular or Adjusting.")
        if len(self.lines) < 2:
            raise ValueError("A template needs at least two lines.")

        debits = credits = 0
        account_ids = set()
        for index, line in enumerate(self.lines, 1):
            if line.debit_cents < 0 or line.credit_cents < 0:
                raise ValueError(f"Template line {index} amounts cannot be negative.")
            if bool(line.debit_cents) == bool(line.credit_cents):
                raise ValueError(
                    f"Template line {index} needs a debit or a credit, not both."
                )
            debits += line.debit_cents
            credits += line.credit_cents
            account_ids.add(line.account_id)
        if debits != credits or debits == 0:
            raise ValueError(
                f"Template does not balance: debits {debits / 100:,.2f} vs "
                f"credits {credits / 100:,.2f}."
            )

        owns_conn = conn is None
        if owns_conn:
            conn = get_connection()
        try:
            cursor = conn.cursor()
            placeholders = ", ".join("?" for _ in account_ids)
            cursor.execute(
                f"SELECT id FROM accounts WHERE client_id = ? "
                f"AND id IN ({placeholders})",
                [self.client_id, *account_ids],
            )
            if {row["id"] for row in cursor.fetchall()} != account_ids:
                raise ValueError(
                    "Every template account must belong to the selected client."
                )
        finally:
            if owns_conn:
                conn.close()

    @staticmethod
    def _lines_for(cursor, template_id: int) -> List[TemplateLine]:
        cursor.execute(
            """SELECT * FROM journal_entry_template_lines
               WHERE template_id = ? ORDER BY sort_order, id""",
            (template_id,),
        )
        return [
            TemplateLine(
                id=row["id"], account_id=row["account_id"],
                debit_cents=int(row["debit_cents"] or 0),
                credit_cents=int(row["credit_cents"] or 0),
                memo=row["memo"] or "", sort_order=row["sort_order"],
            )
            for row in cursor.fetchall()
        ]

    @staticmethod
    def _from_row(row, lines: List[TemplateLine]) -> "JournalEntryTemplate":
        return JournalEntryTemplate(
            id=row["id"], client_id=row["client_id"], name=row["name"],
            description=row["description"], entry_type=row["entry_type"],
            source_reference=row["source_reference"] or "", lines=lines,
            archived_at=row["archived_at"] or "",
            archived_by=row["archived_by"] or "",
            created_at=row["created_at"] or "", created_by=row["created_by"] or "",
            updated_at=row["updated_at"] or "", updated_by=row["updated_by"] or "",
        )

    def save(self, conn=None) -> int:
        self.validate(conn=conn)
        owns_conn = conn is None
        if owns_conn:
            conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id FROM journal_entry_templates
                   WHERE client_id = ? AND name = ? COLLATE NOCASE
                     AND archived_at IS NULL AND (? IS NULL OR id != ?)""",
                (self.client_id, self.name, self.id, self.id),
            )
            if cursor.fetchone():
                raise ValueError(
                    "An active template with this name already exists for the client."
                )

            actor = current_actor()
            is_new = self.id is None
            old_values = None
            if is_new:
                cursor.execute(
                    """INSERT INTO journal_entry_templates
                       (client_id, name, description, source_reference, entry_type,
                        created_by, updated_by)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        self.client_id, self.name, self.description,
                        self.source_reference or None, self.entry_type, actor, actor,
                    ),
                )
                self.id = cursor.lastrowid
            else:
                cursor.execute(
                    """SELECT * FROM journal_entry_templates
                       WHERE id = ? AND client_id = ?""",
                    (self.id, self.client_id),
                )
                old_row = cursor.fetchone()
                if not old_row:
                    raise ValueError("Template not found for the selected client.")
                old_template = JournalEntryTemplate._from_row(
                    old_row, JournalEntryTemplate._lines_for(cursor, self.id)
                )
                old_values = old_template._snapshot()
                cursor.execute(
                    """UPDATE journal_entry_templates
                       SET name = ?, description = ?, source_reference = ?,
                           entry_type = ?, updated_at = datetime('now', 'localtime'),
                           updated_by = ?
                       WHERE id = ? AND client_id = ?""",
                    (
                        self.name, self.description, self.source_reference or None,
                        self.entry_type, actor, self.id, self.client_id,
                    ),
                )
                cursor.execute(
                    "DELETE FROM journal_entry_template_lines WHERE template_id = ?",
                    (self.id,),
                )

            for line in self.lines:
                cursor.execute(
                    """INSERT INTO journal_entry_template_lines
                       (template_id, account_id, debit_cents, credit_cents,
                        memo, sort_order)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        self.id, line.account_id, line.debit_cents,
                        line.credit_cents, line.memo or None, line.sort_order,
                    ),
                )
                line.id = cursor.lastrowid

            cursor.execute(
                "SELECT * FROM journal_entry_templates WHERE id = ?", (self.id,)
            )
            saved = cursor.fetchone()
            self.created_at = saved["created_at"] or ""
            self.created_by = saved["created_by"] or ""
            self.updated_at = saved["updated_at"] or ""
            self.updated_by = saved["updated_by"] or ""
            AuditLog.write(
                cursor, self.client_id, "journal_entry_templates", self.id,
                "INSERT" if is_new else "UPDATE", old_values=old_values,
                new_values=self._snapshot(),
            )
            if owns_conn:
                conn.commit()
            return self.id
        except Exception:
            if owns_conn:
                conn.rollback()
            raise
        finally:
            if owns_conn:
                conn.close()

    def archive(self, conn=None) -> None:
        if self.id is None:
            raise ValueError("Save the template before archiving it.")
        owns_conn = conn is None
        if owns_conn:
            conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM journal_entry_templates WHERE id = ? AND client_id = ?",
                (self.id, self.client_id),
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError("Template not found for the selected client.")
            if row["archived_at"]:
                return
            old_values = JournalEntryTemplate._from_row(
                row, JournalEntryTemplate._lines_for(cursor, self.id)
            )._snapshot()
            actor = current_actor()
            cursor.execute(
                """UPDATE journal_entry_templates
                   SET archived_at = datetime('now', 'localtime'), archived_by = ?,
                       updated_at = datetime('now', 'localtime'), updated_by = ?
                   WHERE id = ? AND client_id = ?""",
                (actor, actor, self.id, self.client_id),
            )
            cursor.execute(
                """SELECT * FROM recurring_schedules
                   WHERE template_id = ? AND is_active = 1""",
                (self.id,),
            )
            schedule = cursor.fetchone()
            if schedule:
                cursor.execute(
                    """UPDATE recurring_schedules
                       SET is_active = 0, updated_at = datetime('now', 'localtime'),
                           updated_by = ? WHERE id = ?""",
                    (actor, schedule["id"]),
                )
                AuditLog.write(
                    cursor, self.client_id, "recurring_schedules", schedule["id"],
                    "UPDATE", old_values=RecurringSchedule._snapshot_row(schedule),
                    new_values={**RecurringSchedule._snapshot_row(schedule),
                                "is_active": False},
                )
            cursor.execute(
                "SELECT * FROM journal_entry_templates WHERE id = ?", (self.id,)
            )
            saved = cursor.fetchone()
            self.archived_at = saved["archived_at"] or ""
            self.archived_by = saved["archived_by"] or ""
            self.updated_at = saved["updated_at"] or ""
            self.updated_by = saved["updated_by"] or ""
            AuditLog.write(
                cursor, self.client_id, "journal_entry_templates", self.id, "UPDATE",
                old_values=old_values,
                new_values={
                    **old_values,
                    "archived_at": self.archived_at,
                    "archived_by": self.archived_by,
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

    def restore(self) -> None:
        if self.id is None:
            raise ValueError("Save the template before restoring it.")
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM journal_entry_templates WHERE id = ? AND client_id = ?",
                (self.id, self.client_id),
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError("Template not found for the selected client.")
            if not row["archived_at"]:
                return
            cursor.execute(
                """SELECT id FROM journal_entry_templates
                   WHERE client_id = ? AND name = ? COLLATE NOCASE
                     AND archived_at IS NULL AND id != ?""",
                (self.client_id, row["name"], self.id),
            )
            if cursor.fetchone():
                raise ValueError(
                    "Rename the active template with this name before restoring this one."
                )
            old_values = JournalEntryTemplate._from_row(
                row, JournalEntryTemplate._lines_for(cursor, self.id)
            )._snapshot()
            actor = current_actor()
            cursor.execute(
                """UPDATE journal_entry_templates
                   SET archived_at = NULL, archived_by = NULL,
                       updated_at = datetime('now', 'localtime'), updated_by = ?
                   WHERE id = ? AND client_id = ?""",
                (actor, self.id, self.client_id),
            )
            self.archived_at = ""
            self.archived_by = ""
            self.updated_by = actor
            AuditLog.write(
                cursor, self.client_id, "journal_entry_templates", self.id, "UPDATE",
                old_values=old_values,
                new_values={
                    **old_values, "archived_at": None, "archived_by": None,
                },
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def get_by_id(template_id: int, client_id: int) -> Optional["JournalEntryTemplate"]:
        with get_cursor() as cursor:
            cursor.execute(
                """SELECT * FROM journal_entry_templates
                   WHERE id = ? AND client_id = ?""",
                (template_id, client_id),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return JournalEntryTemplate._from_row(
                row, JournalEntryTemplate._lines_for(cursor, template_id)
            )

    @staticmethod
    def get_all(
        client_id: int, include_archived: bool = False
    ) -> List["JournalEntryTemplate"]:
        with get_cursor() as cursor:
            query = "SELECT * FROM journal_entry_templates WHERE client_id = ?"
            params = [client_id]
            if not include_archived:
                query += " AND archived_at IS NULL"
            query += " ORDER BY name COLLATE NOCASE, id"
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [
                JournalEntryTemplate._from_row(
                    row, JournalEntryTemplate._lines_for(cursor, row["id"])
                )
                for row in rows
            ]


@dataclass
class RecurringSchedule:
    template_id: int
    frequency: str = "Monthly"
    date_rule: str = "PeriodEnd"
    starts_on: Optional[date] = None
    ends_on: Optional[date] = None
    day_of_month: Optional[int] = None
    reversal_rule: str = "None"
    is_active: bool = True
    id: Optional[int] = None
    created_at: str = ""
    created_by: str = ""
    updated_at: str = ""
    updated_by: str = ""

    def _snapshot(self) -> dict:
        return {
            "template_id": self.template_id,
            "frequency": self.frequency,
            "date_rule": self.date_rule,
            "day_of_month": self.day_of_month,
            "starts_on": _iso(self.starts_on),
            "ends_on": _iso(self.ends_on),
            "reversal_rule": self.reversal_rule,
            "is_active": bool(self.is_active),
        }

    @staticmethod
    def _snapshot_row(row) -> dict:
        return {
            "template_id": row["template_id"],
            "frequency": row["frequency"],
            "date_rule": row["date_rule"],
            "day_of_month": row["day_of_month"],
            "starts_on": row["starts_on"],
            "ends_on": row["ends_on"],
            "reversal_rule": row["reversal_rule"],
            "is_active": bool(row["is_active"]),
        }

    def validate(self, conn=None) -> int:
        self.template_id = int(self.template_id)
        self.starts_on = _parse_date(self.starts_on)
        self.ends_on = _parse_date(self.ends_on)
        if self.frequency not in SCHEDULE_FREQUENCIES:
            raise ValueError("Frequency must be Monthly, Quarterly, or Annually.")
        if self.date_rule not in SCHEDULE_DATE_RULES:
            raise ValueError(
                "Entry date must use period end, period start, or day of month."
            )
        if not self.starts_on:
            raise ValueError("A recurring schedule needs a first applicable date.")
        if self.ends_on and self.ends_on < self.starts_on:
            raise ValueError("The last applicable date cannot precede the first.")
        if self.date_rule == "DayOfMonth":
            if self.frequency != "Monthly":
                raise ValueError("Day of month is available only for monthly schedules.")
            try:
                self.day_of_month = int(self.day_of_month)
            except (TypeError, ValueError):
                raise ValueError("Choose a day of month between 1 and 31.")
            if not 1 <= self.day_of_month <= 31:
                raise ValueError("Choose a day of month between 1 and 31.")
        else:
            self.day_of_month = None
        if self.reversal_rule not in REVERSAL_RULES:
            raise ValueError("Reversal must be None or NextDay.")
        if self.reversal_rule == "NextDay" and self.date_rule != "PeriodEnd":
            raise ValueError("Automatic reversal requires a period-end schedule.")

        owns_conn = conn is None
        if owns_conn:
            conn = get_connection()
        try:
            row = conn.execute(
                """SELECT id, client_id, archived_at FROM journal_entry_templates
                   WHERE id = ?""",
                (self.template_id,),
            ).fetchone()
            if not row:
                raise ValueError("Template not found.")
            if row["archived_at"]:
                raise ValueError("An archived template cannot have an active schedule.")
            return int(row["client_id"])
        finally:
            if owns_conn:
                conn.close()

    def save(self, conn=None) -> int:
        client_id = self.validate(conn=conn)
        owns_conn = conn is None
        if owns_conn:
            conn = get_connection()
        try:
            cursor = conn.cursor()
            actor = current_actor()
            is_new = self.id is None
            old_values = None
            if is_new:
                cursor.execute(
                    "SELECT id FROM recurring_schedules WHERE template_id = ?",
                    (self.template_id,),
                )
                if cursor.fetchone():
                    raise ValueError("This template already has a recurring schedule.")
                cursor.execute(
                    """INSERT INTO recurring_schedules
                       (template_id, frequency, date_rule, day_of_month,
                        starts_on, ends_on, reversal_rule, is_active,
                        created_by, updated_by)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        self.template_id, self.frequency, self.date_rule,
                        self.day_of_month, _iso(self.starts_on), _iso(self.ends_on),
                        self.reversal_rule, int(self.is_active), actor, actor,
                    ),
                )
                self.id = cursor.lastrowid
            else:
                cursor.execute(
                    """SELECT rs.* FROM recurring_schedules rs
                       JOIN journal_entry_templates t ON t.id = rs.template_id
                       WHERE rs.id = ? AND t.client_id = ?""",
                    (self.id, client_id),
                )
                old = cursor.fetchone()
                if not old:
                    raise ValueError("Schedule not found for the selected client.")
                if old["template_id"] != self.template_id:
                    raise ValueError("A schedule cannot be moved to another template.")
                old_values = self._snapshot_row(old)
                cursor.execute(
                    """UPDATE recurring_schedules
                       SET frequency = ?, date_rule = ?, day_of_month = ?,
                           starts_on = ?, ends_on = ?, reversal_rule = ?,
                           is_active = ?, updated_at = datetime('now', 'localtime'),
                           updated_by = ? WHERE id = ?""",
                    (
                        self.frequency, self.date_rule, self.day_of_month,
                        _iso(self.starts_on), _iso(self.ends_on), self.reversal_rule,
                        int(self.is_active), actor, self.id,
                    ),
                )
            cursor.execute("SELECT * FROM recurring_schedules WHERE id = ?", (self.id,))
            saved = cursor.fetchone()
            self.created_at = saved["created_at"] or ""
            self.created_by = saved["created_by"] or ""
            self.updated_at = saved["updated_at"] or ""
            self.updated_by = saved["updated_by"] or ""
            AuditLog.write(
                cursor, client_id, "recurring_schedules", self.id,
                "INSERT" if is_new else "UPDATE", old_values=old_values,
                new_values=self._snapshot(),
            )
            if owns_conn:
                conn.commit()
            return self.id
        except Exception:
            if owns_conn:
                conn.rollback()
            raise
        finally:
            if owns_conn:
                conn.close()

    @staticmethod
    def _from_row(row) -> "RecurringSchedule":
        return RecurringSchedule(
            id=row["id"], template_id=row["template_id"],
            frequency=row["frequency"], date_rule=row["date_rule"],
            day_of_month=row["day_of_month"], starts_on=_parse_date(row["starts_on"]),
            ends_on=_parse_date(row["ends_on"]), reversal_rule=row["reversal_rule"],
            is_active=bool(row["is_active"]), created_at=row["created_at"] or "",
            created_by=row["created_by"] or "", updated_at=row["updated_at"] or "",
            updated_by=row["updated_by"] or "",
        )

    @staticmethod
    def get_by_id(
        schedule_id: int, client_id: int
    ) -> Optional["RecurringSchedule"]:
        with get_cursor() as cursor:
            cursor.execute(
                """SELECT rs.* FROM recurring_schedules rs
                   JOIN journal_entry_templates t ON t.id = rs.template_id
                   WHERE rs.id = ? AND t.client_id = ?""",
                (schedule_id, client_id),
            )
            row = cursor.fetchone()
        return RecurringSchedule._from_row(row) if row else None

    @staticmethod
    def get_for_template(
        template_id: int, client_id: int
    ) -> Optional["RecurringSchedule"]:
        with get_cursor() as cursor:
            cursor.execute(
                """SELECT rs.* FROM recurring_schedules rs
                   JOIN journal_entry_templates t ON t.id = rs.template_id
                   WHERE rs.template_id = ? AND t.client_id = ?""",
                (template_id, client_id),
            )
            row = cursor.fetchone()
        return RecurringSchedule._from_row(row) if row else None

    @staticmethod
    def get_all(
        client_id: int, active_only: bool = False
    ) -> List["RecurringSchedule"]:
        with get_cursor() as cursor:
            query = (
                "SELECT rs.* FROM recurring_schedules rs "
                "JOIN journal_entry_templates t ON t.id = rs.template_id "
                "WHERE t.client_id = ? AND t.archived_at IS NULL"
            )
            params = [client_id]
            if active_only:
                query += " AND rs.is_active = 1"
            query += " ORDER BY t.name COLLATE NOCASE, rs.id"
            cursor.execute(query, params)
            rows = cursor.fetchall()
        return [RecurringSchedule._from_row(row) for row in rows]

    def set_active(self, active: bool) -> None:
        self.is_active = bool(active)
        self.save()
