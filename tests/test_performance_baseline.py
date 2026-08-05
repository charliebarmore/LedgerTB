"""Representative performance baselines, using generated temporary data only."""

import json
import os
import time
import tracemalloc
from datetime import date

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from tests.conftest import page_path

from database.connection import get_connection
from models.audit_log import AuditLog
from models.journal_entry import JournalEntry
from services.csv_import import CSVImporter
from services.import_identity import classify_import_duplicates, hash_source
from tests.performance_fixtures import make_large_bank_csv, seed_journal_volume


pytestmark = pytest.mark.performance

# Regression tripwires, not benchmarks: generous enough that a busy shared
# machine (app + build + suite at once) doesn't flake them, tight enough that
# a real 2x blowup still fails. A 15s ceiling failed at 15.46s under load.
MAX_FIXTURE_SECONDS = float(os.getenv("PROBOOKS_PERF_FIXTURE_SECONDS", "25"))
MAX_QUERY_SECONDS = float(os.getenv("PROBOOKS_PERF_QUERY_SECONDS", "8"))
MAX_PAGE_SECONDS = float(os.getenv("PROBOOKS_PERF_PAGE_SECONDS", "25"))
MAX_CSV_SECONDS = float(os.getenv("PROBOOKS_PERF_CSV_SECONDS", "25"))
MAX_CSV_PEAK_MIB = float(os.getenv("PROBOOKS_PERF_CSV_PEAK_MIB", "512"))


def _timed(action):
    started = time.perf_counter()
    result = action()
    return result, time.perf_counter() - started


def _select_client(monkeypatch, client_id):
    import utils.client_selector as selector

    monkeypatch.setattr(selector, "render_client_selector", lambda: client_id)
    monkeypatch.setattr(st, "page_link", lambda *args, **kwargs: None)


def test_10k_journal_entries_query_and_render_baseline(client_id, accounts, monkeypatch):
    today = date.today()
    start = date(today.year, 1, 1)
    span = max(1, (today - start).days + 1)
    conn = get_connection()
    try:
        stats, fixture_seconds = _timed(lambda: seed_journal_volume(
            conn,
            client_id=client_id,
            debit_account_id=accounts["cash"],
            credit_account_id=accounts["revenue"],
            start_date=start,
            date_span_days=span,
        ))
    finally:
        conn.close()

    summary, summary_seconds = _timed(lambda: JournalEntry.get_filtered_summary(
        client_id, start, today,
    ))
    last_page, page_query_seconds = _timed(lambda: JournalEntry.get_all(
        client_id, start, today, limit=25, offset=9_975,
    ))
    audit_counts, audit_seconds = _timed(lambda: AuditLog.get_filtered_counts(
        client_id, table_name="journal_entries",
    ))

    _select_client(monkeypatch, client_id)
    journal_page = AppTest.from_file(page_path("pages/2_Journal_Entries.py"), default_timeout=MAX_PAGE_SECONDS,
    )
    journal_page.session_state["journal_active_tab"] = "View Entries"
    journals, journal_render_seconds = _timed(journal_page.run)
    audit_page, audit_render_seconds = _timed(lambda: AppTest.from_file(page_path("pages/8_Audit_Trail.py"), default_timeout=MAX_PAGE_SECONDS,
    ).run())

    assert stats.entry_count == 10_000
    assert stats.line_count == 20_000
    assert stats.audit_count == 10_000
    assert JournalEntry.count(client_id) == 10_000
    assert summary["total_count"] == 10_000
    assert summary["total_debits"] == stats.total_cents / 100
    assert summary["total_credits"] == stats.total_cents / 100
    assert summary["regular_count"] == stats.regular_count
    assert summary["adjusting_count"] == stats.adjusting_count
    assert summary["beginning_count"] == stats.beginning_count
    assert len(last_page) == 25
    assert all(len(entry.lines) == 2 for entry in last_page)
    assert audit_counts["total"] == 10_000
    assert not journals.exception
    assert not audit_page.exception
    assert any(metric.label == "Filtered Entries" for metric in journals.metric)
    assert any(button.label == "Next" and not button.disabled for button in journals.button)
    assert any(metric.label == "Total Changes" for metric in audit_page.metric)

    timings = {
        "fixture_seconds": round(fixture_seconds, 4),
        "summary_seconds": round(summary_seconds, 4),
        "last_page_query_seconds": round(page_query_seconds, 4),
        "audit_count_seconds": round(audit_seconds, 4),
        "journal_page_render_seconds": round(journal_render_seconds, 4),
        "audit_page_render_seconds": round(audit_render_seconds, 4),
    }
    print("\nPERF 10k journal entries " + json.dumps(timings, sort_keys=True))
    assert fixture_seconds < MAX_FIXTURE_SECONDS
    assert max(summary_seconds, page_query_seconds, audit_seconds) < MAX_QUERY_SECONDS
    assert max(journal_render_seconds, audit_render_seconds) < MAX_PAGE_SECONDS


def test_50k_csv_parse_and_duplicate_review_baseline(client_id, accounts):
    csv_content, expected_duplicates = make_large_bank_csv()
    source_id = hash_source(csv_content.encode("utf-8"))

    tracemalloc.start()
    try:
        rows, parse_seconds = _timed(lambda: CSVImporter.parse_csv(
            csv_content,
            date_column="Date",
            description_column="Description",
            amount_column="Amount",
            source_id=source_id,
            source_filename="performance-50k.csv",
        ))
        for row in rows:
            row["client_id"] = client_id
            row["bank_account_id"] = accounts["cash"]
        duplicate_count, classify_seconds = _timed(
            lambda: classify_import_duplicates(rows, client_id)
        )
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    peak_mib = peak_bytes / (1024 * 1024)
    assert len(rows) == 50_000
    assert duplicate_count == expected_duplicates
    assert expected_duplicates == 9
    assert sum(1 for row in rows if row.get("is_duplicate")) == expected_duplicates
    assert all(row.get("row_fingerprint") for row in rows)
    assert all(row.get("idempotency_key") for row in rows)

    timings = {
        "csv_bytes": len(csv_content.encode("utf-8")),
        "rows": len(rows),
        "known_duplicates": duplicate_count,
        "parse_seconds": round(parse_seconds, 4),
        "duplicate_classification_seconds": round(classify_seconds, 4),
        "peak_mib": round(peak_mib, 2),
    }
    print("\nPERF 50k CSV " + json.dumps(timings, sort_keys=True))
    assert parse_seconds < MAX_CSV_SECONDS
    assert classify_seconds < MAX_CSV_SECONDS
    assert peak_mib < MAX_CSV_PEAK_MIB
