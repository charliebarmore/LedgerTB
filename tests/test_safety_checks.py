"""Tests for the safety checklist (services/production_readiness.py)."""

import services.production_readiness as pr
from services.production_readiness import SafetyCheck, overall_status


def _check(key="x", passed=True, required=True):
    return SafetyCheck(key, key, passed, "detail", required=required)


def test_overall_status_three_tiers():
    protected = [_check("book_encrypted"), _check("backup", required=False)]
    assert overall_status(protected) == "protected"

    backup_only = [_check("book_encrypted"),
                   _check("backup", passed=False, required=False)]
    assert overall_status(backup_only) == "backup_needed"

    at_risk = [_check("book_encrypted", passed=False),
               _check("backup", passed=False, required=False)]
    assert overall_status(at_risk) == "at_risk"


def test_advisory_checks_never_turn_the_banner_red():
    # A failing advisory (unverifiable disk encryption, shared-drive book)
    # must not flag the book at risk — a permanent red banner teaches people
    # to ignore the page.
    checks = [_check("disk_encryption", passed=False, required=False),
              _check("file_access", passed=False, required=False),
              _check("backup", required=False)]
    assert overall_status(checks) == "protected"


def test_status_labels():
    assert _check(passed=True).status_label == "Pass"
    assert _check(passed=False, required=True).status_label == "Action needed"
    # The backup is advisory for the banner but still an action, not homework.
    assert _check("backup", passed=False, required=False).status_label == "Action needed"
    assert _check("disk_encryption", passed=False, required=False).status_label == "Check yourself"


def test_windows_disk_encryption_is_advisory_not_a_permanent_failure(monkeypatch):
    """Pre-fix, non-macOS platforms failed a REQUIRED FileVault check forever,
    so every Windows install showed 'TEST DATA ONLY' with no way to clear it."""
    pr._disk_encryption_check.cache_clear()
    monkeypatch.setattr("sys.platform", "win32")
    try:
        check = pr._disk_encryption_check()
    finally:
        pr._disk_encryption_check.cache_clear()

    assert check.required is False
    assert "BitLocker" in check.detail
    assert check.status_label == "Check yourself"


def test_windows_file_access_passes_with_plain_explanation(db, monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(pr, "is_local_book", lambda p: True)
    check = pr._file_access_check()
    assert check.passed is True
    assert "user profile" in check.detail


def test_shared_book_file_access_is_advisory(db, monkeypatch):
    """A firm-mode book on a shared drive is SUPPOSED to be reachable by
    others; POSIX-mode nagging would misread the feature as a failure."""
    monkeypatch.setattr(pr, "is_local_book", lambda p: False)
    check = pr._file_access_check()
    assert check.required is False
    assert "shared location" in check.detail


def test_checks_inspect_the_active_book_not_the_default(db, monkeypatch, tmp_path):
    """Pre-fix, the checks read config.DATABASE_PATH — after 'Switch book…'
    they inspected the wrong file."""
    from database import connection as dbconn
    assert pr._book_path() == dbconn.DATABASE_PATH


def test_plaintext_migration_copy_is_a_required_failure(db):
    from database import connection as dbconn
    from database.crypto import plaintext_backup_path

    backup = plaintext_backup_path(dbconn.DATABASE_PATH)
    backup.write_bytes(b"SQLite format 3\x00sensitive")

    check = pr._plaintext_backup_check()
    assert check.passed is False
    assert check.required is True
    assert check.status_label == "Action needed"
    assert overall_status([check]) == "at_risk"
