"""Fiscal-period scheduling for recurring journal-entry drafts.

The service is deliberately human-triggered. It previews canonical periods and
files idempotent draft snapshots; it never posts journal entries.
"""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Optional

from database.connection import get_connection, get_cursor
from models.audit_log import AuditLog
from models.draft_entry import DraftEntry, DraftLine
from models.fiscal_period import FiscalPeriod
from models.recurring_entry import JournalEntryTemplate, RecurringSchedule
from utils.actor import current_actor
from utils.fiscal_dates import fiscal_year_ending_year


_PERIOD_TYPES = {
    "Monthly": "Month",
    "Quarterly": "Quarter",
    "Annually": "Year",
}


@dataclass(frozen=True)
class OccurrencePreview:
    client_id: int
    schedule_id: int
    template_id: int
    template_name: str
    period_name: str
    period_type: str
    period_start: date
    period_end: date
    entry_date: date
    state: str
    reason: str = ""
    occurrence_id: Optional[int] = None
    draft_id: Optional[int] = None
    draft_status: str = ""
    generation_number: Optional[int] = None
    reversal_draft_id: Optional[int] = None
    reversal_draft_status: str = ""
    reversal_generation_number: Optional[int] = None

    def key(self) -> tuple[int, str, str]:
        return self.schedule_id, self.period_start.isoformat(), self.period_end.isoformat()


def _entry_date(schedule: RecurringSchedule, period: FiscalPeriod) -> date:
    if schedule.date_rule == "PeriodStart":
        return period.start_date
    if schedule.date_rule == "DayOfMonth":
        day = min(schedule.day_of_month, monthrange(period.end_date.year,
                                                    period.end_date.month)[1])
        return date(period.end_date.year, period.end_date.month, day)
    return period.end_date


def _expected_periods(
    client_id: int,
    fiscal_year_end_month: int,
    schedule: RecurringSchedule,
    through_date: date,
) -> List[FiscalPeriod]:
    endpoint = min(through_date, schedule.ends_on) if schedule.ends_on else through_date
    if endpoint < schedule.starts_on:
        return []
    first_fy = fiscal_year_ending_year(schedule.starts_on, fiscal_year_end_month)
    last_fy = fiscal_year_ending_year(endpoint, fiscal_year_end_month)
    period_type = _PERIOD_TYPES[schedule.frequency]
    periods = []
    for year in range(first_fy, last_fy + 1):
        periods.extend(
            period for period in FiscalPeriod._calendar(
                client_id, year, fiscal_year_end_month
            ) if period.period_type == period_type
        )
    return periods


def _stored_period_keys(cursor, client_id: int) -> set[tuple[str, str, str]]:
    cursor.execute(
        """SELECT period_type, start_date, end_date FROM fiscal_periods
           WHERE client_id = ? AND period_type IN ('Month', 'Quarter', 'Year')""",
        (client_id,),
    )
    return {
        (row["period_type"], row["start_date"], row["end_date"])
        for row in cursor.fetchall()
    }


def _closed_year_for(cursor, client_id: int, entry_date: date):
    return cursor.execute(
        """SELECT period_name FROM fiscal_periods
           WHERE client_id = ? AND period_type = 'Year' AND is_closed = 1
             AND start_date <= ? AND end_date >= ?
           ORDER BY end_date DESC LIMIT 1""",
        (client_id, entry_date.isoformat(), entry_date.isoformat()),
    ).fetchone()


def _latest_draft(cursor, occurrence_id: int, role: str):
    return cursor.execute(
        """SELECT rod.generation_number, d.id draft_id, d.status draft_status
           FROM recurring_occurrence_drafts rod
           JOIN draft_entries d ON d.id = rod.draft_entry_id
           WHERE rod.occurrence_id = ? AND rod.role = ?
           ORDER BY rod.generation_number DESC LIMIT 1""",
        (occurrence_id, role),
    ).fetchone()


