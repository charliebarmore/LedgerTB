"""Verify that what landed in the ledger matches the file that was uploaded.

An import can under-deliver quietly: a row with an unparseable date is skipped,
a duplicate is held back, an upload is run against the wrong account. The trial
balance still balances afterwards — double-entry guarantees that — so a missing
row leaves no trace a balancing check would catch. This module compares the
imported rows against their source and names the difference.

Two independent checks, useful at different times:

* :func:`check_row_continuity` needs nothing but the database. Every imported
  row records the line it came from, so a gap in that sequence means a row
  didn't make it.
* :func:`verify_against_source` needs the original file re-supplied, and is
  the stronger check: it compares date, description, and amount row by row.
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

from services.import_identity import canonical_description


def _match_key(txn_date, description: str, amount: float) -> Tuple[str, str, int]:
    """Identity of a transaction for matching: date, text, and amount.

    Amount is compared in whole cents. Source files and the database round trip
    through float, and 0.1 + 0.2 style drift would otherwise report a false
    mismatch on rows that are actually identical.
    """
    iso = txn_date.isoformat() if isinstance(txn_date, date) else str(txn_date or "")[:10]
    return (iso, canonical_description(description), int(round((amount or 0) * 100)))


@dataclass
class ContinuityReport:
    """Whether the imported rows form an unbroken run of source line numbers."""

    first_row: Optional[int] = None
    last_row: Optional[int] = None
    present_count: int = 0
    missing_rows: List[int] = field(default_factory=list)
    unnumbered_count: int = 0

    @property
    def is_clean(self) -> bool:
        return not self.missing_rows

    @property
    def expected_count(self) -> int:
        if self.first_row is None or self.last_row is None:
            return self.present_count
        return self.last_row - self.first_row + 1


def check_row_continuity(rows) -> ContinuityReport:
    """Report gaps in the source line numbers of one batch's imported rows.

    Only detects rows missing from the *middle* of a file — rows dropped from
    the end leave no gap behind, which is why this check does not stand in for
    :func:`verify_against_source`.
    """
    numbered = sorted(r.source_row_number for r in rows if r.source_row_number is not None)
    unnumbered = sum(1 for r in rows if r.source_row_number is None)

    if not numbered:
        return ContinuityReport(present_count=0, unnumbered_count=unnumbered)

    present = set(numbered)
    return ContinuityReport(
        first_row=numbered[0],
        last_row=numbered[-1],
        present_count=len(numbered),
        missing_rows=[n for n in range(numbered[0], numbered[-1] + 1) if n not in present],
        unnumbered_count=unnumbered,
    )


@dataclass
class VerificationReport:
    """Row-by-row comparison of a source file against what was imported."""

    matched: List[dict] = field(default_factory=list)
    missing_from_import: List[dict] = field(default_factory=list)
    not_in_source: List[object] = field(default_factory=list)
    source_count: int = 0
    imported_count: int = 0
    source_total: float = 0.0
    imported_total: float = 0.0

    @property
    def is_clean(self) -> bool:
        return not self.missing_from_import and not self.not_in_source

    @property
    def difference(self) -> float:
        return round(self.imported_total - self.source_total, 2)


def verify_against_source(imported_rows, source_rows: List[Dict]) -> VerificationReport:
    """Compare a re-supplied source file against the rows imported from it.

    Matching is on (date, normalized description, amount) and is
    multiplicity-aware: a statement with three identical $79.00 charges must
    match exactly three imported rows, so a set-based comparison would wrongly
    call two of them missing. Consumed matches are therefore removed as they
    pair off.

    Args:
        imported_rows: ``ImportedTransaction`` records for the batch.
        source_rows: dicts with ``date``, ``description``, ``amount`` — the
            shape ``CSVImporter.parse_csv`` returns.
    """
    remaining: Dict[Tuple, List] = {}
    for row in imported_rows:
        remaining.setdefault(_match_key(row.transaction_date, row.description, row.amount), []).append(row)

    matched: List[dict] = []
    missing: List[dict] = []

    for source in source_rows:
        key = _match_key(source.get("date"), source.get("description", ""), source.get("amount", 0))
        candidates = remaining.get(key)
        if candidates:
            matched.append({"source": source, "imported": candidates.pop(0)})
            if not candidates:
                del remaining[key]
        else:
            missing.append(source)

    unmatched_imports = [row for rows in remaining.values() for row in rows]

    return VerificationReport(
        matched=matched,
        missing_from_import=missing,
        not_in_source=unmatched_imports,
        source_count=len(source_rows),
        imported_count=len(imported_rows),
        source_total=round(sum(r.get("amount") or 0 for r in source_rows), 2),
        imported_total=round(sum(r.amount or 0 for r in imported_rows), 2),
    )
