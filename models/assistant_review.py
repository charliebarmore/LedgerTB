"""The human side of assistant oversight: what the AI did, and sign-off.

Everything an assistant process writes is stamped "<user> (AI)" in the audit
trail (utils/actor.mark_as_assistant). This model turns that stream into a
reviewable queue: list assistant actions past the last review checkpoint, and
record an append-only "reviewed through here" mark when a person signs off.
"""
from dataclasses import dataclass
from typing import List, Optional

from database.connection import get_cursor


@dataclass
class AssistantAction:
    audit_id: int
    changed_at: str
    action: str
    table_name: str
    record_id: int
    actor: str


def _latest_mark_id(cursor, client_id: int) -> int:
    cursor.execute(
        "SELECT COALESCE(MAX(through_audit_id), 0) AS m "
        "FROM assistant_review_marks WHERE client_id = ?", (client_id,))
    return cursor.fetchone()["m"]


def unreviewed_actions(client_id: int, limit: int = 200) -> List[AssistantAction]:
    """Assistant-attributed audit rows past the latest review mark, oldest
    first — the order a reviewer reads them in."""
    with get_cursor() as cursor:
        mark = _latest_mark_id(cursor, client_id)
        cursor.execute(
            """SELECT id, datetime(changed_at, 'localtime') AS at_local,
                      action, table_name, record_id, performed_by
               FROM audit_log
               WHERE client_id = ? AND id > ?
                 AND performed_by LIKE '%(AI)'
               ORDER BY id LIMIT ?""",
            (client_id, mark, max(1, int(limit))),
        )
        return [AssistantAction(
            audit_id=r["id"], changed_at=r["at_local"] or "",
            action=r["action"], table_name=r["table_name"],
            record_id=r["record_id"], actor=r["performed_by"],
        ) for r in cursor.fetchall()]


def unreviewed_count(client_id: int) -> int:
    with get_cursor() as cursor:
        mark = _latest_mark_id(cursor, client_id)
        cursor.execute(
            "SELECT COUNT(*) AS n FROM audit_log WHERE client_id = ? "
            "AND id > ? AND performed_by LIKE '%(AI)'", (client_id, mark))
        return cursor.fetchone()["n"]


def latest_mark(client_id: int) -> Optional[dict]:
    with get_cursor() as cursor:
        cursor.execute(
            """SELECT through_audit_id,
                      datetime(reviewed_at, 'localtime') AS at_local,
                      reviewed_by
               FROM assistant_review_marks WHERE client_id = ?
               ORDER BY id DESC LIMIT 1""", (client_id,))
        row = cursor.fetchone()
    if not row:
        return None
    return {"through_audit_id": row["through_audit_id"],
            "reviewed_at": row["at_local"] or "",
            "reviewed_by": row["reviewed_by"]}


def mark_reviewed(client_id: int) -> int:
    """Record sign-off through the newest assistant audit row. Returns the
    audit id the mark covers. The sign-off itself is audit-logged."""
    from models.audit_log import AuditLog
    from utils.actor import current_actor

    with get_cursor(commit=True) as cursor:
        cursor.execute(
            "SELECT COALESCE(MAX(id), 0) AS m FROM audit_log "
            "WHERE client_id = ? AND performed_by LIKE '%(AI)'", (client_id,))
        through = cursor.fetchone()["m"]
        cursor.execute(
            """INSERT INTO assistant_review_marks
               (client_id, through_audit_id, reviewed_by) VALUES (?, ?, ?)""",
            (client_id, through, current_actor()),
        )
    # REVIEW lives in the event-level audit stream (the row-level table's
    # CHECK constraint predates the action). The mark row above is the durable
    # record; this makes the sign-off visible in the activity feed.
    AuditLog.log_event(client_id, "REVIEW", "assistant_activity_reviewed",
                       {"through_audit_id": through})
    return through