def _overlapping_occurrence(
    cursor, schedule_id: int, period_start: date, period_end: date
):
    """An already-accounted period whose boundaries intersect a candidate."""
    return cursor.execute(
        """SELECT id, period_name, period_start, period_end, disposition
           FROM recurring_occurrences
           WHERE schedule_id = ?
             AND period_start <= ? AND period_end >= ?
           ORDER BY period_start, id LIMIT 1""",
        (schedule_id, period_end.isoformat(), period_start.isoformat()),
    ).fetchone()


def _preview_schedule(
    cursor,
    client_id: int,
    template: JournalEntryTemplate,
    schedule: RecurringSchedule,
    fiscal_year_end_month: int,
    through_date: date,
) -> List[OccurrencePreview]:
    stored_keys = _stored_period_keys(cursor, client_id)
    invalid_reason = ""
    try:
        template.validate(conn=cursor.connection)
    except ValueError as exc:
        invalid_reason = str(exc)
    if not invalid_reason:
        account_ids = [line.account_id for line in template.lines]
        placeholders = ", ".join("?" for _ in account_ids)
        cursor.execute(
            f"SELECT id FROM accounts WHERE client_id = ? AND is_active = 1 "
            f"AND id IN ({placeholders})",
            [client_id, *account_ids],
        )
        if {row["id"] for row in cursor.fetchall()} != set(account_ids):
            invalid_reason = (
                "A template account is inactive. Reactivate it or update the template."
            )

    previews = []
    for period in _expected_periods(
        client_id, fiscal_year_end_month, schedule, through_date
    ):
        scheduled = _entry_date(schedule, period)
        if scheduled < schedule.starts_on:
            continue
        if schedule.ends_on and scheduled > schedule.ends_on:
            continue
        if scheduled > through_date:
            continue
        key = (
            period.period_type,
            period.start_date.isoformat(),
            period.end_date.isoformat(),
        )
        base = dict(
            client_id=client_id, schedule_id=schedule.id,
            template_id=template.id, template_name=template.name,
            period_name=period.period_name, period_type=period.period_type,
            period_start=period.start_date, period_end=period.end_date,
            entry_date=scheduled,
        )
        occurrence = cursor.execute(
            """SELECT * FROM recurring_occurrences
               WHERE schedule_id = ? AND period_start = ? AND period_end = ?""",
            (schedule.id, period.start_date.isoformat(), period.end_date.isoformat()),
        ).fetchone()
        if occurrence:
            if occurrence["disposition"] == "Skipped":
                previews.append(OccurrencePreview(
                    **base, state="Skipped", reason=occurrence["skip_reason"] or "",
                    occurrence_id=occurrence["id"],
                ))
                continue
            primary = _latest_draft(cursor, occurrence["id"], "Primary")
            if not primary:
                previews.append(OccurrencePreview(
                    **base, state="Blocked",
                    reason="The occurrence exists but its draft link is missing.",
                    occurrence_id=occurrence["id"],
                ))
            else:
                reversal = _latest_draft(cursor, occurrence["id"], "Reversal")
                previews.append(OccurrencePreview(
                    **base, state="Handled", occurrence_id=occurrence["id"],
                    draft_id=primary["draft_id"],
                    draft_status=primary["draft_status"],
                    generation_number=primary["generation_number"],
                    reversal_draft_id=(
                        reversal["draft_id"] if reversal else None
                    ),
                    reversal_draft_status=(
                        reversal["draft_status"] if reversal else ""
                    ),
                    reversal_generation_number=(
                        reversal["generation_number"] if reversal else None
                    ),
                ))
            continue
        overlap = _overlapping_occurrence(
            cursor, schedule.id, period.start_date, period.end_date
        )
        if overlap:
            previews.append(OccurrencePreview(
                **base, state="Blocked",
                reason=(
                    f"{period.period_name} overlaps the already handled "
                    f"{overlap['period_name']} occurrence. Changing a schedule's "
                    "frequency never reopens previously accounted dates."
                ),
            ))
            continue
        if key not in stored_keys:
            previews.append(OccurrencePreview(
                **base, state="Blocked",
                reason=(
                    f"The fiscal calendar is missing {period.period_name}. "
                    "Create that fiscal-year calendar first."
                ),
            ))
            continue
        if invalid_reason:
            previews.append(OccurrencePreview(
                **base, state="Blocked", reason=invalid_reason,
            ))
            continue
        closed = _closed_year_for(cursor, client_id, scheduled)
        if closed:
            previews.append(OccurrencePreview(
                **base, state="Blocked",
                reason=f"{closed['period_name']} is closed. Reopen it before generating.",
            ))
            continue
        previews.append(OccurrencePreview(**base, state="Due"))
    return previews


