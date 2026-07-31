"""Passphrase-keyed SQLCipher helpers: key derivation, state detection, migration.

ProBooks encrypts its SQLite database at rest with SQLCipher. A launch passphrase
is turned into a 32-byte key ONCE via PBKDF2-HMAC-SHA512 (derive_key); every
database connection then opens with that raw key, which skips SQLCipher's own
per-connection PBKDF2 (~40 ms/open) in favour of the raw-key fast path
(~0.1 ms/open). That matters because the app opens a fresh connection per query.
The passphrase and derived key live only in the process -- never on disk or in
the OS keychain.
"""

import hashlib
import shutil
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
    tmp_enc = path.with_suffix(path.suffix + ".enc.tmp")
    if tmp_enc.exists():
        tmp_enc.unlink()

    key = key_pragma(derive_key(passphrase))
    src = sqlcipher3.connect(str(path))  # opened without a key -> read as plaintext
    try:
        src.execute(f"ATTACH DATABASE {_sql_str(str(tmp_enc))} AS enc KEY {key}")
        src.execute("SELECT sqlcipher_export('enc')")
        src.execute("DETACH DATABASE enc")
    finally:
        src.close()

    backup = path.with_suffix(path.suffix + ".plaintext.bak")
    shutil.move(str(path), str(backup))
    shutil.move(str(tmp_enc), str(path))
    return backup
