"""Book-scoped credential-vault names for local assistant authorization."""

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


_PREFIX = "mcp_book"
LEGACY_SECRET_NAMES = (
    "mcp_db_key",
    "mcp_access_level",
    "mcp_export_roots",
)


@dataclass(frozen=True)
class AssistantCredentialNames:
    key: str
    level: str
    book_id: str
    export_roots: str


def book_scope(path: Path) -> str:
    """Opaque, stable-on-this-machine namespace for a canonical book path."""
    canonical = os.path.normcase(str(Path(path).expanduser().resolve()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def credential_names(path: Path) -> AssistantCredentialNames:
    scope = book_scope(path)
    return AssistantCredentialNames(
        key=f"{_PREFIX}:{scope}:db_key",
        level=f"{_PREFIX}:{scope}:access_level",
        book_id=f"{_PREFIX}:{scope}:book_id",
        export_roots=f"{_PREFIX}:{scope}:export_roots",
    )


def revoke_legacy_credentials() -> bool:
    """Remove pre-book-scoping credentials, which are unsafe to reuse."""
    from utils import secure_store

    found = False
    for name in LEGACY_SECRET_NAMES:
        if secure_store.get_secret(name):
            found = True
            secure_store.delete_secret(name)
    return found
