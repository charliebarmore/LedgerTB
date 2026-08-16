"""Passphrase-keyed SQLCipher helpers: key derivation, state detection, migration.

LedgerTB encrypts its SQLite database at rest with SQLCipher. A launch passphrase
is turned into a 32-byte key ONCE via PBKDF2-HMAC-SHA512 (derive_key); every
database connection then opens with that raw key, which skips SQLCipher's own
per-connection PBKDF2 (~40 ms/open) in favour of the raw-key fast path
(~0.1 ms/open). That matters because the app opens a fresh connection per query.
The passphrase and derived key live only in the process -- never on disk or in
the OS keychain.
"""

import hashlib
import os
from pathlib import Path

# Optional: when SQLCipher isn't installed the app runs unencrypted (see
# database/connection.py). Only the helpers that actually touch an encrypted
# file need the driver; they raise a clear error if it's missing.
try:
    import sqlcipher3
except ImportError:
    sqlcipher3 = None

# Fixed application salt for the passphrase KDF. It is not secret. A fixed salt
# (rather than a random per-install one) keeps the derived key a pure function of
# the passphrase, so backups, restores, and migrations need no sidecar salt file
# that, if lost, would render the data unrecoverable -- a real hazard for a CPA's
# records. Brute-forcing still costs a full 256k-iteration PBKDF2 per guess for
# anyone who obtains the encrypted file.
# Compatibility invariant: this legacy-branded value is part of every existing
# book's key derivation. Renaming it would make those encrypted books unreadable.
_KDF_SALT = b"ProBooks/SQLCipher/v1/kdf-salt"
_KDF_ITERATIONS = 256_000
_KEY_BYTES = 32


def derive_key(passphrase: str) -> str:
    """Derive the SQLCipher raw key (64-char hex) from a passphrase. Run once
    per unlock; the result is what connections are keyed with."""
    return hashlib.pbkdf2_hmac(
        "sha512", passphrase.encode("utf-8"), _KDF_SALT, _KDF_ITERATIONS, _KEY_BYTES
    ).hex()


def key_pragma(raw_key_hex: str) -> str:
    """SQL literal for ``PRAGMA key`` / ``ATTACH ... KEY`` using a raw key.

    Produces ``"x'<hex>'"`` (SQLCipher's raw-key form). The input is validated
    hex, so there is nothing to escape.
    """
    if len(raw_key_hex) != _KEY_BYTES * 2 or any(
        ch not in "0123456789abcdefABCDEF" for ch in raw_key_hex
    ):
        raise ValueError("raw key must be a 64-character hex string")
    return "\"x'" + raw_key_hex + "'\""


def _sql_str(value: str) -> str:
    """Single-quoted SQL string literal with embedded quotes doubled (for the
    ATTACH filename, which is a path we control but escape defensively)."""
    return "'" + value.replace("'", "''") + "'"


def _has_sqlite_header(path: Path) -> bool:
    with open(path, "rb") as handle:
        return handle.read(16) == b"SQLite format 3\x00"


def database_state(path: Path) -> str:
    """Classify the database file so the unlock gate picks the right flow.

    - ``"absent"``    -> missing or empty; first run, create a new encrypted DB.
    - ``"plaintext"`` -> legacy unencrypted SQLite; needs one-time migration.
    - ``"encrypted"`` -> SQLCipher database; unlock with the passphrase.
    """
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return "absent"
    return "plaintext" if _has_sqlite_header(path) else "encrypted"


def plaintext_backup_path(path: Path) -> Path:
    """The one reserved backup path used by plaintext-to-encrypted migration."""
    path = Path(path)
    return path.with_suffix(path.suffix + ".plaintext.bak")


def is_plaintext_sqlite(path: Path) -> bool:
    """True only for a regular, non-symlink SQLite file with a clear header."""
    path = Path(path)
    return path.is_file() and not path.is_symlink() and _has_sqlite_header(path)


def verify_passphrase(path: Path, passphrase: str) -> bool:
    """True if ``passphrase`` opens the encrypted database at ``path``."""
    if sqlcipher3 is None:
        raise RuntimeError("SQLCipher (sqlcipher3) is not installed; cannot open an encrypted database.")
    try:
        conn = sqlcipher3.connect(str(path))
        try:
            conn.execute(f"PRAGMA key = {key_pragma(derive_key(passphrase))}")
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        finally:
            conn.close()
        return True
    except Exception:
        return False