def preview_due(
    client_id: int,
    through_date: Optional[date] = None,
    include_handled: bool = True,
) -> List[OccurrencePreview]:
    """Preview recurring occurrences without changing the book."""
    from models.client import Client

    through = through_date or date.today()
    if not isinstance(through, date):
        through = date.fromisoformat(str(through))
    client = Client.get_by_id(client_id)
    if not client:
        raise ValueError("Client not found.")

    schedules = RecurringSchedule.get_all(client_id, active_only=True)
    templates = {
        template.id: template for template in JournalEntryTemplate.get_all(client_id)
    }
    previews = []
    with get_cursor() as cursor:
        for schedule in schedules:
            template = templates.get(schedule.template_id)
            if not template:
                continue
            previews.extend(_preview_schedule(
                cursor, client_id, template, schedule,
                client.fiscal_year_end_month, through,
            ))
    if not include_handled:
        previews = [item for item in previews if item.state in ("Due", "Blocked")]
    return sorted(
        previews,
        key=lambda item: (item.entry_date, item.template_name.lower(), item.schedule_id),
    )


def _find_preview(
    client_id: int, schedule_id: int, period_start: date, period_end: date
) -> OccurrencePreview:
    matches = [
        item for item in preview_due(client_id, through_date=period_end)
        if item.schedule_id == schedule_id
        and item.period_start == period_start
        and item.period_end == period_end
    ]
    if not matches:
        raise ValueError("This schedule and fiscal period are not eligible.")
    return matches[0]


def _draft_from_template(
    cursor, template: JournalEntryTemplate, entry_date: date
) -> DraftEntry:
    account_ids = [line.account_id for line in template.lines]
    placeholders = ", ".join("?" for _ in account_ids)
    cursor.execute(
        f"SELECT id, account_number FROM accounts WHERE client_id = ? "
        f"AND is_active = 1 AND id IN ({placeholders})",
        [template.client_id, *account_ids],
    )
    numbers = {row["id"]: row["account_number"] for row in cursor.fetchall()}
    if set(numbers) != set(account_ids):
        raise ValueError(
            "A template account is inactive. Reactivate it or update the template."
        )
    return DraftEntry(
        client_id=template.client_id,
        proposed_by=f"Recurring schedule: {template.name}",
        entry_date=entry_date.isoformat(),
        entry_type=template.entry_type,
        description=template.description,
        rationale=f"Generated from recurring template {template.name}.",
        lines=[
            DraftLine(
                account_number=numbers[line.account_id],
                debit_cents=line.debit_cents,
                credit_cents=line.credit_cents,
                memo=line.memo,
            )
            for line in template.lines
        ],
    )


def _generation_snapshot(template: JournalEntryTemplate, reversal_rule: str) -> dict:
    return {
        "reversal_rule": reversal_rule,
        "template_name": template.name,
        "template_source_reference": template.source_reference or "",
    }


