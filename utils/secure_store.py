"""Small-secret storage backed by the operating system credential vault."""

from pathlib import Path
from typing import Optional

SERVICE_NAME = "com.ledgerlabs.ledgertb"
LEGACY_SERVICE_NAME = "com.ledgerlabs.probooks"


def _read(keyring, service: str, name: str) -> Optional[str]:
    return keyring.get_password(service, name) or None


def get_secret(name: str) -> Optional[str]:
    try:
        import keyring
        current = _read(keyring, SERVICE_NAME, name)
        if current:
            return current
        legacy = _read(keyring, LEGACY_SERVICE_NAME, name)
        if not legacy:
            return None
        # Copy and retain on READ so an older installed build stays usable
        # during the transition. Any WRITE (set_secret/delete_secret) is
        # authoritative and clears the legacy name — a read-only fallback must
        # never resurrect a value the user has since changed or revoked.
        try:
            keyring.set_password(SERVICE_NAME, name, legacy)
            if _read(keyring, SERVICE_NAME, name) != legacy:
                raise RuntimeError("The credential vault did not verify migration.")
        except Exception:
            pass
        return legacy
    except Exception:
        return None


def set_secret(name: str, value: str) -> None:
    if not value:
        raise ValueError("Secret value cannot be empty.")
    import keyring
    keyring.set_password(SERVICE_NAME, name, value)
    if keyring.get_password(SERVICE_NAME, name) != value:
        raise RuntimeError("The credential vault did not verify the saved secret.")
    # A write is authoritative for BOTH service names. Leaving a stale copy
    # under the old ProBooks service would let an old installed build keep
    # enforcing a superseded value — e.g. an assistant access level the user
    # has since turned down. The verified new-name write above stands even if
    # the vault refuses the legacy delete.
    try:
        keyring.delete_password(LEGACY_SERVICE_NAME, name)
    except Exception:
        pass


def delete_secret(name: str) -> None:
    try:
        import keyring
    except Exception:
        return
    for service in (SERVICE_NAME, LEGACY_SERVICE_NAME):
        try:
            keyring.delete_password(service, name)
        except Exception:
            pass


def migrate_legacy_secret(name: str, legacy_path: Path) -> Optional[str]:
    """Move a legacy plaintext secret into Keychain after verifying the write."""
    current = get_secret(name)
    if current:
        return current
    if not legacy_path.exists():
        return None
    try:
        value = legacy_path.read_text().strip()
        if not value:
            return None
        set_secret(name, value)
        legacy_path.unlink()
        return value
    except Exception:
        # Preserve the legacy file if migration is unavailable or fails.
        return None