def _fsync_path(path: Path) -> None:
    """Force a file or directory to durable storage, where the platform can.

    A rename is only atomic once both the new file's contents and the directory
    entry naming it have actually reached the disk. Without this a power loss
    can leave the rename visible and the contents not, which is the one failure
    an atomic replace is supposed to rule out. Directory fsync is a no-op or an
    error on some platforms (Windows), so it is best effort there.
    """
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _replace_or_explain(tmp_new: Path, path: Path) -> None:
    """Swap the prepared file in, or say why Windows refused.

    Verified on Windows 11 / NTFS with Python 3.12: os.replace fails with
    WinError 5 if ANY handle is open on the target, including a plain read
    handle and a live SQLite connection. POSIX replaces the name regardless.

    It fails safely, since the replace is all-or-nothing and the book is left
    untouched, but "Access is denied" tells the reader nothing about what to do.
    """
    try:
        os.replace(tmp_new, path)
    except PermissionError as exc:
        raise RuntimeError(
            "Another program has this book open, so it could not be replaced. "
            "On Windows a file cannot be replaced while anything holds it open. "
            "Close every other LedgerTB window and any assistant access, then "
            "try again. Nothing has been changed."
        ) from exc


def _data_version(conn) -> int:
    """SQLite's counter of commits made by OTHER connections to this database.

    The one signal available here for "did somebody else write while we were
    working". It does not move for this connection's own writes, which is
    exactly what makes it usable as a race detector.
    """
    return conn.execute("PRAGMA data_version").fetchone()[0]


def rekey_file(path: Path, current_key_hex: str, new_key_hex: str) -> None:
    """Re-encrypt a standalone SQLCipher file in place, atomically.

    The same export, verify, single-replace shape as change_passphrase, without
    its concurrency checks: this is for a file nothing else is writing, such as
    a backup. The original is never replaced by anything that has not opened and
    passed an integrity check first.
    """
    if sqlcipher3 is None:
        raise RuntimeError("SQLCipher (sqlcipher3) is not installed.")
    path = Path(path)
    current_pragma = key_pragma(current_key_hex)
    new_pragma = key_pragma(new_key_hex)
    tmp_new = path.with_suffix(path.suffix + ".rekey.tmp")
    if tmp_new.exists() or tmp_new.is_symlink():
        tmp_new.unlink()
    try:
        src = sqlcipher3.connect(str(path))
        try:
            src.execute(f"PRAGMA key = {current_pragma}")
            src.execute("SELECT count(*) FROM sqlite_master").fetchone()
            page_size = src.execute("PRAGMA cipher_page_size").fetchone()[0]
            src.execute(
                f"ATTACH DATABASE {_sql_str(str(tmp_new))} AS rekeyed KEY {new_pragma}"
            )
            if page_size:
                src.execute(f"PRAGMA rekeyed.cipher_page_size = {int(page_size)}")
            src.execute("SELECT sqlcipher_export('rekeyed')")
            src.execute("DETACH DATABASE rekeyed")
        finally:
            src.close()

        check = sqlcipher3.connect(str(tmp_new))
        try:
            check.execute(f"PRAGMA key = {new_pragma}")
            ok = check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            check.close()
        if not ok:
            raise RuntimeError("The re-encrypted copy failed its integrity check.")

        _fsync_path(tmp_new)
        _replace_or_explain(tmp_new, path)
        _fsync_path(path)
        _fsync_path(path.parent)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        if tmp_new.exists():
            tmp_new.unlink()
        raise


def key_fingerprint(key_hex: str) -> str:
    """A short, non-reversible marker for which key opens a file.

    Recorded in a backup's manifest so the app can say which passphrase a
    backup needs without opening it, and without storing anything that helps
    open it. A SHA-256 of a 256-bit key is not a shortcut to the key.
    """
    return hashlib.sha256(("ledgertb-key-fingerprint:" + key_hex).encode()).hexdigest()[:16]


