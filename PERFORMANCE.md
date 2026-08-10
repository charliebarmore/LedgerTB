# Performance baselines

LedgerTB has generated, repeatable volume fixtures before query or rendering
optimization begins. The fixtures never read or alter the live books database.
Pytest creates a temporary SQLite database and deletes it after each test.

## Representative loads

- **Journal workload:** 10,000 journal headers, 20,000 balanced lines, and
  10,000 corresponding audit events. It measures full SQL-backed totals, a deep
  paginated query, audit counts, and Streamlit rendering of the Journal Entries
  and Audit Trail pages.
- **Import workload:** a generated 50,000-row bank CSV with deterministic dates,
  descriptions, amounts, source-row identities, and nine known within-file
  duplicates. It measures CSV parsing, durable fingerprint generation, duplicate
  classification, and peak Python allocation reported by `tracemalloc`.

Run only these baselines and show measurements:

```bash
pytest -q -m performance -s
```

They also run with the normal test suite. The default limits are deliberately
generous regression tripwires, not performance promises or production SLAs.
Slower CI can override them with `LEDGERTB_PERF_FIXTURE_SECONDS`,
`LEDGERTB_PERF_QUERY_SECONDS`, `LEDGERTB_PERF_PAGE_SECONDS`,
`LEDGERTB_PERF_CSV_SECONDS`, and `LEDGERTB_PERF_CSV_PEAK_MIB`.

When optimizing, capture the printed JSON before and after the change on the
same machine. Preserve the correctness assertions: counts, balanced totals,
pagination, audit history, known duplicate count, and durable identities matter
more than a faster but incomplete result.

## Initial reference run

Measured on the development Mac on 2026-07-13. These values establish an
order-of-magnitude comparison point only; use the generated output from the same
machine for meaningful before/after work.

| Workload | Measurement |
| --- | ---: |
| Build 10,000-entry database fixture | 0.09 s |
| Journal totals across 10,000 entries | 0.007 s |
| Deep page query (offset 9,975) | 0.006 s |
| Journal Entries page render | 0.11 s |
| Audit Trail page render | 0.04 s |
| Parse 50,000-row / 2.97 MB CSV | 3.50 s |
| Fingerprint and classify 50,000 rows | 1.73 s |
| CSV parse/classification peak allocation | 45.03 MiB |
