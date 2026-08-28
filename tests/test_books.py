"""Firm mode: book-file registry, the in-use lock protocol, and book switching."""
import json
import os

import pytest

from database import connection as dbconn
from utils import book_lock, books


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(books, "SETTINGS_PATH", tmp_path / "books.json")
    monkeypatch.setattr(books, "DEFAULT_BOOK", tmp_path / "default.db")
    monkeypatch.delenv("LEDGERTB_DB_PATH", raising=False)
    monkeypatch.delenv("PROBOOKS_DB_PATH", raising=False)
    return tmp_path


def test_registry_round_trip_and_recents(settings, tmp_path):
    assert books.active_book() == tmp_path / "default.db"

    books.set_active_book(tmp_path / "SmithCo.db")
    books.set_active_book(tmp_path / "JonesLLC.db")
    books.set_active_book(tmp_path / "SmithCo.db")  # reopen: moves to front

    assert books.active_book() == tmp_path / "SmithCo.db"
    recents = [p.name for p in books.recent_books()]
    assert recents[0] == "default.db"  # default is always offered
    assert recents.index("SmithCo.db") < recents.index("JonesLLC.db")
    assert recents.count("SmithCo.db") == 1  # deduped

    # The env override (dev/tests) beats the saved choice.
    os.environ["LEDGERTB_DB_PATH"] = str(tmp_path / "env.db")
    try:
        assert books.active_book() == tmp_path / "default.db"
    finally:
        del os.environ["LEDGERTB_DB_PATH"]


def test_legacy_env_override_and_book_extension_remain_supported(settings,
                                                                 tmp_path):
    """Renaming must not strand config-managed installs or existing books."""
    legacy_book = tmp_path / "Existing Client.probooks"
    books.set_active_book(legacy_book)
    assert books.active_book() == legacy_book

    os.environ["PROBOOKS_DB_PATH"] = str(tmp_path / "managed.db")
    try:
        # DEFAULT_BOOK was resolved from the environment at process startup;
        # seeing the legacy variable here must still suppress a saved choice.
        assert books.active_book() == books.DEFAULT_BOOK
    finally:
        del os.environ["PROBOOKS_DB_PATH"]


def test_local_book_detection_is_conservative(settings, tmp_path, monkeypatch):
    managed = tmp_path / "managed"
    monkeypatch.setattr(books, "USER_DATA_DIR", managed)
    monkeypatch.setattr(books, "DEFAULT_BOOK", managed / "ledgertb.db")

    assert books.is_local_book(managed / "ledgertb.db")
    assert books.is_local_book(managed / "client-books" / "Smith.db")
    assert not books.is_local_book(tmp_path / "shared" / "Smith.db")


def test_lock_acquire_conflict_takeover_release(settings, tmp_path):
    book = tmp_path / "shared.db"

    assert book_lock.acquire(book)["acquired"] is True
    holder = book_lock.read_lock(book)
    assert holder["pid"] == os.getpid()
    # Re-acquiring our own lock is fine (same process reopening).
    assert book_lock.acquire(book)["acquired"] is True

    # Someone on another machine holds it: refused, holder reported.
    other = {"user": "colleague", "host": "OFFICE-PC", "pid": 1234,
             "opened_at": "2026-08-04T15:00:00+00:00"}
    book_lock.lock_path(book).write_text(json.dumps(other))
    result = book_lock.acquire(book)
    assert result["acquired"] is False
    assert "colleague on OFFICE-PC" in book_lock.describe(result["holder"])
    description = book_lock.describe(result["holder"])
    assert "T15:00:00+00:00" not in description
    assert " at " in description

    # release() never removes someone else's lock…
    book_lock.release(book)
    assert book_lock.read_lock(book)["user"] == "colleague"
    # …but a deliberate takeover replaces it, and release then clears ours.
    assert book_lock.takeover(book)["acquired"] is True
    book_lock.release(book)
    assert book_lock.read_lock(book) is None


def test_stale_lock_from_this_machine_is_reclaimed(settings, tmp_path):
    import getpass
    import socket

    book = tmp_path / "crashed.db"
    stale = {"user": getpass.getuser(), "host": socket.gethostname(),
             "pid": 99999999, "opened_at": "2026-08-04T09:00:00+00:00"}
    book_lock.lock_path(book).write_text(json.dumps(stale))

    result = book_lock.acquire(book)
    assert result["acquired"] is True
    assert book_lock.read_lock(book)["pid"] == os.getpid()