def _link_draft(cursor, client_id, occurrence_id, draft_id, role, generation,
                snapshot, **lineage) -> None:
    """Persist the instructions and their audit record in the draft transaction."""
    cursor.execute(
        """INSERT INTO recurring_occurrence_drafts
           (occurrence_id, draft_entry_id, role, generation_number,
            snapshot_reversal_rule, snapshot_template_name, snapshot_source_reference)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (occurrence_id, draft_id, role, generation, snapshot["reversal_rule"],
         snapshot["template_name"], snapshot["template_source_reference"]),
    )
    AuditLog.write(
        cursor, client_id, "recurring_occurrence_drafts", cursor.lastrowid, "INSERT",
        new_values={
            "occurrence_id": occurrence_id, "draft_entry_id": draft_id,
            "role": role, "generation_number": generation,
            "snapshot": {key: snapshot[key] for key in (
                "reversal_rule", "template_name", "template_source_reference",
            )},
            **lineage,
        },
    )


def generate_occurrence(
    client_id: int,
    schedule_id: int,
    period_start: date,
    period_end: date,
) -> dict:
    """Generate one primary draft, or return the existing occurrence safely."""
    preview = _find_preview(client_id, schedule_id, period_start, period_end)
    if preview.state == "Handled":
        return {
            "result": "already_generated", "occurrence_id": preview.occurrence_id,
            "draft_id": preview.draft_id,
        }
    if preview.state == "Skipped":
        return {"result": "skipped", "occurrence_id": preview.occurrence_id}
    if preview.state != "Due":
        raise ValueError(preview.reason or "This occurrence cannot be generated.")

    conn = get_connection()
    try:
        # Keep template, schedule, and generation-time instructions consistent
        # while reading them and writing the resulting draft.
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        template = JournalEntryTemplate.get_by_id(preview.template_id, client_id)
        schedule = RecurringSchedule.get_by_id(schedule_id, client_id)
        if not template or not schedule or not schedule.is_active:
            raise ValueError("The recurring schedule is no longer active.")
        closed = _closed_year_for(cursor, client_id, preview.entry_date)
        if closed:
            raise ValueError(
                f"{closed['period_name']} is closed. Reopen it before generating."
            )
        draft = _draft_from_template(cursor, template, preview.entry_date)
        actor = current_actor()
        cursor.execute(
            """INSERT INTO recurring_occurrences
               (schedule_id, period_name, period_type, period_start, period_end,
                scheduled_entry_date, disposition, generated_at, generated_by)
               VALUES (?, ?, ?, ?, ?, ?, 'Generated',
                       datetime('now', 'localtime'), ?)""",
            (
                schedule_id, preview.period_name, preview.period_type,
                preview.period_start.isoformat(), preview.period_end.isoformat(),
                preview.entry_date.isoformat(), actor,
            ),
        )
        occurrence_id = cursor.lastrowid
        AuditLog.write(
            cursor, client_id, "recurring_occurrences", occurrence_id, "INSERT",
            new_values={
                "schedule_id": schedule_id, "template_id": template.id,
                "template_name": template.name, "period_name": preview.period_name,
                "period_start": preview.period_start, "period_end": preview.period_end,
                "scheduled_entry_date": preview.entry_date,
                "disposition": "Generated", "generated_by": actor,
            },
        )
        draft.save(conn=conn)
        _link_draft(
            cursor, client_id, occurrence_id, draft.id, "Primary", 1,
            _generation_snapshot(template, schedule.reversal_rule),
        )
        conn.commit()
        return {
            "result": "generated", "occurrence_id": occurrence_id,
            "draft_id": draft.id,
        }
    except Exception:
        conn.rollback()
        with get_cursor() as cursor:
            existing = cursor.execute(
                """SELECT id, disposition FROM recurring_occurrences
                   WHERE schedule_id = ? AND period_start = ? AND period_end = ?""",
                (schedule_id, period_start.isoformat(), period_end.isoformat()),
            ).fetchone()
            if existing:
                if existing["disposition"] == "Skipped":
                    return {
                        "result": "skipped", "occurrence_id": existing["id"],
                    }
                primary = _latest_draft(cursor, existing["id"], "Primary")
                return {
                    "result": "already_generated",
                    "occurrence_id": existing["id"],
                    "draft_id": primary["draft_id"] if primary else None,
                }
        raise
    finally:
        conn.close()


def generate_selected(client_id: int, selections: list[tuple[int, date, date]]) -> dict:
    """Generate selected periods and account for every requested selection."""
    results = {
        "generated": [], "already_generated": [], "skipped": [], "errors": [],
    }
    seen = set()
    for schedule_id, period_start, period_end in selections:
        key = (int(schedule_id), period_start, period_end)
        if key in seen:
            continue
        seen.add(key)
        try:
            result = generate_occurrence(client_id, *key)
        except Exception as exc:
            results["errors"].append({
                "schedule_id": key[0],
                "period_start": key[1].isoformat(),
                "period_end": key[2].isoformat(),
                "error": str(exc),
            })
        else:
            bucket = result["result"]
            results.setdefault(bucket, []).append(result)
    results["requested_count"] = len(seen)
    results["accounted_count"] = sum(
        len(results[name])
        for name in ("generated", "already_generated", "skipped", "errors")
    )
    return results


def skip_occurrence(
    client_id: int,
    schedule_id: int,
    period_start: date,
    period_end: date,
    reason: str,
) -> dict:
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("Explain why this recurring entry is being skipped.")
    preview = _find_preview(client_id, schedule_id, period_start, period_end)
    if preview.state == "Skipped":
        return {"result": "already_skipped", "occurrence_id": preview.occurrence_id}
    if preview.state == "Handled":
        raise ValueError("A generated occurrence cannot be skipped.")
    if preview.state != "Due":
        raise ValueError(preview.reason or "This occurrence cannot be skipped.")

    actor = current_actor()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO recurring_occurrences
               (schedule_id, period_name, period_type, period_start, period_end,
                scheduled_entry_date, disposition, skipped_at, skipped_by, skip_reason)
               VALUES (?, ?, ?, ?, ?, ?, 'Skipped',
                       datetime('now', 'localtime'), ?, ?)""",
            (
                schedule_id, preview.period_name, preview.period_type,
                period_start.isoformat(), period_end.isoformat(),
                preview.entry_date.isoformat(), actor, reason,
            ),
        )
        occurrence_id = cursor.lastrowid
        AuditLog.write(
            cursor, client_id, "recurring_occurrences", occurrence_id, "INSERT",
            new_values={
                "schedule_id": schedule_id, "period_name": preview.period_name,
                "period_start": period_start, "period_end": period_end,
                "scheduled_entry_date": preview.entry_date,
                "disposition": "Skipped", "skip_reason": reason,
                "skipped_by": actor,
            },
        )
        conn.commit()
        return {"result": "skipped", "occurrence_id": occurrence_id}
    except Exception:
        conn.rollback()
        with get_cursor() as cursor:
            existing = cursor.execute(
                """SELECT id, disposition FROM recurring_occurrences
                   WHERE schedule_id = ? AND period_start = ? AND period_end = ?""",
                (schedule_id, period_start.isoformat(), period_end.isoformat()),
            ).fetchone()
            if existing:
                if existing["disposition"] == "Skipped":
                    return {
                        "result": "already_skipped",
                        "occurrence_id": existing["id"],
                    }
                raise ValueError("This occurrence was generated and cannot be skipped.")
        raise
    finally:
        conn.close()


