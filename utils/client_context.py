"""Canonical client-context tracking for stateful Streamlit pages.

Client ids are only unique inside one LedgerTB book.  Any state that can
drive a write therefore belongs to ``(book, client_id)``, not to a bare
client id.  This module centralizes that identity and gives each page a
generation counter it can embed in mutation-widget keys.  A new generation
is the browser-visible reset Streamlit requires; deleting session-state values
alone does not prevent the frontend from restoring an old widget value.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, MutableMapping


_ACTIVE_IDENTITY_KEY = "_active_client_context"
_ACTIVE_GENERATION_KEY = "_active_client_context_generation"
_INTENT_PREFIX = "_client_navigation_intent_"


def _book_identity(book_path) -> str:
    """Return a stable identity for a book path, even before it exists."""
    path = Path(book_path).expanduser()
    try:
        return str(path.resolve(strict=False))
    except (OSError, RuntimeError):
        return str(path.absolute())


def client_context_identity(book_path, client_id: int) -> tuple[str, int]:
    """Build the canonical identity for client-owned UI state."""
    return (_book_identity(book_path), int(client_id))


def sync_active_client_context(
    session_state: MutableMapping,
    client_id: int,
    book_path,
) -> bool:
    """Record the app-wide client context and report whether it changed."""
    identity = client_context_identity(book_path, client_id)
    previous = session_state.get(_ACTIVE_IDENTITY_KEY)
    changed = previous is not None and previous != identity
    if changed:
        session_state[_ACTIVE_GENERATION_KEY] = (
            session_state.get(_ACTIVE_GENERATION_KEY, 0) + 1
        )
    else:
        session_state.setdefault(_ACTIVE_GENERATION_KEY, 0)
    session_state[_ACTIVE_IDENTITY_KEY] = identity
    return changed


@dataclass(frozen=True)
class ClientPageScope:
    """The current ownership and widget generation for one page."""

    page: str
    identity: tuple[str, int]
    generation: int
    changed: bool

    def key(self, base: str) -> str:
        """Make a mutation-widget key unique to this page generation."""
        return f"{base}__{self.page}_g{self.generation}"


def scope_page_to_client(
    session_state: MutableMapping,
    page: str,
    client_id: int,
    book_path,
) -> ClientPageScope:
    """Bind a page's state to the selected book and client.

    Page ownership is tracked separately from the app-wide marker.  This is
    important when a user switches clients on another page: the destination
    page must still notice that its own saved state belongs to the old client.
    """
    identity = client_context_identity(book_path, client_id)
    sync_active_client_context(session_state, client_id, book_path)

    owner_key = f"_client_context_owner_{page}"
    generation_key = f"_client_context_generation_{page}"
    previous = session_state.get(owner_key)
    changed = previous is not None and previous != identity
    if changed:
        session_state[generation_key] = session_state.get(generation_key, 0) + 1
    else:
        session_state.setdefault(generation_key, 0)
    session_state[owner_key] = identity

    return ClientPageScope(
        page=page,
        identity=identity,
        generation=session_state[generation_key],
        changed=changed,
    )


def set_client_intent(
    session_state: MutableMapping,
    name: str,
    value: Any,
    client_id: int,
    book_path,
) -> None:
    """Store a one-shot cross-page value owned by the current client context.

    Plain Streamlit session keys survive page changes, but a bare account or
    entry id is unsafe because ids restart in each book.  Intents carry their
    owner so a destination can distinguish a legitimate drill-down after a
    client switch from stale navigation left by another book or client.
    """
    session_state[f"{_INTENT_PREFIX}{name}"] = (
        client_context_identity(book_path, client_id),
        value,
    )


def pop_client_intent(
    session_state: MutableMapping,
    name: str,
    client_id: int,
    book_path,
    default=None,
):
    """Consume an intent only when it belongs to the selected book/client."""
    payload = session_state.pop(f"{_INTENT_PREFIX}{name}", None)
    if not isinstance(payload, tuple) or len(payload) != 2:
        return default
    owner, value = payload
    if owner != client_context_identity(book_path, client_id):
        return default
    return value