def test_switching_books_isolates_data(client_id, accounts, tmp_path):
    from database import init_database
    from models.client import Client

    original = dbconn.DATABASE_PATH
    assert any(c.id == client_id for c in Client.get_all())

    try:
        dbconn.DATABASE_PATH = tmp_path / "book-b.db"
        init_database()
        assert Client.get_all() == []  # a fresh book knows nothing of book A
        Client(name="Book B Client").save(seed_accounts=False)
        assert len(Client.get_all()) == 1
    finally:
        dbconn.DATABASE_PATH = original

    names = [c.name for c in Client.get_all()]
    assert "Book B Client" not in names
    assert any(c.id == client_id for c in Client.get_all())


def test_remembered_key_round_trip_and_stale_cleanup(client_id, monkeypatch):
    """'Remember on this Mac': a saved key unlocks without a prompt; a key
    that no longer opens the book is dropped from the vault."""
    from utils import unlock
    from utils.secure_store import get_secret, set_secret

    name = unlock.saved_key_name(dbconn.DATABASE_PATH)
    real_key = dbconn.get_active_key()
    assert real_key

    set_secret(name, real_key)
    dbconn.clear_active_key()
    assert unlock.try_saved_key() is True
    assert dbconn.has_active_key()

    # Wrong key (passphrase changed): refused AND forgotten.
    set_secret(name, "00" * 32)
    dbconn.clear_active_key()
    assert unlock.try_saved_key() is False
    assert not dbconn.has_active_key()
    assert get_secret(name) is None

    # Nothing saved: quietly declines.
    assert unlock.try_saved_key() is False
    dbconn.set_active_key(real_key)  # restore for teardown


def test_switch_book_flag_suppresses_auto_unlock(db, monkeypatch):
    """Data Safety's "Switch book…" must reach the chooser even when the
    passphrase is remembered — auto-unlock used to reopen the same book on
    the very next run, so the button appeared to do nothing."""
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    import utils.client_selector as selector
    from tests.conftest import page_path

    monkeypatch.setattr(selector, "render_client_selector", lambda: None)
    monkeypatch.setattr(st, "page_link", lambda *a, **k: None)

    calls = []
    monkeypatch.setattr("utils.unlock.try_saved_key",
                        lambda: calls.append(1) or True)
    monkeypatch.setattr("utils.books.active_book",
                        lambda: dbconn.DATABASE_PATH)

    key = dbconn.get_active_key()
    dbconn.clear_active_key()
    try:
        at = AppTest.from_file(page_path("pages/9_Data_Safety.py"),
                               default_timeout=30)
        at.session_state["_switch_book"] = True
        at.run()
        assert not at.exception
        assert not calls, "auto-unlock ran despite the switch-book request"
        assert any("Keep using the current book" in b.label for b in at.button)
        # The switch screen leads with choosing/creating, not a passphrase
        # form for the book being left.
        assert any(ti.key == "book_new_name" for ti in at.text_input)
        assert not any("Unlock" in b.label for b in at.button)
    finally:
        dbconn.set_active_key(key)


def test_create_book_by_name_actually_creates(db, monkeypatch, tmp_path):
    """Clicking 'Create this book' must run to completion — the first ship
    of this screen raised NameError on the click, which rendering-only
    assertions couldn't catch."""
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    import utils.books as books
    import utils.client_selector as selector
    from tests.conftest import page_path

    monkeypatch.setattr(selector, "render_client_selector", lambda: None)
    monkeypatch.setattr(st, "page_link", lambda *a, **k: None)
    monkeypatch.setattr("utils.unlock.try_saved_key", lambda: False)
    monkeypatch.setattr(books, "USER_DATA_DIR", tmp_path)
    monkeypatch.setattr(books, "SETTINGS_PATH", tmp_path / "books.json")
    monkeypatch.setattr(books, "active_book", lambda: dbconn.DATABASE_PATH)

    chosen = []
    monkeypatch.setattr(books, "set_active_book",
                        lambda p: chosen.append(str(p)))

    key = dbconn.get_active_key()
    dbconn.clear_active_key()
    try:
        at = AppTest.from_file(page_path("pages/9_Data_Safety.py"),
                               default_timeout=30)
        at.session_state["_switch_book"] = True
        at.run()
        next(ti for ti in at.text_input
             if ti.key == "book_new_name").input("Demo").run()
        next(b for b in at.button if b.key == "book_create_named").click().run()
        assert not at.exception
        assert chosen and chosen[0].endswith("Demo.ledgertb")
        assert (tmp_path / "Books").is_dir()
        assert "_switch_book" not in at.session_state
    finally:
        dbconn.set_active_key(key)