def undo_skip(client_id: int, occurrence_id: int) -> None:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT ro.*, t.client_id FROM recurring_occurrences ro
               JOIN recurring_schedules rs ON rs.id = ro.schedule_id
               JOIN journal_entry_templates t ON t.id = rs.template_id
               WHERE ro.id = ? AND t.client_id = ?""",
            (occurrence_id, client_id),
        )
        row = cursor.fetchone()
        if not row or row["disposition"] != "Skipped":
            raise ValueError("Skipped occurrence not found for the selected client.")
        scheduled = date.fromisoformat(row["scheduled_entry_date"])
        closed = _closed_year_for(cursor, client_id, scheduled)
        if closed:
            raise ValueError(
                f"{closed['period_name']} is closed. Reopen it before undoing the skip."
            )
        old_values = {
            "schedule_id": row["schedule_id"], "period_name": row["period_name"],
            "period_start": row["period_start"], "period_end": row["period_end"],
            "scheduled_entry_date": row["scheduled_entry_date"],
            "disposition": row["disposition"], "skip_reason": row["skip_reason"],
            "skipped_by": row["skipped_by"], "skipped_at": row["skipped_at"],
        }
        cursor.execute("DELETE FROM recurring_occurrences WHERE id = ?", (occurrence_id,))
        AuditLog.write(
            cursor, client_id, "recurring_occurrences", occurrence_id, "DELETE",
            old_values=old_values,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def regenerate_occurrence(
    client_id: int, occurrence_id: int, role: str = "Primary"
) -> dict:
    role = str(role or "").strip().title()
    if role not in ("Primary", "Reversal"):
        raise ValueError("Recurring draft role must be Primary or Reversal.")
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        row = cursor.execute(
            """SELECT ro.*, rs.is_active, rs.reversal_rule, t.id template_id
               FROM recurring_occurrences ro
               JOIN recurring_schedules rs ON rs.id = ro.schedule_id
               JOIN journal_entry_templates t ON t.id = rs.template_id
               WHERE ro.id = ? AND t.client_id = ?
                 AND ro.disposition = 'Generated'""",
            (occurrence_id, client_id),
        ).fetchone()
        if not row:
            raise ValueError("Generated occurrence not found for the selected client.")
        latest = _latest_draft(cursor, occurrence_id, role)
        if not latest or latest["draft_status"] != "rejected":
            raise ValueError(
                f"Only a rejected {role.lower()} draft can be regenerated."
            )

        if role == "Primary":
            if not row["is_active"]:
                raise ValueError(
                    "Reactivate the schedule before regenerating this draft."
                )
            scheduled = date.fromisoformat(row["scheduled_entry_date"])
            template = JournalEntryTemplate.get_by_id(row["template_id"], client_id)
            if not template or template.archived_at:
                raise ValueError("Restore the template before regenerating this draft.")
            draft = _draft_from_template(cursor, template, scheduled)
            snapshot = _generation_snapshot(template, row["reversal_rule"])
        else:
            primary = cursor.execute(
                """SELECT d.id draft_id, d.posted_entry_id
                   FROM recurring_occurrence_drafts rod
                   JOIN draft_entries d ON d.id = rod.draft_entry_id
                   WHERE rod.occurrence_id = ? AND rod.role = 'Primary'
                     AND d.status = 'approved' AND d.posted_entry_id IS NOT NULL
                   ORDER BY rod.generation_number DESC LIMIT 1""",
                (occurrence_id,),
            ).fetchone()
            if not primary:
                raise ValueError(
                    "The recurring primary must be posted before regenerating "
                    "its reversal."
                )
            rejected = cursor.execute(
                "SELECT * FROM draft_entries WHERE id = ? AND client_id = ?",
                (latest["draft_id"], client_id),
            ).fetchone()
            if not rejected:
                raise ValueError("Rejected reversal draft not found.")
            source = DraftEntry._from_row(rejected)
            snapshot = recurring_draft_context(conn, source.id, client_id)
            scheduled = date.fromisoformat(source.entry_date)
            draft = DraftEntry(
                client_id=client_id,
                proposed_by=source.proposed_by,
                entry_date=source.entry_date,
                entry_type=source.entry_type,
                description=source.description,
                rationale=source.rationale,
                lines=[
                    DraftLine(
                        account_number=line.account_number,
                        debit_cents=line.debit_cents,
                        credit_cents=line.credit_cents,
                        memo=line.memo,
                    )
                    for line in source.lines
                ],
            )

        closed = _closed_year_for(cursor, client_id, scheduled)
        if closed:
            raise ValueError(
                f"{closed['period_name']} is closed. Reopen it before regenerating."
            )
        draft.save(conn=conn)
        generation = int(latest["generation_number"]) + 1
        _link_draft(
            cursor, client_id, occurrence_id, draft.id, role, generation,
            snapshot, regenerated_from_draft_id=latest["draft_id"],
        )
        conn.commit()
        return {
            "result": "regenerated", "occurrence_id": occurrence_id,
            "draft_id": draft.id, "role": role,
            "generation_number": generation,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def recurring_draft_context(conn, draft_id: int, client_id: int) -> Optional[dict]:
    """Return recurring lineage for one client-owned draft, if it has any."""
    cursor = conn.cursor()
    row = cursor.execute(
        """SELECT rod.id link_id, rod.role, rod.generation_number,
                  ro.id occurrence_id, ro.period_name, ro.period_start,
                  ro.period_end, ro.scheduled_entry_date,
                  rs.id schedule_id, t.id template_id,
                  rod.snapshot_reversal_rule reversal_rule,
                  rod.snapshot_template_name template_name,
                  rod.snapshot_source_reference template_source_reference,
                  d.proposed_by
           FROM recurring_occurrence_drafts rod
           JOIN recurring_occurrences ro ON ro.id = rod.occurrence_id
           JOIN recurring_schedules rs ON rs.id = ro.schedule_id
           JOIN journal_entry_templates t ON t.id = rs.template_id
           JOIN draft_entries d ON d.id = rod.draft_entry_id
           WHERE rod.draft_entry_id = ? AND t.client_id = ?""",
        (draft_id, client_id),
    ).fetchone()
    if not row:
        return None
    context = dict(row)
    context["snapshot_available"] = all(context[key] is not None for key in (
        "reversal_rule", "template_name", "template_source_reference",
    ))
    context["approval_blocked_reason"] = ""
    if not context["snapshot_available"]:
        # Old drafts retain their stored attribution. Never substitute a live
        # template name/reference and imply it was captured at generation.
        context["template_name"] = row["proposed_by"].removeprefix(
            "Recurring schedule: "
        )
        context["template_source_reference"] = ""
        if row["role"] == "Primary":
            context["approval_blocked_reason"] = (
                "This draft was created before recurring instructions were saved. "
                "Reject it, review the template and reversal setting, then regenerate "
                "the period in Templates & recurring before approving."
            )
        else:
            # A reversal already contains the opposite amounts and its date;
            # its posted-primary link is sufficient to approve/regenerate it.
            context["reversal_rule"] = "NextDay"
    if row["role"] == "Reversal":
        primary = cursor.execute(
            """SELECT d.id draft_id, d.posted_entry_id
               FROM recurring_occurrence_drafts rod
               JOIN draft_entries d ON d.id = rod.draft_entry_id
               WHERE rod.occurrence_id = ? AND rod.role = 'Primary'
                 AND d.status = 'approved' AND d.posted_entry_id IS NOT NULL
               ORDER BY rod.generation_number DESC LIMIT 1""",
            (row["occurrence_id"],),
        ).fetchone()
        context["primary_draft_id"] = primary["draft_id"] if primary else None
        context["primary_posted_entry_id"] = (
            primary["posted_entry_id"] if primary else None
        )
    return context


def recurring_draft_contexts(client_id: int, draft_ids: List[int]) -> dict[int, dict]:
    """Return recurring lineage keyed by draft id for one client."""
    contexts = {}
    with get_cursor() as cursor:
        conn = cursor.connection
        for draft_id in dict.fromkeys(int(value) for value in draft_ids):
            context = recurring_draft_context(conn, draft_id, client_id)
            if context:
                contexts[draft_id] = context
    return contexts


def occurrence_history(client_id: int, limit: int = 100) -> List[dict]:
    """Recent occurrence and draft generations for the Templates view."""
    with get_cursor() as cursor:
        cursor.execute(
            """SELECT ro.id occurrence_id, ro.period_name, ro.period_start,
                      ro.period_end, ro.scheduled_entry_date, ro.disposition,
                      ro.skip_reason, ro.generated_at, ro.generated_by,
                      ro.skipped_at, ro.skipped_by,
                      rs.id schedule_id, t.id template_id, t.name template_name
               FROM recurring_occurrences ro
               JOIN recurring_schedules rs ON rs.id = ro.schedule_id
               JOIN journal_entry_templates t ON t.id = rs.template_id
               WHERE t.client_id = ?
               ORDER BY ro.period_end DESC, ro.id DESC LIMIT ?""",
            (client_id, max(1, int(limit))),
        )
        occurrences = [dict(row) for row in cursor.fetchall()]
        for item in occurrences:
            cursor.execute(
                """SELECT rod.role, rod.generation_number, d.id draft_id,
                          d.status draft_status, d.posted_entry_id
                   FROM recurring_occurrence_drafts rod
                   JOIN draft_entries d ON d.id = rod.draft_entry_id
                   WHERE rod.occurrence_id = ?
                   ORDER BY CASE rod.role WHEN 'Primary' THEN 0 ELSE 1 END,
                            rod.generation_number""",
                (item["occurrence_id"],),
            )
            item["drafts"] = [dict(row) for row in cursor.fetchall()]
    return occurrences


def rejected_recoveries(client_id: int) -> List[dict]:
    """Latest rejected primary/reversal generations that can be recovered.

    This query deliberately does not depend on the schedule's current
    frequency, active state, or the template's archive state. A posted
    primary's rejected reversal remains an accounting obligation even after
    somebody reorganizes or archives the schedule that created it.
    """
    with get_cursor() as cursor:
        cursor.execute(
            """SELECT ro.id occurrence_id, ro.period_name, ro.period_start,
                      ro.period_end, t.name template_name, rod.role,
                      rod.generation_number, d.id draft_id, d.entry_date
               FROM recurring_occurrence_drafts rod
               JOIN recurring_occurrences ro ON ro.id = rod.occurrence_id
               JOIN recurring_schedules rs ON rs.id = ro.schedule_id
               JOIN journal_entry_templates t ON t.id = rs.template_id
               JOIN draft_entries d ON d.id = rod.draft_entry_id
               WHERE t.client_id = ? AND d.status = 'rejected'
                 AND rod.generation_number = (
                     SELECT MAX(latest.generation_number)
                     FROM recurring_occurrence_drafts latest
                     WHERE latest.occurrence_id = rod.occurrence_id
                       AND latest.role = rod.role
                 )
               ORDER BY ro.period_end, t.name COLLATE NOCASE, rod.role""",
            (client_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


def create_reversal_after_primary_approval(
    conn,
    primary_draft: DraftEntry,
    primary_entry_id: int,
    context: Optional[dict] = None,
) -> Optional[int]:
    """Create the one linked reversal draft inside the approval transaction."""
    context = context or recurring_draft_context(
        conn, primary_draft.id, primary_draft.client_id
    )
    if not context or context["role"] != "Primary":
        return None
    if context["reversal_rule"] != "NextDay":
        return None

    cursor = conn.cursor()
    existing = cursor.execute(
        """SELECT rod.draft_entry_id
           FROM recurring_occurrence_drafts rod
           WHERE rod.occurrence_id = ? AND rod.role = 'Reversal'
           ORDER BY rod.generation_number DESC LIMIT 1""",
        (context["occurrence_id"],),
    ).fetchone()
    if existing:
        return existing["draft_entry_id"]

    reversal_date = date.fromisoformat(context["period_end"]) + timedelta(days=1)
    reversal = DraftEntry(
        client_id=primary_draft.client_id,
        proposed_by=f"Recurring schedule: {context['template_name']}",
        entry_date=reversal_date.isoformat(),
        entry_type=primary_draft.entry_type,
        description=f"Reversal: {primary_draft.description}"[:200],
        rationale=(
            f"Scheduled reversal of journal entry #{primary_entry_id} from "
            f"recurring template {context['template_name']}."
        ),
        lines=[
            DraftLine(
                account_number=line.account_number,
                debit_cents=line.credit_cents,
                credit_cents=line.debit_cents,
                memo=f"Reversal of JE #{primary_entry_id}",
            )
            for line in primary_draft.lines
        ],
    )
    reversal.save(conn=conn)
    _link_draft(
        cursor, primary_draft.client_id, context["occurrence_id"], reversal.id,
        "Reversal", 1, context, primary_draft_id=primary_draft.id,
        primary_posted_entry_id=primary_entry_id,
    )
    return reversal.id
