"""What was recently *done* to a client's books, as opposed to what is in them.

The dashboard used to list recent journal entries, which for an imported month
is the import itself re-printed one row at a time — forty-five lines that say
nothing about the work. This feed reports at the level of the action taken:
"imported 45 transactions into Credit Card Payable - AMEX", "posted AJE-001".

Three tables contribute, and they do not agree about time:

* ``imported_transactions.created_at`` — UTC, converted to local by
  :meth:`ImportedTransaction.get_batch_summaries`.
* ``journal_entries.created_at`` — UTC, converted to local by
  :meth:`JournalEntry.get_hand_keyed_recent`.
* ``audit_log.changed_at`` — already local; it is stamped by the application
  rather than by SQLite (see the UTC audit-trail fix).

Every timestamp reaching this module is therefore local, and the ordering
below assumes it. Anything added later must convert before it gets here or the
feed will interleave wrongly by the UTC offset.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from models.audit_log import AuditLog
from utils.dates import short_date
from models.journal_entry import JournalEntry
from models.transaction import ImportedTransaction

# Row-level INSERT/UPDATE/DELETE are deliberately excluded: they are the noise
# this feed exists to replace. These are the actions a person would describe as
# something they did.
NOTABLE_AUDIT_ACTIONS = (
    "EXPORT", "BACKUP", "RESTORE", "CLOSE", "REOPEN", "REVERSE", "OVERRIDE",
    "REVIEW",
)

_AUDIT_PHRASING = {
    "EXPORT": "Exported",
    "BACKUP": "Backed up the books",
    "RESTORE": "Restored from a backup",
    "CLOSE": "Closed a period",
    "REOPEN": "Reopened a period",
    "REVERSE": "Reversed a journal entry",
    "OVERRIDE": "Overrode a duplicate warning",
    "REVIEW": "Ran a book review",
}

_ENTRY_PHRASING = {
    "Adjusting": "Posted adjusting entry",
    "Closing": "Posted closing entry",
    "Beginning Balance": "Entered beginning balances",
    "Regular": "Entered journal entry",
}


@dataclass
class ActivityEvent:
    """One thing that was done, ready to render."""

    when: Optional[datetime]
    kind: str          # import | journal | audit
    summary: str       # the headline — what was done
    detail: Optional[str] = None
    page: Optional[str] = None   # page to open for more
    actor: Optional[str] = None  # who did it (None on rows from before tracking)


def _parse(value) -> Optional[datetime]:
    """Accept the several shapes SQLite hands back for a timestamp."""
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("T", " ").split(".")[0])
    except (TypeError, ValueError):
        return None


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _account_label(batch: dict) -> str:
    if batch["account_count"] > 1:
        return _plural(batch["account_count"], "account")
    if batch["account_number"]:
        return f"{batch['account_number']} - {batch['account_name']}"
    return batch["account_name"] or "an account"


def _import_events(client_id: int) -> List[ActivityEvent]:
    events = []
    for batch in ImportedTransaction.get_batch_summaries(client_id):
        source = (
            _plural(batch["filename_count"], "file")
            if batch["filename_count"] > 1
            else batch["source_filename"]
        )
        detail = f"from {source}" if source else None

        unposted = batch["pending_count"] + batch["categorized_count"]
        if unposted:
            waiting = f"{unposted} still to review"
            detail = f"{detail} · {waiting}" if detail else waiting

        events.append(ActivityEvent(
            when=_parse(batch["imported_at"]),
            kind="import",
            summary=(
                f"Imported {_plural(batch['row_count'], 'transaction')} "
                f"into {_account_label(batch)}"
            ),
            detail=detail,
            page="pages/4_Import_Transactions.py",
            actor=batch.get("created_by"),
        ))
    return events


def _journal_events(client_id: int, limit: int) -> List[ActivityEvent]:
    events = []
    for entry in JournalEntry.get_hand_keyed_recent(client_id, limit=limit):
        phrasing = _ENTRY_PHRASING.get(entry["entry_type"], "Entered journal entry")
        # An adjusting entry's AJE-001 label is how it is referred to in review,
        # so prefer it over the internal entry id.
        if entry["entry_type"] == "Adjusting" and entry["aje_reference"]:
            summary = f"{phrasing} {entry['aje_reference']}"
        elif entry["entry_type"] == "Beginning Balance":
            summary = phrasing
        else:
            summary = f"{phrasing} #{entry['id']}"

        pieces = []
        if entry["description"]:
            pieces.append(entry["description"])
        if entry["entry_date"]:
            pieces.append(f"dated {entry['entry_date']:%m/%d/%Y}")
        pieces.append(f"{entry['total_debits']:,.2f}")

        events.append(ActivityEvent(
            when=_parse(entry["created_at"]),
            kind="journal",
            summary=summary,
            detail=" · ".join(pieces),
            page="pages/2_Journal_Entries.py",
            actor=entry.get("created_by"),
        ))
    return events


def _audit_events(client_id: int, limit: int) -> List[ActivityEvent]:
    """Notable non-CRUD events.

    Queried one action at a time on purpose: audit_log is dominated by row-level
    INSERTs (one per imported transaction), so a single recent-rows query would
    push a period close or an export out of range after any sizeable import.
    """
    events = []
    for action in NOTABLE_AUDIT_ACTIONS:
        for log in AuditLog.get_all(client_id, action=action, limit=limit):
            summary = _AUDIT_PHRASING.get(action, action.title())
            if action == "EXPORT":
                # table_name carries the event slug, e.g. trial_balance_worksheet_export
                what = (log.table_name or "").replace("_export", "").replace("_", " ").strip()
                summary = f"Exported {what}" if what else "Exported a report"
            elif action == "REVERSE" and log.record_id:
                summary = f"Reversed journal entry #{log.record_id}"

            events.append(ActivityEvent(
                when=_parse(log.changed_at),
                kind="audit",
                summary=summary,
                detail=None,
                page="pages/8_Audit_Trail.py",
                actor=log.performed_by,
            ))
    return events


def describe_when(when: Optional[datetime], now: Optional[datetime] = None) -> str:
    """Phrase a timestamp the way someone recalling their own work would.

    Recent work is easier to place relatively ("2 hours ago") while older work
    is easier to place absolutely, so this switches to a date after a week.
    """
    if when is None:
        return ""
    now = now or datetime.now()
    seconds = (now - when).total_seconds()

    if seconds < 0:          # clock skew; don't claim the future
        return short_date(when)
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours} hour ago" if hours == 1 else f"{hours} hours ago"
    if seconds < 172800:
        return "yesterday"
    if seconds < 604800:
        return f"{int(seconds // 86400)} days ago"
    return short_date(when)


def get_recent_activity(client_id: int, limit: int = 6) -> List[ActivityEvent]:
    """The most recent things done to this client's books, newest first.

    Events with no usable timestamp sort last rather than being dropped — a
    missing timestamp should not hide that the work happened.
    """
    events = (
        _import_events(client_id)
        + _journal_events(client_id, limit)
        + _audit_events(client_id, limit)
    )
    events.sort(key=lambda e: (e.when is not None, e.when or datetime.min), reverse=True)
    return events[:limit]
