"""Evidence-backed safety checks for the open book.

Every label and detail here is read by an accountant, not a developer: say
what is true, what will happen, and what to do — never which module failed.

Three-tier verdict (`overall_status`):
- "at_risk"       — a required protection is missing; fix before client work.
- "backup_needed" — protections hold, but there is no current verified backup.
- "protected"     — everything passes.

Checks that can't be verified on this platform (BitLocker without admin
rights, NTFS permissions) are advisory: shown with guidance, never able to
turn the banner red. A permanent unfixable warning teaches people to ignore
the page.
"""

import stat
import subprocess
import sys
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path

from config import API_KEY_FILE
from database import connection as dbconn
from services.backups import backup_health
from utils.books import is_local_book


@dataclass(frozen=True)
class SafetyCheck:
    key: str
    label: str
    passed: bool
    detail: str
    required: bool = True  # advisory checks (False) never turn the banner red

    @property
    def status_label(self) -> str:
        if self.passed:
            return "Pass"
        # The backup check is advisory for the banner (yellow, not red) but
        # still something to do, not something to go verify by hand.
        if self.required or self.key == "backup":
            return "Action needed"
        return "Check yourself"


def _book_path() -> Path:
    # The active book — dbconn.DATABASE_PATH is reassigned when books switch;
    # config.DATABASE_PATH is only the default and would inspect the wrong file.
    return Path(dbconn.DATABASE_PATH)


@lru_cache(maxsize=1)
def _disk_encryption_check() -> SafetyCheck:
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["fdesetup", "status"], capture_output=True, text=True, timeout=5
            )
            enabled = "FileVault is On" in result.stdout
        except Exception:
            enabled = False
        if enabled:
            detail = ("FileVault is on — if this computer is lost or stolen, "
                      "everything on the disk stays unreadable.")
        else:
            detail = ("FileVault is off. Turn it on in System Settings → "
                      "Privacy & Security → FileVault. The book file is "
                      "encrypted either way, but reports and exports you save "
                      "from it are not.")
        return SafetyCheck("disk_encryption", "This computer's disk is encrypted",
                           enabled, detail)
    if sys.platform.startswith("win"):
        return SafetyCheck(
            "disk_encryption", "This computer's disk is encrypted", False,
            "ProBooks can't check BitLocker without administrator rights. "
            "Check it yourself: Settings → Privacy & security → Device "
            "encryption (or search Windows for \"BitLocker\"). The book file "
            "is encrypted either way, but reports and exports you save from "
            "it are not.",
            required=False,
        )
    return SafetyCheck(
        "disk_encryption", "This computer's disk is encrypted", False,
        "ProBooks can't check full-disk encryption on this system. If the "
        "computer holds client work, confirm disk encryption is on.",
        required=False,
    )


def _file_access_check() -> SafetyCheck:
    path = _book_path()
    label = "Who can open the book file"
    if not path.exists():
        return SafetyCheck("file_access", label, False,
                           "The book file hasn't been created yet.")
    if not is_local_book(path):
        return SafetyCheck(
            "file_access", label, False,
            "This book is on a shared location, so access is controlled by "
            "that folder's own permissions. Make sure only the right people "
            "can open that folder.",
            required=False,
        )
    if sys.platform.startswith("win"):
        return SafetyCheck(
            "file_access", label, True,
            "The book lives in your user profile folder, which Windows keeps "
            "private to your account.",
        )
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077 == 0:
        detail = "Only your user account can open the book file."
    else:
        detail = ("Other accounts on this computer could open the book file. "
                  "Reopen the book in ProBooks to tighten this, and keep the "
                  "file in your own user folder — not a shared or public one.")
    return SafetyCheck("file_access", label, mode & 0o077 == 0, detail)


def _file_intact_check() -> SafetyCheck:
    label = "The book file is intact"
    try:
        conn = dbconn.open_keyed(_book_path())
        try:
            passed = conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            conn.close()
    except Exception:
        passed = False
    detail = ("The whole file reads back cleanly — no corruption."
              if passed else
              "The file did not read back cleanly. Stop working in this book "
              "and restore from a verified backup.")
    return SafetyCheck("file_intact", label, passed, detail)


def _book_encrypted_check() -> SafetyCheck:
    from database.crypto import database_state

    label = "The book itself is encrypted"
    state = database_state(_book_path())
    if state == "encrypted":
        return SafetyCheck("book_encrypted", label, True,
                           "Without the passphrase, the book file is unreadable "
                           "— on this computer or anywhere it's copied.")
    if state == "absent":
        return SafetyCheck("book_encrypted", label, False,
                           "The book will be encrypted when it's first created.")
    return SafetyCheck("book_encrypted", label, False,
                       "This book is NOT encrypted — anyone who gets the file "
                       "can read it. Go to Firm Settings to encrypt it before "
                       "putting client work in it.")


def _plaintext_backup_check() -> SafetyCheck:
    from database.crypto import plaintext_backup_path

    label = "Unencrypted migration copy removed"
    backup = plaintext_backup_path(_book_path())
    if not backup.exists() and not backup.is_symlink():
        return SafetyCheck(
            "plaintext_backup", label, True,
            "No readable migration copy is sitting beside the encrypted book.",
        )
    return SafetyCheck(
        "plaintext_backup", label, False,
        "A readable migration copy may still be beside this book. Remove it "
        "with the guarded control below after confirming the encrypted book "
        "is working.",
    )


def _api_key_check() -> SafetyCheck:
    label = "API key kept out of plain files"
    if not API_KEY_FILE.exists():
        return SafetyCheck("api_key_file", label, True,
                           "No API key is sitting in a readable file. (Keys are "
                           "kept in the system credential vault.)")
    return SafetyCheck("api_key_file", label, False,
                       "An old version of ProBooks left your API key in a "
                       "plain file. ProBooks moves it into the system "
                       "credential vault automatically — if this message "
                       "doesn't clear after using AI categorization once, the "
                       "vault on this computer is refusing to store it.")


def get_safety_checks() -> list[SafetyCheck]:
    health = backup_health()
    return [
        _disk_encryption_check(),
        _file_access_check(),
        _file_intact_check(),
        _book_encrypted_check(),
        _plaintext_backup_check(),
        _api_key_check(),
        SafetyCheck("backup", "Recent verified backup", bool(health["healthy"]),
                    health["reason"] if not health["healthy"] else
                    "The latest backup was verified readable after it was written.",
                    required=False),
    ]


def overall_status(checks: list[SafetyCheck] | None = None) -> str:
    """'at_risk' | 'backup_needed' | 'protected' — see the module docstring."""
    checks = get_safety_checks() if checks is None else checks
    if any(c.required and not c.passed for c in checks):
        return "at_risk"
    if any(c.key == "backup" and not c.passed for c in checks):
        return "backup_needed"
    return "protected"
