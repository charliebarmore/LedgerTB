"""Book files: which database the app opens, ProSystem-style.

A "book" is one encrypted LedgerTB database file. The default book lives in
the user data directory; a firm can instead keep book files on a shared drive
and open whichever one they're working on — the app stays installed locally,
the data travels by path. The active choice and a recent list persist in
books.json in the user data directory (never inside a book).

LEDGERTB_DB_PATH still overrides everything (dev and tests), with the legacy
PROBOOKS_DB_PATH name accepted for compatibility.
"""
import json
import os
from pathlib import Path

from config import DATABASE_PATH as DEFAULT_BOOK
from config import USER_DATA_DIR, app_env

SETTINGS_PATH = USER_DATA_DIR / "books.json"
MAX_RECENT = 8
BOOK_EXTENSION = ".ledgertb"
# ProBooks-era ".probooks" files keep working with no special handling: book
# selection is entirely path-based, so no legacy-extension code path exists.


def _load() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text())
    except Exception:
        return {}


def _save(data: dict) -> None:
    """Write the registry privately and atomically.

    Book files are named after clients ("Smith & Co.ledgertb"), so this list
    of paths is a list of who the firm works for — it should not be readable
    by other accounts on the machine. And a crash midway through a plain write
    truncates it, losing which book was open and the recent list, so the
    replace is atomic.
    """
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(SETTINGS_PATH.parent, 0o700)
    except OSError:
        pass
    payload = json.dumps(data, indent=2) + "\n"
    temp = SETTINGS_PATH.with_name(f".{SETTINGS_PATH.name}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(temp, SETTINGS_PATH)


def active_book() -> Path:
    """The book the app should open: env override, else the saved choice,
    else the default."""
    if app_env("DB_PATH"):
        return DEFAULT_BOOK
    saved = _load().get("active")
    return Path(saved) if saved else DEFAULT_BOOK


def is_local_book(path) -> bool:
    """Whether a book is in LedgerTB's local managed-data area.

    Custom/external paths are treated conservatively as shared-drive books.
    MCP writes are disabled for those paths because the MCP process does not
    participate in the desktop app's sidecar writer lock.
    """
    try:
        resolved = Path(path).expanduser().resolve()
        default = Path(DEFAULT_BOOK).expanduser().resolve()
        managed = Path(USER_DATA_DIR).expanduser().resolve()
        return resolved == default or resolved.is_relative_to(managed)
    except (OSError, RuntimeError, ValueError):
        return False


def set_active_book(path) -> None:
    path = str(Path(path))
    data = _load()
    data["active"] = path
    recent = [p for p in data.get("recent", []) if p != path]
    recent.insert(0, path)
    data["recent"] = recent[:MAX_RECENT]
    _save(data)


def recent_books() -> list:
    """Recently opened book paths, most recent first (may include paths that
    are currently unreachable, e.g. an offline share — the UI says so)."""
    seen = []
    for p in [str(DEFAULT_BOOK)] + _load().get("recent", []):
        if p not in seen:
            seen.append(p)
    return [Path(p) for p in seen]
