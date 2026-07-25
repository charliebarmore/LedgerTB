"""Verification of imported rows against their source file."""
from datetime import date

from models.transaction import ImportedTransaction
from services.import_verification import (
    check_row_continuity,
    verify_against_source,
)


def _imported(description, amount, day=5, row_number=None):
    return ImportedTransaction(
        transaction_date=date(2026, 1, day),
        description=description,
        amount=amount,
        source_row_number=row_number,
    )


def _source(description, amount, day=5):
    return {"date": date(2026, 1, day), "description": description, "amount": amount}


# --- continuity -------------------------------------------------------------

def test_unbroken_row_numbers_are_clean():
    rows = [_imported("A", -1.00, row_number=n) for n in (2, 3, 4, 5)]
    report = check_row_continuity(rows)

    assert report.is_clean
    assert report.first_row == 2 and report.last_row == 5
    assert report.present_count == 4
    assert report.expected_count == 4


def test_a_gap_in_row_numbers_is_reported():
    rows = [_imported("A", -1.00, row_number=n) for n in (2, 3, 6)]
    report = check_row_continuity(rows)

    assert not report.is_clean
    assert report.missing_rows == [4, 5]
    assert report.present_count == 3
    assert report.expected_count == 5


def test_continuity_handles_rows_with_no_source_number():
    rows = [_imported("A", -1.00, row_number=2), _imported("B", -2.00)]
    report = check_row_continuity(rows)

    assert report.is_clean
    assert report.unnumbered_count == 1
    assert report.present_count == 1


def test_continuity_of_an_empty_batch():
    report = check_row_continuity([])
    assert report.is_clean
    assert report.present_count == 0
    assert report.first_row is None


# --- source comparison ------------------------------------------------------

def test_identical_file_and_import_verify_clean():
    source = [_source("CANVA", -15.00), _source("OBSIDIAN", -5.00, day=6)]
    imported = [_imported("CANVA", -15.00), _imported("OBSIDIAN", -5.00, day=6)]

    report = verify_against_source(imported, source)

    assert report.is_clean
    assert len(report.matched) == 2
    assert report.source_total == -20.00
    assert report.imported_total == -20.00
    assert report.difference == 0.0


def test_row_in_file_but_not_imported_is_flagged():
    source = [_source("CANVA", -15.00), _source("DROPPED", -99.00, day=6)]
    imported = [_imported("CANVA", -15.00)]

    report = verify_against_source(imported, source)

    assert not report.is_clean
    assert len(report.missing_from_import) == 1
    assert report.missing_from_import[0]["description"] == "DROPPED"
    assert report.difference == 99.00


def test_row_imported_but_not_in_file_is_flagged():
    """Catches a batch that picked up rows from somewhere else."""
    source = [_source("CANVA", -15.00)]
    imported = [_imported("CANVA", -15.00), _imported("STRANGER", -7.00, day=6)]

    report = verify_against_source(imported, source)

    assert not report.is_clean
    assert len(report.not_in_source) == 1
    assert report.not_in_source[0].description == "STRANGER"


def test_repeated_identical_charges_match_one_for_one():
    """Three identical charges must match three rows, not collapse to one."""
    source = [_source("CWR DIGITAL LLC", -79.00) for _ in range(3)]
    imported = [_imported("CWR DIGITAL LLC", -79.00) for _ in range(3)]

    report = verify_against_source(imported, source)

    assert report.is_clean
    assert len(report.matched) == 3


def test_missing_one_of_several_identical_charges_is_caught():
    source = [_source("CWR DIGITAL LLC", -79.00) for _ in range(3)]
    imported = [_imported("CWR DIGITAL LLC", -79.00) for _ in range(2)]

    report = verify_against_source(imported, source)

    assert not report.is_clean
    assert len(report.matched) == 2
    assert len(report.missing_from_import) == 1
    assert report.difference == 79.00


def test_amount_mismatch_shows_as_missing_and_unexpected():
    """A wrong amount is a real difference, not a match."""
    source = [_source("CANVA", -15.00)]
    imported = [_imported("CANVA", -15.50)]

    report = verify_against_source(imported, source)

    assert not report.is_clean
    assert len(report.missing_from_import) == 1
    assert len(report.not_in_source) == 1
    assert report.difference == -0.50


def test_harmless_text_differences_still_match():
    """Case and spacing vary between exports; they are not real differences."""
    source = [_source("  Amazon   Web Services ", -32.95)]
    imported = [_imported("AMAZON WEB SERVICES", -32.95)]

    assert verify_against_source(imported, source).is_clean


def test_float_rounding_does_not_create_a_false_mismatch():
    source = [_source("A", 0.1), _source("B", 0.2, day=6)]
    imported = [_imported("A", 0.1), _imported("B", 0.2, day=6)]

    assert verify_against_source(imported, source).is_clean


def test_same_amount_on_a_different_date_does_not_match():
    source = [_source("CANVA", -15.00, day=5)]
    imported = [_imported("CANVA", -15.00, day=9)]

    report = verify_against_source(imported, source)

    assert not report.is_clean
    assert len(report.missing_from_import) == 1
    assert len(report.not_in_source) == 1


def test_verifying_an_empty_import_reports_every_source_row():
    source = [_source("CANVA", -15.00), _source("OBSIDIAN", -5.00, day=6)]

    report = verify_against_source([], source)

    assert not report.is_clean
    assert len(report.missing_from_import) == 2
    assert report.imported_total == 0.0
    assert report.source_total == -20.00
