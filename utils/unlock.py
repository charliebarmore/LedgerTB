"""Passphrase gate for the encrypted database, and the book-file chooser.

LedgerTB encrypts its SQLite database with SQLCipher. The key is derived from a
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
import ipaddress
import os
from pathlib import Path

import streamlit as st

from config import APP_NAME
from database import connection as dbconn
from database.crypto import (
    change_passphrase,
    database_state,
    derive_key,
    encrypt_plaintext_db,
    plaintext_backup_path,
    verify_passphrase,
)
from utils import book_lock, books, maintenance_lock, secure_store

MIN_PASSPHRASE_LEN = 12

# Every LedgerTB-managed backup follows the book onto the new passphrase.
# A partial re-key was considered and rejected by the maintainer: it leaves
# passphrase tiers the recovery UI cannot open, and a labelled mixture is worse
# to live with than one slower rotation. Backups are preserved, never deleted.
REKEY_ALL_BACKUPS = None

# A launch token binds this browser session to the window the app opened.
# Without it, the unlock is process-wide: anything else running on the machine
# — another signed-in user on a terminal server, or a page served by some other
# tool on another localhost port, which Streamlit's origin check permits — can
# open a second session and read the decrypted books without the passphrase.
# The desktop launchers generate a token, pass it in the environment, and open
# the window with it in the URL. Running from source there is no launcher to
# mint one, so the gate stays off and the source path is documented as
# single-user.
UI_TOKEN_ENV = "LEDGERTB_UI_TOKEN"
_UI_TOKEN_PARAM = "t"
_UI_SESSION_FLAG = "_ui_session_authorized"


def _require_local_session():
    """Refuse sessions that did not come from the window this app opened, and
    refuse to serve at all if the server was bound off-loopback."""
    try:
        address = st.get_option("server.address")
    except Exception:
        address = None
    if address and not _is_loopback(address):
        st.markdown(f"## 🔒 {APP_NAME}")
        st.error(
            f"{APP_NAME} is listening on {address}, which makes your books "
            "reachable from other computers on this network. It only ever "
            "runs on this computer. Restart it without a custom server "
            "address."
        )
        st.stop()

    expected = os.environ.get(UI_TOKEN_ENV)
    if not expected or st.session_state.get(_UI_SESSION_FLAG):
        return
    if st.query_params.get(_UI_TOKEN_PARAM) == expected:
        st.session_state[_UI_SESSION_FLAG] = True
        return
    st.markdown(f"## 🔒 {APP_NAME}")
    st.error(
        "This page was not opened by " + APP_NAME + ". Close it and use the "
        f"{APP_NAME} window instead. (Only the window the app opens can reach "
        "your books; this protects them from other programs on this computer.)"
    )
    st.stop()


def _is_loopback(address: str) -> bool:
    if address in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def saved_key_name(book) -> str:
    """Vault entry name for a remembered book key (per book path)."""
    import hashlib

    digest = hashlib.sha256(str(book).encode()).hexdigest()[:16]
    return f"book_key_{digest}"


def forget_saved_key(book) -> None:
    secure_store.delete_secret(saved_key_name(book))


class RotationResult:
    """What a passphrase change actually achieved, including what it could not.

    Deliberately not a bare success flag. Once the new key is live the operation
    has succeeded whatever else fails afterwards, and the caller still needs to
    know which follow-ups did not happen and what this machine could and could
    not verify.
    """

    def __init__(self, new_key, backup_path=None):
        self.new_key = new_key
        self.backup_path = backup_path
        self.warnings = []
        self.new_key_opens = False
        self.backups_converted = 0
        self.old_key_refused = False
        self.integrity_ok = False

    @property
    def verified(self) -> bool:
        """Every check this machine can actually run came back clean.

        Says nothing about other machines, old backups, or credentials held by
        other processes. See change_book_passphrase.
        """
        return self.new_key_opens and self.old_key_refused and self.integrity_ok


def assistant_access_enabled(book) -> bool:
    """Whether an assistant (MCP) is authorized for this book right now."""
    from utils.assistant_access import credential_names

    return bool(secure_store.get_secret(credential_names(Path(book)).key))


def change_book_passphrase(new_passphrase: str) -> RotationResult:
    """Re-encrypt the open book under a new passphrase.

    Order matters here, so it is spelled out:

    1. Refuse outright unless this session can reasonably claim the book: not
       read-only, and no assistant authorized. An assistant's key lives in a
       separate process that this one cannot stop, and rewriting its vault entry
       would not invalidate a key already loaded in it, so the requirement is
       that it be switched off first rather than fixed up afterwards.
    2. Take a verified backup keyed with the NEW passphrase, before the live
       book is touched. If anything later fails, that is a recovery point which
       opens with the passphrase the user just chose. A backup under the old one
       would be no help to the person this feature exists for, who may never
       have known it.
    3. Re-encrypt and swap (database.crypto.change_passphrase).
    4. Only then publish the new key to this process, so no query can open the
       new key against the old file or the reverse.
    5. Everything after that point is a follow-up, not a precondition. A failure
       there is a warning naming what to do, never a report that the passphrase
       did not change: it did, and telling someone otherwise is how they end up
       recording the wrong one.

    The current passphrase is not requested. The session may have been opened
    from a remembered key by someone who never knew it, which is the main
    situation this rescues.

    What a clean return does NOT prove: that no other machine was writing, that
    old backups cannot still be opened with the previous passphrase, or that no
    other process still holds the old key. Callers must not present it as such.
    """
    book = dbconn.DATABASE_PATH
    current_key = dbconn.get_active_key()
    if not current_key:
        raise RuntimeError("The book must be unlocked before its passphrase can be changed.")
    if dbconn.READ_ONLY:
        raise RuntimeError(
            "This book is open read-only, so its passphrase cannot be changed "
            "from here. The session holding it open for writing is the one that "
            "can change it."
        )
    if not books.is_local_book(book):
        raise RuntimeError(
            "This book is not in LedgerTB's own data folder, so it may be on a "
            "shared drive where no other computer's activity can be seen from "
            "here. Its passphrase cannot be changed in place. Copy it somewhere "
            "local, change the passphrase there, and put it back with everyone "
            "else closed out."
        )
    if assistant_access_enabled(book):
        raise RuntimeError(
            "Turn assistant access off for this book first, under Assistant "
            "access below. It runs as a separate program holding its own copy "
            "of the key, which this app cannot close or update from here. "
            "Re-enable it afterwards and it will pick up the new passphrase."
        )
    if len(new_passphrase) < MIN_PASSPHRASE_LEN:
        raise ValueError(
            f"The new passphrase must be at least {MIN_PASSPHRASE_LEN} characters."
        )
    new_key = derive_key(new_passphrase)
    if new_key == current_key:
        raise ValueError("The new passphrase is the same as the current one.")

    from services.backups import create_backup

    # Everything from here to the end of the swap is exclusive. data_version
    # detects a concurrent write after the fact, which is too late; this stops
    # one starting, including from the separate MCP process.
    # try/finally rather than an explicit __exit__ on each path: a
    # KeyboardInterrupt or SystemExit would skip an except clause and leave the
    # book locked for maintenance with nothing running.
    with maintenance_lock.hold(book):
        try:
            backup_path = create_backup(
                reason="pre_passphrase_change", apply_retention=False,
                target_key=new_key,
            ).database_path
        except Exception as exc:
            raise RuntimeError(
                f"No backup could be taken, so the passphrase was not changed: {exc}"
            ) from exc

        # Point of no return is inside this call, at its single atomic replace.
        # Before it, the attempt owns that backup: the live book is still on the
        # old key, so a new-key recovery point left in the managed set is the
        # mixed tier this design exists to avoid.
        try:
            change_passphrase(book, current_key, new_passphrase)
        except Exception as exc:
            from services.backups import quarantine_backup

            try:
                moved = quarantine_backup(backup_path)
            except Exception as cleanup_exc:
                raise RuntimeError(
                    f"{exc} A backup for the attempt was left in place and "
                    f"could not be moved aside ({cleanup_exc}): "
                    f"{backup_path.name}. It opens with the passphrase you "
                    "just tried, not the one the book still uses."
                ) from exc
            raise RuntimeError(
                f"{exc} The backup taken for this attempt was moved to "
                f"{moved.parent.name}/, so your recovery points still all open "
                "with the passphrase the book already had."
            ) from exc

        # From here nothing may raise. The new key is live, and telling someone
        # the passphrase did not change is how they record the wrong one.
        result = RotationResult(new_key, backup_path)
        try:
            dbconn.set_active_key(new_key)
        except Exception as exc:
            result.warnings.append(
                "The passphrase was changed, but this session could not switch "
                f"to it ({exc}). Close and reopen the book using the new "
                "passphrase."
            )

    try:
        result.new_key_opens = verify_passphrase(book, new_passphrase)
        result.old_key_refused = not _key_opens(book, current_key)
        result.integrity_ok = _integrity_with_key(book, new_key)
    except Exception as exc:
        result.warnings.append(
            f"The passphrase was changed, but the checks afterwards could not "
            f"be completed ({exc}). Take a backup now and verify it opens."
        )
    if not result.verified:
        result.warnings.append(
            "The book was re-encrypted, but this machine could not confirm "
            "every check on it afterwards. Take a backup now and verify it "
            "opens before doing further work in this book."
        )

    # Every managed backup follows the book onto the new passphrase, so the
    # recovery set never splits across two of them. See rekey_backups.
    try:
        from services.backups import rekey_backups

        converted, failed = rekey_backups(current_key, new_key, REKEY_ALL_BACKUPS)
        result.backups_converted = converted
        for name, reason in failed:
            result.warnings.append(
                f"The backup {name} could not be converted to the new "
                f"passphrase ({reason}). It was left as it was; check which "
                "passphrase opens it before relying on it."
            )
    except Exception as exc:
        result.warnings.append(
            "The passphrase was changed, but existing backups could not be "
            f"converted to it ({exc}). They were left as they were; check "
            "which passphrase opens them before relying on them."
        )

    # A machine that remembered the old key holds one that no longer opens the
    # book. Replacing it keeps the next launch silent; leaving it would make the
    # app quietly drop the entry and start demanding a passphrase instead.
    try:
        if secure_store.get_secret(saved_key_name(book)):
            secure_store.set_secret(saved_key_name(book), new_key)
    except Exception as exc:
        result.warnings.append(
            "The passphrase was changed, but this computer's remembered key "
            f"could not be updated ({exc}). You will be asked for the new "
            "passphrase next time you open this book."
        )
    return result


def _key_opens(book, key_hex) -> bool:
    import sqlcipher3

    from database.crypto import key_pragma

    try:
        conn = sqlcipher3.connect(str(book))
        try:
            conn.execute(f"PRAGMA key = {key_pragma(key_hex)}")
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        finally:
            conn.close()
        return True
    except Exception:
        return False


def _integrity_with_key(book, key_hex) -> bool:
    import sqlcipher3

    from database.crypto import key_pragma

    try:
        conn = sqlcipher3.connect(str(book))
        try:
            conn.execute(f"PRAGMA key = {key_pragma(key_hex)}")
            return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            conn.close()
    except Exception:
        return False


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
    # Before anything else: this session must belong to the app's own window,
    # and the server must not be reachable from the network.
    _require_local_session()
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
        migration_copy = plaintext_backup_path(dbconn.DATABASE_PATH)
        if migration_copy.exists() or migration_copy.is_symlink():
            st.warning(
                "An unencrypted migration copy is still stored beside this "
                "book. Remove it from Data Safety after confirming the "
                "encrypted book works.",
                icon="⚠️",
            )
        if dbconn.READ_ONLY:
            holder = book_lock.read_lock(dbconn.DATABASE_PATH)
            who = f" — in use by {book_lock.describe(holder)}" if holder else ""
            st.warning(f"Read-only: this book is open for viewing only{who}.",
                       icon="🔍")
        return
    # Locked: point at the chosen book before deciding which form to show.
    dbconn.DATABASE_PATH = books.active_book()
    # A remembered key skips the prompt — but never skips the in-use lock,
    # and never while the user asked to switch books ("_switch_book"), or
    # auto-unlock would reopen the same book before the chooser can render.
    if (not st.session_state.get("_book_lock_holder")
            and not st.session_state.get("_switch_book")
            and try_saved_key()):
        result = book_lock.acquire(dbconn.DATABASE_PATH)
        if result["acquired"]:
            books.set_active_book(dbconn.DATABASE_PATH)
            return
        st.session_state["_pending_book_key"] = dbconn.get_active_key()
        st.session_state["_book_lock_holder"] = result["holder"]
        dbconn.clear_active_key()
    _render_gate(database_state(dbconn.DATABASE_PATH))
    st.stop()


def passphrase_strength(passphrase: str) -> tuple[str, str]:
    """A blunt, honest read on how long this passphrase would survive.

    The encryption is only ever as strong as what the user types: everything
    else about the cipher is fixed, so this is the one security decision the
    accountant actually makes. Judged on length and variety rather than a
    scoring library — the goal is to push people toward a several-word
    passphrase, not to grade them precisely.
    """
    length = len(passphrase)
    words = len([w for w in passphrase.split() if w])
    classes = sum((
        any(c.islower() for c in passphrase),
        any(c.isupper() for c in passphrase),
        any(c.isdigit() for c in passphrase),
        any(not c.isalnum() for c in passphrase),
    ))
    if length >= 24 or words >= 4:
        return "strong", "Strong — a determined attacker will not get through this."
    if length >= 16 or (words >= 3 and length >= 14):
        return "good", "Good. Adding another word would make it far stronger."
    return "weak", (
        "Weak. A stolen book file with a passphrase this short can be cracked. "
        "Four random words are far stronger than a short complicated password."
    )


def _render_strength(passphrase: str) -> None:
    if not passphrase:
        return
    level, message = passphrase_strength(passphrase)
    if level == "strong":
        st.success(message, icon="🔒")
    elif level == "good":
        st.info(message, icon="🔑")
    else:
        st.warning(message, icon="⚠️")


def _valid_new_passphrase(passphrase: str, confirm: str) -> bool:
    if len(passphrase) < MIN_PASSPHRASE_LEN:
        st.error(
            f"Use at least {MIN_PASSPHRASE_LEN} characters. This passphrase is "
            "the only thing protecting the book if the file is ever stolen."
        )
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
        st.session_state.pop("_switch_book", None)
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
            st.session_state.pop("_switch_book", None)
            books.set_active_book(dbconn.DATABASE_PATH)
            st.rerun()
    with c2:
        if st.button("Take over the book"):
            book_lock.takeover(dbconn.DATABASE_PATH)
            dbconn.set_active_key(st.session_state.pop("_pending_book_key"))
            st.session_state.pop("_book_lock_holder", None)
            st.session_state.pop("_switch_book", None)
            books.set_active_book(dbconn.DATABASE_PATH)
            st.rerun()
    with c3:
        if st.button("Cancel"):
            st.session_state.pop("_pending_book_key", None)
            st.session_state.pop("_book_lock_holder", None)
            st.rerun()


def _render_switch_screen():
    """The gate after "Switch book…": choosing is the point, so the chooser
    IS the screen — no passphrase form for the book being left."""
    st.caption(
        "A **book** is one encrypted file that holds clients — each client "
        "with its own chart of accounts, entries, and reports. One book for "
        "your whole practice is the normal setup; you switch clients inside "
        "it without ever coming back here. Start a **separate book** only "
        "when data should be fully walled off from your real work — a demo "
        "or training book, or a firm book kept on a shared drive."
    )
    if st.button("← Keep using the current book"):
        st.session_state.pop("_switch_book", None)
        st.rerun()
    st.divider()

    recents = [p for p in books.recent_books()
               if str(p) != str(dbconn.DATABASE_PATH)]
    if recents:
        st.subheader("Open a recent book")
        pick = st.selectbox(
            "Recent books", options=[str(p) for p in recents], index=None,
            placeholder="Choose a book", key="book_recent_pick",
            label_visibility="collapsed",
        )
        if pick and st.button("Open selected", key="book_open_recent"):
            books.set_active_book(pick)
            st.session_state.pop("_switch_book", None)
            st.rerun()

    st.subheader("Start a new book")
    new_name = st.text_input(
        "Name the new book",
        placeholder="e.g. Northline Digital, or Smith & Co",
        key="book_new_name",
    )
    st.caption("Created in LedgerTB's data folder on this computer; "
               "you set its passphrase on the next screen.")
    if new_name.strip() and st.button("Create this book", type="primary",
                                      key="book_create_named"):
        safe = "".join(ch for ch in new_name.strip() if ch not in "/\\:")
        target = Path(books.USER_DATA_DIR) / "Books" / f"{safe}{books.BOOK_EXTENSION}"
        target.parent.mkdir(parents=True, exist_ok=True)
        books.set_active_book(target)
        st.session_state.pop("_switch_book", None)
        st.rerun()

    with st.expander("Open or create a book at a specific path "
                     "(shared-drive books)"):
        other = st.text_input(
            "Full path to the book file",
            placeholder=r"e.g. /Volumes/Shared/Books/SmithCo.ledgertb",
            key="book_other_path",
        )
        if other and st.button("Open this path", key="book_open_other"):
            books.set_active_book(other.strip())
            st.session_state.pop("_switch_book", None)
            st.rerun()

    st.caption(f"Current book: `{dbconn.DATABASE_PATH}`")


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
                st.session_state.pop("_switch_book", None)
                st.rerun()
        other = st.text_input(
            "Open or create another book by path",
            placeholder=r"e.g. /Volumes/Shared/Books/SmithCo.ledgertb",
            key="book_other_path",
        )
        if other and st.button("Open this path", key="book_open_other"):
            books.set_active_book(other.strip())
            st.session_state.pop("_switch_book", None)
            st.rerun()
        st.caption(
            "A book is one encrypted file that holds clients — your whole "
            "practice usually lives in a single book, and adding a client "
            "never creates a new one. Separate books are for data you want "
            "fully walled off (a demo book) or for firm books on a shared "
            "drive, where an in-use lock keeps two people from writing to "
            "one book at once. Each book has a passphrase — using the same "
            "one for all your books is fine (one office passphrase), and "
            "\"Remember on this computer\" skips the prompt entirely."
        )


def _render_gate(state: str):
    # Keep the lock screen clean: no client nav, just the passphrase prompt.
    st.markdown(f"## 🔒 {APP_NAME}")
    if st.session_state.get("_book_lock_holder"):
        _render_lock_choice()
        return
    if st.session_state.get("_switch_book"):
        _render_switch_screen()
        return
    if state == "encrypted":
        _unlock_form()
    elif state == "plaintext":
        _migrate_form()
    else:  # "absent"
        _setup_form()
    _render_book_chooser()
    st.divider()
    st.caption(
        "LedgerTB is software, not accounting, tax, legal, audit, assurance, "
        "or other professional advice. You are responsible for verifying its "
        "outputs, protecting client data, and maintaining recovery copies."
    )
    gate_links = st.columns(2)
    with gate_links[0]:
        st.page_link(
            "pages/16_Help_and_Updates.py",
            label="Help & Updates",
            icon=":material/help:",
        )
    with gate_links[1]:
        st.page_link(
            "pages/15_Legal.py",
            label="Read Legal & Disclosures",
            icon=":material/gavel:",
        )


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
    # Deliberately not st.form: a form batches its inputs and cannot show the
    # strength read until submit, and this is the one security choice the user
    # actually makes. Same reason the client forms use containers.
    st.caption(
        "First run — create a passphrase to encrypt this database. It is not "
        "stored anywhere, so if you lose it the data cannot be recovered. "
        "**Four random words** (\"stapler-harbor-mint-cyclone\") beat a short "
        "complicated password: easier to remember, far harder to crack."
    )
    passphrase = st.text_input("New passphrase", type="password",
                               key="setup_passphrase")
    _render_strength(passphrase)
    confirm = st.text_input("Confirm passphrase", type="password",
                            key="setup_passphrase_confirm")
    remember = st.checkbox(
        "Remember on this computer",
        key="setup_remember",
        help="Stores this book's unlock key in your system credential "
             "vault, so opening the app skips the passphrase. Anyone who can "
             "sign in to your computer account can then open the book. Undo "
             "any time on Data Safety.",
    )
    if st.button("Create encrypted database", type="primary",
                 key="setup_submit"):
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
    passphrase = st.text_input("New passphrase", type="password",
                               key="migrate_passphrase")
    _render_strength(passphrase)
    confirm = st.text_input("Confirm passphrase", type="password",
                            key="migrate_passphrase_confirm")
    if st.button("Encrypt existing database", type="primary",
                 key="migrate_submit"):
        if _valid_new_passphrase(passphrase, confirm):
            try:
                encrypt_plaintext_db(dbconn.DATABASE_PATH, passphrase)
            except Exception as exc:
                st.error(f"The database was not changed: {exc}")
            else:
                _finish_unlock(derive_key(passphrase))
