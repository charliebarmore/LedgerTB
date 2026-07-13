"""Deterministic, generated performance data for representative ProBooks loads.

These helpers intentionally bypass model-by-model writes: their job is to
construct a realistic database quickly so read/query/render behavior can be
measured. They only run against the temporary database supplied by pytest.
"""

import csv
import json
from dataclasses import dataclass
from datetime import date, timedelta
from io import StringIO


@dataclass(frozen=True)
class JournalFixtureStats:
    entry_count: int
    line_count: int
    audit_count: int
    total_cents: int
    regular_count: int
    adjusting_count: int
    beginning_count: int


def seed_journal_volume(
    conn,
    *,
    client_id: int,
    debit_account_id: int,
    credit_account_id: int,
    entry_count: int = 10_000,
    start_date: date,
    date_span_days: int,
) -> JournalFixtureStats:
    """Create balanced entries, two lines each, and matching audit history."""
    if entry_count < 1:
        raise ValueError("entry_count must be positive")
    if date_span_days < 1:
        raise ValueError("date_span_days must be positive")

    headers = []
    amounts = []
    type_counts = {"Regular": 0, "Adjusting": 0, "Beginning Balance": 0}
    for index in range(entry_count):
        if index % 100 == 0:
            entry_type = "Beginning Balance"
        elif index % 20 == 0:
            entry_type = "Adjusting"
        else:
            entry_type = "Regular"
        type_counts[entry_type] += 1
        amount_cents = 100 + (index % 100_000)
        amounts.append(amount_cents)
        entry_date = start_date + timedelta(days=index % date_span_days)
        headers.append((
            client_id,
            entry_date.isoformat(),
            f"Performance fixture entry {index + 1:05d}",
            f"PERF-{index + 1:05d}",
            entry_type,
            f"AJE-{index + 1:05d}" if entry_type == "Adjusting" else None,
        ))

    cursor = conn.cursor()
    cursor.executemany(
        """
        INSERT INTO journal_entries
            (client_id, entry_date, description, source_reference, entry_type, aje_reference)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        headers,
    )
    rows = cursor.execute(
        """
        SELECT id, entry_date, description, source_reference, entry_type, aje_reference
        FROM journal_entries
        WHERE client_id = ? AND source_reference LIKE 'PERF-%'
        ORDER BY source_reference
        """,
        (client_id,),
    ).fetchall()
    if len(rows) != entry_count:
        raise RuntimeError(f"Expected {entry_count} fixture entries; found {len(rows)}.")

    lines = []
    audits = []
    for index, row in enumerate(rows):
        amount_cents = amounts[index]
        lines.extend((
            (row["id"], debit_account_id, amount_cents, 0, "Performance debit"),
            (row["id"], credit_account_id, 0, amount_cents, "Performance credit"),
        ))
        audits.append((
            client_id,
            "journal_entries",
            row["id"],
            "INSERT",
            json.dumps({
                "entry_date": row["entry_date"],
                "description": row["description"],
                "source_reference": row["source_reference"],
                "entry_type": row["entry_type"],
                "total_debits": amount_cents / 100,
                "total_credits": amount_cents / 100,
            }, separators=(",", ":")),
            "performance-fixture",
        ))

    cursor.executemany(
        """
        INSERT INTO journal_entry_lines
            (journal_entry_id, account_id, debit, credit, memo)
        VALUES (?, ?, ?, ?, ?)
        """,
        lines,
    )
    cursor.executemany(
        """
        INSERT INTO audit_log
            (client_id, table_name, record_id, action, new_values, session_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        audits,
    )
    conn.commit()

    return JournalFixtureStats(
        entry_count=entry_count,
        line_count=len(lines),
        audit_count=len(audits),
        total_cents=sum(amounts),
        regular_count=type_counts["Regular"],
        adjusting_count=type_counts["Adjusting"],
        beginning_count=type_counts["Beginning Balance"],
    )


def make_large_bank_csv(
    row_count: int = 50_000,
    *,
    duplicate_every: int = 5_000,
) -> tuple[str, int]:
    """Generate a large bank CSV and the known within-file duplicate count."""
    if row_count < 1:
        raise ValueError("row_count must be positive")
    if duplicate_every < 2:
        raise ValueError("duplicate_every must be at least 2")

    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["Date", "Description", "Amount"])
    base_date = date(2025, 1, 1)
    previous = None
    duplicate_count = 0
    for index in range(row_count):
        if index and index % duplicate_every == 0:
            row = previous
            duplicate_count += 1
        else:
            amount_cents = (index % 250_000) + 1
            if index % 3:
                amount_cents = -amount_cents
            row = (
                (base_date + timedelta(days=index % 730)).strftime("%m/%d/%Y"),
                f"Fixture merchant {index + 1:05d} reference {1_000_000 + index}",
                f"{amount_cents / 100:.2f}",
            )
        writer.writerow(row)
        previous = row
    return output.getvalue(), duplicate_count
