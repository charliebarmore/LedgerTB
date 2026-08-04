"""Passphrase gate for the encrypted database, and the book-file chooser.

ProBooks encrypts its SQLite database with SQLCipher. The key is derived from a
passphrase entered at launch and held only in this process -- never written to
disk or the OS keychain (see database/connection). Every page calls
require_unlock() right after st.set_page_config; until the passphrase is set the
page renders only the gate and stops.

The gate is satisfied by the *process* holding an active key, not by session
state, so one unlock covers the whole launch (and the pytest ``db`` fixture,
which sets a key directly, transparently passes the gate).

Firm mode: the gate is also where a different BOOK FILE is chosen (a firm can
keep book files on a shared drive, ProSystem-style). Opening a book takes an
in-use lock beside the file; if someone else holds it, the opener chooses
read-only or takeover. See utils/books.py and utils/book_lock.py.
"""

import atexit

import streamlit as st

from config import APP_NAME
from database import connection as dbconn
from database.crypto import (
    database_state,
    derive_key,
    encrypt_plaintext_db,
    verify_passphrase,
)
from utils import book_lock, books, secure_store

MIN_PASSPHRASE_LEN = 8


def saved_key_name(book) -> str:
    """Vault entry name for a remembered book key (per book path)."""
    import hashlib

    digest = hashlib.sha256(str(book).encode()).hexdigest()[:16]
    return f"book_key_{digest}"


def forget_saved_key(book) -> None:
    secure_store.delete_secret(saved_key_name(book))


def try_saved_key() -> bool:
    """Unlock from a remembered key ("Remember on this computer"). A key that no
    longer opens the book (changed passphrase) is dropped from the vault."""
    book = dbconn.DATABASE_PATH
    if database_state(book) != "encrypted":
        return False
    key = secure_store.get_secret(saved_key_name(book))
    if not key:
        return False
    dbconn.set_active_key(key)
    try:
        conn = dbconn.get_connection()
        try:
            conn.execute("SELECT count(*) FROM sqlite_master")
        finally:
            conn.close()
    except Exception:
        dbconn.clear_active_key()
        forget_saved_key(book)
        return False
    return True

# Best-effort lock release on clean shutdown. release() only removes a lock
# this process wrote, so this can never clobber another machine's session; a
# hard kill leaves a stale lock that the next same-user open reclaims.
atexit.register(lambda: book_lock.release(dbconn.DATABASE_PATH))


def require_unlock():
    """Ensure the database is unlocked, or render the gate and stop the page.

    When the SQLCipher driver isn't installed (fallback mode, see
    database/connection.py) there is no passphrase: an existing encrypted
    database is refused outright, and otherwise the page runs unencrypted
    behind a persistent warning.
    """
    if not dbconn.ENCRYPTION_AVAILABLE:
        if database_state(dbconn.DATABASE_PATH) == "encrypted":
            st.markdown(f"## 🔒 {APP_NAME}")
            st.error(
                "This database is encrypted, but the SQLCipher driver "
                "(`sqlcipher3`) is not installed on this machine, so it cannot "
                "be unlocked. Install SQLCipher and relaunch."
            )
            st.stop()
        st.warning(
            "Encryption is off: the SQLCipher driver is not installed, so this "
            "database is stored unencrypted. Fine for evaluating with sample "
            "data; install `sqlcipher3` before keeping real books here.",
            icon="🔓",
        )
        return
    if dbconn.has_active_key():
        if dbconn.READ_ONLY:
            holder = book_lock.read_lock(dbconn.DATABASE_PATH)
            who = f" — in use by {book_lock.describe(holder)}" if holder else ""
            st.warning(f"Read-only: this book is open for viewing only{who}.",
                       icon="🔍")
        return
    # Locked: point at the chosen book before deciding which form to show.
    dbconn.DATABASE_PATH = books.active_book()
    # A remembered key skips the prompt — but never skips the in-use lock.
    if not st.session_state.get("_book_lock_holder") and try_saved_key():
        result = book_lock.acquire(dbconn.DATABASE_PATH)
        if result["acquired"]:
            books.set_active_book(dbconn.DATABASE_PATH)
            return
        st.session_state["_pending_book_key"] = dbconn.get_active_key()
        st.session_state["_book_lock_holder"] = result["holder"]
        dbconn.clear_active_key()
    _render_gate(database_state(dbconn.DATABASE_PATH))
    st.stop()


def _valid_new_passphrase(passphrase: str, confirm: str) -> bool:
    if len(passphrase) < MIN_PASSPHRASE_LEN:
        st.error(f"Use at least {MIN_PASSPHRASE_LEN} characters.")
        return False
    if passphrase != confirm:
        st.error("Passphrases do not match.")
        return False
    return True


def _finish_unlock(raw_key_hex: str, remember: bool = False):
    """Take the book's in-use lock and activate the key — or, when someone
    else holds the lock, park the key and let the user choose."""
    if remember:
        try:
            secure_store.set_secret(saved_key_name(dbconn.DATABASE_PATH), raw_key_hex)
        except Exception:
            pass  # remembering is a convenience; never block the unlock on it
    result = book_lock.acquire(dbconn.DATABASE_PATH)
    if result["acquired"]:
        dbconn.set_active_key(raw_key_hex)
        books.set_active_book(dbconn.DATABASE_PATH)
        st.rerun()
    else:
        st.session_state["_pending_book_key"] = raw_key_hex
        st.session_state["_book_lock_holder"] = result["holder"]
        st.rerun()


