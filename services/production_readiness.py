"""Evidence-backed production-data readiness checks."""

import os
import sqlite3
import stat
import subprocess
import sys
from functools import lru_cache
from dataclasses import dataclass

from config import API_KEY_FILE, DATABASE_PATH
from services.backups import backup_health


@dataclass(frozen=True)
class ReadinessCheck:
    key: str
    label: str
    passed: bool
    detail: str
    required: bool = True


@lru_cache(maxsize=1)
def _filevault_check() -> ReadinessCheck:
    if sys.platform != "darwin":
        return ReadinessCheck("filevault", "Full-disk encryption", False,
                              "FileVault can only be verified on macOS.")
    try:
        result = subprocess.run(
            ["fdesetup", "status"], capture_output=True, text=True, timeout=5
        )
        enabled = "FileVault is On" in result.stdout
        return ReadinessCheck(
            "filevault", "FileVault", enabled,
            result.stdout.strip() or "Unable to determine FileVault status.",
        )
    except Exception as exc:
        return ReadinessCheck("filevault", "FileVault", False, str(exc))


def _permissions_check() -> ReadinessCheck:
    if not DATABASE_PATH.exists():
        return ReadinessCheck("permissions", "Database permissions", False,
                              "Database has not been created yet.")
    mode = stat.S_IMODE(DATABASE_PATH.stat().st_mode)
    passed = mode & 0o077 == 0
    return ReadinessCheck(
        "permissions", "Database permissions", passed,
        f"Current mode: {mode:04o}; required: no group/other access.",
    )


def _database_integrity_check() -> ReadinessCheck:
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        try:
            passed = conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            conn.close()
        return ReadinessCheck("integrity", "Database integrity", passed,
                              "SQLite integrity check passed." if passed else "Integrity check failed.")
    except Exception as exc:
        return ReadinessCheck("integrity", "Database integrity", False, str(exc))


def get_readiness_checks() -> list[ReadinessCheck]:
    health = backup_health()
    return [
        _filevault_check(),
        _permissions_check(),
        _database_integrity_check(),
        ReadinessCheck(
            "encrypted_database", "Encrypted database", False,
            "SQLCipher encryption has not been implemented yet.",
        ),
        ReadinessCheck(
            "legacy_secret", "Secrets outside plaintext files", not API_KEY_FILE.exists(),
            "No legacy plaintext API-key file exists."
            if not API_KEY_FILE.exists()
            else "A legacy plaintext API-key file still needs Keychain migration.",
        ),
        ReadinessCheck(
            "backup", "Current verified backup", bool(health["healthy"]),
            health["reason"],
        ),
    ]


def is_production_ready() -> bool:
    return all(c.passed for c in get_readiness_checks() if c.required)
