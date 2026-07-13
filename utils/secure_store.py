"""Small-secret storage backed by the operating system credential vault."""

from pathlib import Path
from typing import Optional

SERVICE_NAME = "com.ledgerlabs.probooks"


def get_secret(name: str) -> Optional[str]:
    try:
        import keyring
        return keyring.get_password(SERVICE_NAME, name) or None
    except Exception:
        return None


def set_secret(name: str, value: str) -> None:
    if not value:
        raise ValueError("Secret value cannot be empty.")
    import keyring
    keyring.set_password(SERVICE_NAME, name, value)
    if keyring.get_password(SERVICE_NAME, name) != value:
        raise RuntimeError("The credential vault did not verify the saved secret.")


def delete_secret(name: str) -> None:
    try:
        import keyring
        keyring.delete_password(SERVICE_NAME, name)
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