def test_ui_token_gate_refuses_sessions_the_app_did_not_open(db, monkeypatch):
    """The unlock is process-wide, so a second session reaching the local port
    would otherwise get the decrypted books with no passphrase. Only the window
    the launcher opened carries the token."""
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    import utils.client_selector as selector
    from tests.conftest import page_path
    from utils import unlock

    monkeypatch.setattr(selector, "render_client_selector", lambda: None)
    monkeypatch.setattr(st, "page_link", lambda *a, **k: None)
    monkeypatch.setenv(unlock.UI_TOKEN_ENV, "launch-secret")

    # No token: blocked before any book content renders.
    at = AppTest.from_file(page_path("pages/7_Dashboard.py"), default_timeout=30)
    at.run()
    assert not at.exception
    assert any("was not opened by" in e.value for e in at.error)

    # Wrong token is no better than none.
    at = AppTest.from_file(page_path("pages/7_Dashboard.py"), default_timeout=30)
    at.query_params["t"] = "guessed"
    at.run()
    assert any("was not opened by" in e.value for e in at.error)

    # The real token opens the app, and the session stays authorized
    # afterwards even though navigation drops the query parameter.
    at = AppTest.from_file(page_path("pages/7_Dashboard.py"), default_timeout=30)
    at.query_params["t"] = "launch-secret"
    at.run()
    assert not any("was not opened by" in e.value for e in at.error)
    at.query_params.clear()
    at.run()
    assert not any("was not opened by" in e.value for e in at.error)


def test_no_token_configured_means_no_gate(db, monkeypatch):
    """Running from source there is no launcher to mint a token; the gate must
    stay out of the way rather than locking the developer out."""
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    import utils.client_selector as selector
    from tests.conftest import page_path
    from utils import unlock

    monkeypatch.setattr(selector, "render_client_selector", lambda: None)
    monkeypatch.setattr(st, "page_link", lambda *a, **k: None)
    monkeypatch.delenv(unlock.UI_TOKEN_ENV, raising=False)

    at = AppTest.from_file(page_path("pages/7_Dashboard.py"), default_timeout=30)
    at.run()
    assert not any("was not opened by" in e.value for e in at.error)


def test_refuses_to_serve_when_bound_off_loopback(db, monkeypatch):
    """A user following a 'share your Streamlit app' guide must not silently
    publish unlocked books to the office network."""
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    import utils.client_selector as selector
    from tests.conftest import page_path

    monkeypatch.setattr(selector, "render_client_selector", lambda: None)
    monkeypatch.setattr(st, "page_link", lambda *a, **k: None)
    real_get_option = st.get_option
    monkeypatch.setattr(st, "get_option",
                        lambda name: "0.0.0.0" if name == "server.address"
                        else real_get_option(name))

    at = AppTest.from_file(page_path("pages/7_Dashboard.py"), default_timeout=30)
    at.run()
    assert any("reachable from other computers" in e.value for e in at.error)


def test_launchers_mint_a_token_and_pass_it_out_of_argv():
    """The token must reach the child through the environment, never argv —
    other users on the machine can read a process's command line."""
    from pathlib import Path

    for name in ("run_ledgertb.py", "desktop.py"):
        source = (Path(__file__).parents[1] / name).read_text()
        assert "secrets.token_urlsafe" in source, name
        assert "LEDGERTB_UI_TOKEN" in source, name
        assert "window_url" in source, name
        assert "text_select=True" in source, name
        assert "--ui-token" not in source, name


def test_passphrase_strength_pushes_toward_several_words():
    from utils.unlock import MIN_PASSPHRASE_LEN, passphrase_strength

    assert MIN_PASSPHRASE_LEN >= 12
    assert passphrase_strength("Passw0rd!")[0] == "weak"
    assert passphrase_strength("correct horse battery staple")[0] == "strong"
    assert passphrase_strength("a" * 24)[0] == "strong"
    assert passphrase_strength("northwind ledger 42")[0] in ("good", "strong")