def change_passphrase(path: Path, current_key_hex: str, new_passphrase: str) -> str:
    """Re-encrypt an encrypted book under a new passphrase. Returns the new key.

    Exports the whole database into a new file keyed with the new passphrase,
    proves that file opens and passes an integrity check, and then replaces the
    live file with it in one atomic rename.

    One rename, not two. An earlier version moved the live file aside and then
    moved the new one in, which left a window where the book did not exist at
    its own path -- and a crash there looks to the unlock gate exactly like a
    first run, so the app would offer to create a fresh empty book while the
    real one sat beside it under a name nothing mentioned. os.replace onto the
    live path is atomic on POSIX, so the path always resolves to a whole book,
    either the old one or the new one. The caller is expected to have taken a
    verified backup under the new key first; that, not a temporary sibling
    file, is the fallback.

    ``PRAGMA rekey`` would be shorter still and rewrites the live file in
    place, which leaves nothing to fall back to at all.

    Concurrency: this reads data_version before the export and again
    immediately before the replace, and refuses if another connection committed
    in between. That is detection, not exclusion. It cannot see a commit that
    lands after the final read, and it cannot stop a process that keeps writing
    to the displaced file after the replace. Callers must not present a
    successful return as proof that nothing else was writing.
    """
    if sqlcipher3 is None:
        raise RuntimeError("SQLCipher (sqlcipher3) is not installed; cannot change the passphrase.")
    path = Path(path)
    if database_state(path) != "encrypted":
        raise RuntimeError("Only an encrypted book has a passphrase to change.")
    if not new_passphrase:
        raise ValueError("The new passphrase cannot be empty.")

    new_key = derive_key(new_passphrase)
    if new_key == current_key_hex:
        raise ValueError("The new passphrase is the same as the current one.")

    current_pragma = key_pragma(current_key_hex)
    new_pragma = key_pragma(new_key)
    tmp_new = path.with_suffix(path.suffix + ".rekey.tmp")
    if tmp_new.exists() or tmp_new.is_symlink():
        tmp_new.unlink()

    try:
        src = sqlcipher3.connect(str(path))
        try:
            src.execute(f"PRAGMA key = {current_pragma}")
            # Fails here, before anything is written, if the key we were handed
            # does not open the book.
            src.execute("SELECT count(*) FROM sqlite_master").fetchone()
            page_size = src.execute("PRAGMA cipher_page_size").fetchone()[0]
            before = _data_version(src)

            src.execute(
                f"ATTACH DATABASE {_sql_str(str(tmp_new))} AS rekeyed KEY {new_pragma}"
            )
            # Carry the source's page size across. sqlcipher_export does not,
            # and a target left on the build's default would still open here
            # while failing against a build compiled with a different one.
            if page_size:
                src.execute(f"PRAGMA rekeyed.cipher_page_size = {int(page_size)}")
            src.execute("SELECT sqlcipher_export('rekeyed')")
            src.execute("DETACH DATABASE rekeyed")

            after = _data_version(src)
        finally:
            src.close()

        if after != before:
            raise RuntimeError(
                "Another connection wrote to this book while its passphrase was "
                "being changed, so nothing was changed. Close the book "
                "everywhere else, including any assistant access, and try again."
            )

        check = sqlcipher3.connect(str(tmp_new))
        try:
            check.execute(f"PRAGMA key = {new_pragma}")
            result = check.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            check.close()
        if result != "ok":
            raise RuntimeError("The re-encrypted copy failed its integrity check.")

        # Last look before the point of no return. Narrow, not closed: a commit
        # landing after this and before the replace is still lost, which is why
        # the caller takes a backup first and why the UI does not claim more.
        final = sqlcipher3.connect(str(path))
        try:
            final.execute(f"PRAGMA key = {current_pragma}")
            if _data_version(final) != before:
                raise RuntimeError(
                    "Another connection wrote to this book while its passphrase "
                    "was being changed, so nothing was changed. Close the book "
                    "everywhere else, including any assistant access, and try "
                    "again."
                )
        finally:
            final.close()

        _fsync_path(tmp_new)
        _replace_or_explain(tmp_new, path)
        _fsync_path(path)
        _fsync_path(path.parent)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        if tmp_new.exists():
            tmp_new.unlink()
        raise

    return new_key


def encrypt_plaintext_db(path: Path, passphrase: str) -> Path:
    """Convert a plaintext SQLite database to a SQLCipher-encrypted one in place.

    Exports the plaintext database into a new encrypted file via
    ``sqlcipher_export``, then swaps it in, preserving the original alongside as
    ``<name>.plaintext.bak`` so a botched passphrase can't lose data. Returns the
    backup path.
    """
    if sqlcipher3 is None:
        raise RuntimeError("SQLCipher (sqlcipher3) is not installed; cannot encrypt the database.")
    path = Path(path)
    if not is_plaintext_sqlite(path):
        raise RuntimeError(
            "Only an ordinary plaintext SQLite book file can be migrated; "
            "symlinks and unexpected files are left untouched."
        )
    backup = plaintext_backup_path(path)
    if backup.exists() or backup.is_symlink():
        raise RuntimeError(
            f"Migration backup already exists: {backup}. Move or remove it "
            "before trying the migration again."
        )
    tmp_enc = path.with_suffix(path.suffix + ".enc.tmp")
    if tmp_enc.exists() or tmp_enc.is_symlink():
        tmp_enc.unlink()

    key = key_pragma(derive_key(passphrase))
    try:
        src = sqlcipher3.connect(str(path))  # no key: source is plaintext
        try:
            src.execute(f"ATTACH DATABASE {_sql_str(str(tmp_enc))} AS enc KEY {key}")
            src.execute("SELECT sqlcipher_export('enc')")
            src.execute("DETACH DATABASE enc")
        finally:
            src.close()

        # Never replace the only live copy until the encrypted export opens
        # with the chosen key and reads back cleanly.
        check = sqlcipher3.connect(str(tmp_enc))
        try:
            check.execute(f"PRAGMA key = {key}")
            result = check.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            check.close()
        if result != "ok":
            raise RuntimeError("The encrypted migration copy failed its integrity check.")

        path.replace(backup)
        try:
            tmp_enc.replace(path)
        except Exception:
            # Best effort rollback: put the original plaintext book back at
            # its original path if the second rename fails.
            if not path.exists() and backup.exists():
                backup.replace(path)
            raise
        try:
            os.chmod(path, 0o600)
            os.chmod(backup, 0o600)
        except OSError:
            pass
    except Exception:
        if tmp_enc.exists():
            tmp_enc.unlink()
        raise

    return backup