def _render_lock_choice():
    holder = st.session_state["_book_lock_holder"]
    st.warning(f"This book is in use by **{book_lock.describe(holder)}**.")
    st.caption(
        "Open read-only to look without touching anything. Take over only if "
        "you are sure no one is actually working in it (for example, after a "
        "crash left the lock behind) — two writers can corrupt a shared book."
    )
    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        if st.button("Open read-only", type="primary"):
            dbconn.READ_ONLY = True
            dbconn.set_active_key(st.session_state.pop("_pending_book_key"))
            st.session_state.pop("_book_lock_holder", None)
            books.set_active_book(dbconn.DATABASE_PATH)
            st.rerun()
    with c2:
        if st.button("Take over the book"):
            book_lock.takeover(dbconn.DATABASE_PATH)
            dbconn.set_active_key(st.session_state.pop("_pending_book_key"))
            st.session_state.pop("_book_lock_holder", None)
            books.set_active_book(dbconn.DATABASE_PATH)
            st.rerun()
    with c3:
        if st.button("Cancel"):
            st.session_state.pop("_pending_book_key", None)
            st.session_state.pop("_book_lock_holder", None)
            st.rerun()


def _render_book_chooser():
    """Open a different book file (shared-drive workflow)."""
    with st.expander("Book file", expanded=False):
        st.caption(f"Current: `{dbconn.DATABASE_PATH}`")
        recents = [p for p in books.recent_books()
                   if str(p) != str(dbconn.DATABASE_PATH)]
        if recents:
            pick = st.selectbox(
                "Recent books",
                options=[str(p) for p in recents],
                index=None,
                placeholder="Choose a recent book",
                key="book_recent_pick",
            )
            if pick and st.button("Open selected", key="book_open_recent"):
                books.set_active_book(pick)
                st.rerun()
        other = st.text_input(
            "Open or create another book by path",
            placeholder=r"e.g. /Volumes/Shared/Books/SmithCo.probooks",
            key="book_other_path",
        )
        if other and st.button("Open this path", key="book_open_other"):
            books.set_active_book(other.strip())
            st.rerun()
        st.caption(
            "A book is one encrypted ProBooks database. Book files can live "
            "on a shared drive, and an in-use lock keeps two people from "
            "writing to one book at once. Each book has a passphrase — using "
            "the same one for all your books is fine (one office passphrase), "
            "and \"Remember on this computer\" skips the prompt entirely."
        )


def _render_gate(state: str):
    # Keep the lock screen clean: no client nav, just the passphrase prompt.
    st.markdown(f"## 🔒 {APP_NAME}")
    if st.session_state.get("_book_lock_holder"):
        _render_lock_choice()
        return
    if state == "encrypted":
        _unlock_form()
    elif state == "plaintext":
        _migrate_form()
    else:  # "absent"
        _setup_form()
    _render_book_chooser()


def _unlock_form():
    st.caption("Enter your passphrase to unlock the database.")
    with st.form("db_unlock"):
        passphrase = st.text_input("Passphrase", type="password")
        remember = st.checkbox(
            "Remember on this computer",
            help="Stores this book's unlock key in your system credential "
                 "vault, so opening the app skips the passphrase. Anyone who "
                 "can sign in to your computer account can then open the book. "
                 "Undo any time on Data Safety.",
        )
        if st.form_submit_button("Unlock", type="primary"):
            if verify_passphrase(dbconn.DATABASE_PATH, passphrase):
                _finish_unlock(derive_key(passphrase), remember=remember)
            else:
                st.error("Incorrect passphrase.")


def _setup_form():
    st.caption(
        "First run — create a passphrase to encrypt this database. It is not "
        "stored anywhere, so if you lose it the data cannot be recovered. A "
        "longer passphrase (a memorable sentence) is stronger than a short one."
    )
    with st.form("db_setup"):
        passphrase = st.text_input("New passphrase", type="password")
        confirm = st.text_input("Confirm passphrase", type="password")
        remember = st.checkbox(
            "Remember on this computer",
            help="Stores this book's unlock key in your system credential "
                 "vault, so opening the app skips the passphrase. Undo any "
                 "time on Data Safety.",
        )
        if st.form_submit_button("Create encrypted database", type="primary"):
            if _valid_new_passphrase(passphrase, confirm):
                # First keyed connection (init_database, next run) creates the
                # database already encrypted under this passphrase.
                _finish_unlock(derive_key(passphrase), remember=remember)


def _migrate_form():
    st.warning(
        "An existing **unencrypted** database was found. Set a passphrase to "
        "encrypt it. A one-time copy of the unencrypted file is kept next to it "
        "(delete that copy once you've confirmed the encrypted database works)."
    )
    with st.form("db_migrate"):
        passphrase = st.text_input("New passphrase", type="password")
        confirm = st.text_input("Confirm passphrase", type="password")
        if st.form_submit_button("Encrypt existing database", type="primary"):
            if _valid_new_passphrase(passphrase, confirm):
                backup = encrypt_plaintext_db(dbconn.DATABASE_PATH, passphrase)
                st.session_state["_migration_backup_name"] = backup.name
                _finish_unlock(derive_key(passphrase))
