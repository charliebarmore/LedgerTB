"""Changing a book's passphrase.

The risk here is not a wrong number on a page, it is an unreadable book, so
most of these tests are about what survives a failure.
"""

import sqlite3
from pathlib import Path

import pytest

from database import connection as dbconn
from database.crypto import (
    change_passphrase,
    database_state,
    derive_key,
    verify_passphrase,
)

OLD = "old-passphrase-here"
NEW = "new-passphrase-here"


@pytest.fixture
def book(tmp_path, monkeypatch):
    """An encrypted book with something in it, opened under OLD."""
    path = tmp_path / "rotate.ledgertb"
    monkeypatch.setattr(dbconn, "DATABASE_PATH", path)
    import config
    import services.backups as _backups

    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(_backups, "DEFAULT_BACKUP_DIR", tmp_path / "backups")
    # Rotation refuses a book outside LedgerTB's managed area, so the throwaway
    # book has to actually be inside one rather than have the check bypassed.
    import utils.books as _books

    monkeypatch.setattr(_books, "USER_DATA_DIR", tmp_path)
    dbconn.set_active_key(derive_key(OLD))

    from database import init_database
    init_database()

    from models.client import Client
    Client(name="Kettle Ridge Cabinetry", entity_type="S-Corp",
           fiscal_year_end_month=12).save(seed_accounts=True)

    yield path
    dbconn.clear_active_key()


# --- the happy path ---------------------------------------------------------

def test_the_new_passphrase_opens_the_book(book):
    change_passphrase(book, derive_key(OLD), NEW)

    assert verify_passphrase(book, NEW) is True


def test_the_old_passphrase_stops_working(book):
    change_passphrase(book, derive_key(OLD), NEW)

    assert verify_passphrase(book, OLD) is False


def test_the_data_survives(book):
    from models.client import Client
    from models.account import Account

    before = [c.name for c in Client.get_all()]
    accounts_before = Account.count(Client.get_all()[0].id)

    new_key = change_passphrase(book, derive_key(OLD), NEW)
    dbconn.set_active_key(new_key)

    assert [c.name for c in Client.get_all()] == before
    assert Account.count(Client.get_all()[0].id) == accounts_before
    assert accounts_before > 0


def test_the_book_is_still_encrypted_afterwards(book):
    change_passphrase(book, derive_key(OLD), NEW)

    assert database_state(book) == "encrypted"
    # Belt and braces: a plain sqlite3 driver must not be able to read it.
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(str(book)).execute("SELECT count(*) FROM sqlite_master")


def test_no_copy_under_the_old_passphrase_is_left_behind(book):
    """Rotating after a passphrase leaks is pointless if the old file stays."""
    change_passphrase(book, derive_key(OLD), NEW)

    leftovers = [p.name for p in Path(book).parent.iterdir()
                 if p.name != Path(book).name and "rekey" in p.name]
    assert leftovers == []


def test_it_returns_the_new_key(book):
    assert change_passphrase(book, derive_key(OLD), NEW) == derive_key(NEW)


# --- refusals ---------------------------------------------------------------

def test_a_wrong_current_key_changes_nothing(book):
    with pytest.raises(Exception):
        change_passphrase(book, derive_key("not-the-passphrase"), NEW)

    assert verify_passphrase(book, OLD) is True
    assert verify_passphrase(book, NEW) is False


def test_reusing_the_same_passphrase_is_refused(book):
    with pytest.raises(ValueError, match="same as the current one"):
        change_passphrase(book, derive_key(OLD), OLD)

    assert verify_passphrase(book, OLD) is True


def test_an_empty_new_passphrase_is_refused(book):
    with pytest.raises(ValueError, match="cannot be empty"):
        change_passphrase(book, derive_key(OLD), "")


def test_a_book_that_is_not_encrypted_has_nothing_to_rotate(tmp_path):
    plain = tmp_path / "plain.db"
    sqlite3.connect(str(plain)).execute("CREATE TABLE t (id INTEGER)")

    with pytest.raises(RuntimeError, match="Only an encrypted book"):
        change_passphrase(plain, derive_key(OLD), NEW)


# --- surviving a failure ----------------------------------------------------

def test_a_failed_swap_leaves_the_original_untouched(book, monkeypatch):
    """There is no partway any more. The replace either happens or it does not,
    and a failure leaves the book exactly as it was."""
    import database.crypto as crypto

    def refuse(src, dst):
        raise OSError("simulated failure at the swap")

    # A scoped context, not monkeypatch.undo(): undo() unwinds every patch this
    # test's monkeypatch has made, including the book fixture's DATABASE_PATH,
    # which sends the assertions below at the real book instead of this one.
    with monkeypatch.context() as patched:
        patched.setattr(crypto.os, "replace", refuse)
        with pytest.raises(OSError):
            crypto.change_passphrase(book, derive_key(OLD), NEW)

    assert book.exists()
    assert verify_passphrase(book, OLD) is True
    assert verify_passphrase(book, NEW) is False
    assert not (book.parent / (book.name + ".rekey.tmp")).exists()

    from models.client import Client
    dbconn.set_active_key(derive_key(OLD))
    assert [c.name for c in Client.get_all()] == ["Kettle Ridge Cabinetry"]


def test_the_live_path_is_never_absent(book, monkeypatch):
    """The old two-rename swap left a window with no file at the book's path,
    and a crash there reads to the unlock gate as a first run: it would offer
    to create a fresh empty book while the real one sat beside it. One replace
    onto the live path removes the window entirely."""
    import database.crypto as crypto

    calls = []
    real = crypto.os.replace

    def record(src, dst):
        calls.append((str(src), str(dst)))
        return real(src, dst)

    with monkeypatch.context() as patched:
        patched.setattr(crypto.os, "replace", record)
        crypto.change_passphrase(book, derive_key(OLD), NEW)

    assert len(calls) == 1, "more than one rename reopens the window"
    assert calls[0][1] == str(book), "the only replace must land on the book itself"
    assert verify_passphrase(book, NEW) is True


def test_a_concurrent_write_aborts_and_changes_nothing(book, monkeypatch):
    """Another connection committing mid-rotation must abort it, not silently
    discard that commit when the file is replaced."""
    import database.crypto as crypto

    seen = []
    real = crypto._data_version

    def moving(conn):
        seen.append(1)
        # First read is the baseline; the second reports someone else's commit.
        return real(conn) + (1 if len(seen) > 1 else 0)

    with monkeypatch.context() as patched:
        patched.setattr(crypto, "_data_version", moving)
        with pytest.raises(RuntimeError, match="Another connection wrote"):
            crypto.change_passphrase(book, derive_key(OLD), NEW)

    assert verify_passphrase(book, OLD) is True
    assert verify_passphrase(book, NEW) is False
    assert not (book.parent / (book.name + ".rekey.tmp")).exists()


def test_the_cipher_page_size_is_carried_across(book):
    """sqlcipher_export does not carry it, and a file left on a build's default
    would open here while failing against a build compiled differently."""
    import sqlcipher3
    from database.crypto import key_pragma

    def page_size(path, key):
        conn = sqlcipher3.connect(str(path))
        try:
            conn.execute(f"PRAGMA key = {key_pragma(key)}")
            return conn.execute("PRAGMA cipher_page_size").fetchone()[0]
        finally:
            conn.close()

    before = page_size(book, derive_key(OLD))
    change_passphrase(book, derive_key(OLD), NEW)

    assert page_size(book, derive_key(NEW)) == before


# --- the app-level wrapper --------------------------------------------------

def test_the_wrapper_swaps_the_active_key(book):
    from utils.unlock import change_book_passphrase
    from models.client import Client

    change_book_passphrase(NEW)

    assert dbconn.get_active_key() == derive_key(NEW)
    # The open session keeps working, without re-unlocking.
    assert [c.name for c in Client.get_all()] == ["Kettle Ridge Cabinetry"]


def test_the_wrapper_updates_a_remembered_key(book):
    """Otherwise the next launch holds a key that no longer opens the book."""
    from utils import secure_store
    from utils.unlock import change_book_passphrase, saved_key_name, try_saved_key

    secure_store.set_secret(saved_key_name(book), derive_key(OLD))
    change_book_passphrase(NEW)

    assert secure_store.get_secret(saved_key_name(book)) == derive_key(NEW)

    dbconn.clear_active_key()
    assert try_saved_key() is True


def test_the_wrapper_leaves_an_unremembered_book_unremembered(book):
    """Rotating must not start saving a key nobody asked it to save."""
    from utils import secure_store
    from utils.unlock import change_book_passphrase, saved_key_name

    change_book_passphrase(NEW)

    assert secure_store.get_secret(saved_key_name(book)) is None


def test_the_wrapper_enforces_the_minimum_length(book):
    from utils.unlock import change_book_passphrase

    with pytest.raises(ValueError, match="at least"):
        change_book_passphrase("short")

    assert verify_passphrase(book, OLD) is True


def test_the_wrapper_refuses_when_the_book_is_locked(book):
    from utils.unlock import change_book_passphrase

    dbconn.clear_active_key()
    with pytest.raises(RuntimeError, match="must be unlocked"):
        change_book_passphrase(NEW)


# --- the audit action -------------------------------------------------------

def test_the_rekey_action_can_actually_be_written(book):
    """AUDIT_ACTIONS and the audit_log CHECK have to move together; migration
    021 is what makes this write something other than an IntegrityError."""
    from models.audit_log import AuditLog
    from models.client import Client

    client = Client.get_all()[0]
    AuditLog.log_event(client.id, "REKEY", "book_passphrase_changed",
                       {"book": str(book)})

    events = AuditLog.get_history("book_passphrase_changed", 0)
    assert events and events[0].action == "REKEY"


# --- the event has to survive an empty book --------------------------------

def test_a_rotation_is_recorded_on_a_book_with_no_clients(tmp_path, monkeypatch):
    """The case that used to lose the event, and the worst one to lose it in:
    a book still being set up or handed over."""
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from models.audit_log import AuditLog
    from models.client import Client
    from tests.conftest import page_path
    import utils.client_selector as selector

    monkeypatch.setattr(dbconn, "DATABASE_PATH", tmp_path / "empty.ledgertb")
    import config
    import services.backups as _backups

    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(_backups, "DEFAULT_BACKUP_DIR", tmp_path / "backups")
    import utils.books as _books

    monkeypatch.setattr(_books, "USER_DATA_DIR", tmp_path)
    dbconn.set_active_key(derive_key(OLD))
    from database import init_database
    init_database()
    assert Client.get_all() == [], "this test needs a book nobody has set up"

    monkeypatch.setattr(selector, "render_client_selector", lambda: None)
    monkeypatch.setattr(st, "page_link", lambda *a, **k: None)

    page = AppTest.from_file(page_path("pages/9_Data_Safety.py"), default_timeout=30).run()
    page.text_input(key="rekey_new").set_value("a-recorded-passphrase")
    page.text_input(key="rekey_confirm").set_value("a-recorded-passphrase")
    next(b for b in page.button if "Change passphrase" in b.label).click().run()

    assert not page.exception
    events = AuditLog.get_history("book_passphrase_changed", 0)
    assert events, "a passphrase change must leave a record"
    assert events[0].action == "REKEY"
    assert events[0].client_id is None, "the book owns this event, not a client"


def test_a_book_level_event_shows_in_every_client_trail(book):
    """It affected the book each client lives in, so it belongs in each view."""
    from datetime import date, timedelta
    from models.audit_log import AuditLog
    from models.client import Client

    first = Client.get_all()[0].id
    second = Client(name="Second Co", entity_type="S-Corp",
                    fiscal_year_end_month=12).save(seed_accounts=False)

    AuditLog.log_event(None, "REKEY", "book_passphrase_changed", {"book": "x"})

    window = (date.today() - timedelta(days=1), date.today() + timedelta(days=1))
    for client_id in (first, second):
        actions = [e.action for e in AuditLog.get_all(client_id, *window)]
        assert "REKEY" in actions


# --- preconditions and ordering --------------------------------------------

def test_a_read_only_session_cannot_rotate(book, monkeypatch):
    """A reader must not rekey the book underneath the session holding it."""
    from utils.unlock import change_book_passphrase

    monkeypatch.setattr(dbconn, "READ_ONLY", True)
    with pytest.raises(RuntimeError, match="read-only"):
        change_book_passphrase(NEW)

    assert verify_passphrase(book, OLD) is True


def test_assistant_access_must_be_turned_off_first(book):
    """Its key lives in another process this one cannot stop, so updating the
    vault entry afterwards would not invalidate what is already loaded."""
    from utils import secure_store
    from utils.assistant_access import credential_names
    from utils.unlock import change_book_passphrase

    secure_store.set_secret(credential_names(book).key, derive_key(OLD))

    with pytest.raises(RuntimeError, match="assistant access off"):
        change_book_passphrase(NEW)

    assert verify_passphrase(book, OLD) is True


def test_a_new_key_backup_is_taken_before_the_book_is_touched(book):
    """The recovery point must open with the passphrase just chosen, not one
    the person may never have known."""
    from services.backups import list_backups
    from utils.unlock import change_book_passphrase
    import config

    result = change_book_passphrase(NEW)

    assert result.backup_path is not None and result.backup_path.exists()
    # It opens with the NEW passphrase, though it was taken before the swap.
    conn = dbconn.open_keyed(result.backup_path, derive_key(NEW))
    try:
        assert conn.execute("SELECT count(*) FROM clients").fetchone()[0] == 1
    finally:
        conn.close()


def test_a_failed_backup_stops_the_rotation(book, monkeypatch):
    """No recovery point, no rotation."""
    import utils.unlock as unlock_mod
    import services.backups as backups

    monkeypatch.setattr(backups, "create_backup",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(RuntimeError, match="No backup could be taken"):
        unlock_mod.change_book_passphrase(NEW)

    assert verify_passphrase(book, OLD) is True


def test_the_post_check_reports_what_this_machine_verified(book):
    from utils.unlock import change_book_passphrase

    result = change_book_passphrase(NEW)

    assert result.new_key_opens is True
    assert result.old_key_refused is True
    assert result.integrity_ok is True
    assert result.verified is True
    assert result.warnings == []


def test_a_vault_failure_after_the_swap_is_a_warning_not_a_failure(book, monkeypatch):
    """The most dangerous message in the feature was reporting that nothing
    changed when the new key was already live."""
    from utils import secure_store
    from utils.unlock import change_book_passphrase, saved_key_name

    secure_store.set_secret(saved_key_name(book), derive_key(OLD))

    def refuse(name, value):
        raise RuntimeError("vault locked")

    monkeypatch.setattr(secure_store, "set_secret", refuse)

    result = change_book_passphrase(NEW)

    assert result.new_key_opens is True
    assert any("remembered key" in w for w in result.warnings)
    assert verify_passphrase(book, NEW) is True


# --- backups follow the book, up to a point --------------------------------

def _backup(reason="manual"):
    from services.backups import create_backup
    import config
    return create_backup(config.BACKUP_DIR, reason=reason, apply_retention=False)


def test_recent_backups_are_converted_to_the_new_passphrase(book, monkeypatch, tmp_path):
    import config
    from utils.unlock import change_book_passphrase

    old_backup = _backup().database_path
    result = change_book_passphrase(NEW)

    assert result.backups_converted >= 1
    conn = dbconn.open_keyed(old_backup, derive_key(NEW))
    try:
        assert conn.execute("SELECT count(*) FROM clients").fetchone()[0] == 1
    finally:
        conn.close()


def test_even_an_old_backup_is_converted(book, monkeypatch, tmp_path):
    """The whole archive follows the book. A partial re-key would leave
    passphrase tiers the recovery UI cannot open, which the maintainer ruled
    out: one slower rotation beats an archive where which passphrase opens
    which recovery point depends on its age."""
    import json
    from datetime import datetime, timedelta, timezone
    import config
    import services.backups as backups
    from utils.unlock import change_book_passphrase

    stale = _backup().database_path
    manifest = stale.with_suffix(".json")
    payload = json.loads(manifest.read_text())
    payload["created_at"] = (
        datetime.now(timezone.utc) - timedelta(days=120)
    ).isoformat()
    manifest.write_text(json.dumps(payload))

    change_book_passphrase(NEW)

    # Converted despite its age, so the archive stays on one passphrase.
    conn = dbconn.open_keyed(stale, derive_key(NEW))
    try:
        conn.execute("SELECT count(*) FROM clients").fetchone()
    finally:
        conn.close()

    record = backups.load_record(stale)
    assert backups.opens_with_active_key(record) is True


def test_converting_backups_is_idempotent(book, monkeypatch, tmp_path):
    import config
    import services.backups as backups
    from database.crypto import key_fingerprint

    _backup()
    first, _ = backups.rekey_backups(derive_key(OLD), derive_key(NEW))
    second, _ = backups.rekey_backups(derive_key(OLD), derive_key(NEW))

    assert first >= 1
    assert second == 0, "an already-converted backup must be left alone"


def test_one_unconvertible_backup_does_not_stop_the_rest(book):
    import services.backups as backups
    from utils.unlock import change_book_passphrase

    import json
    from database.crypto import rekey_file

    good = _backup().database_path
    # A backup from two passphrases ago: intact and listable, but the current
    # key is not the one that opens it, so it cannot be converted.
    stranded = _backup().database_path
    rekey_file(stranded, derive_key(OLD), derive_key("a-third-passphrase"))
    manifest = stranded.with_suffix(".json")
    payload = json.loads(manifest.read_text())
    payload["sha256"] = backups._sha256(stranded)
    manifest.write_text(json.dumps(payload))

    result = change_book_passphrase(NEW)

    assert any(stranded.name in w for w in result.warnings)
    assert result.new_key_opens is True, "a bad backup must not fail the rotation"
    conn = dbconn.open_keyed(good, derive_key(NEW))
    try:
        conn.execute("SELECT count(*) FROM clients").fetchone()
    finally:
        conn.close()


# --- the interprocess maintenance lock -------------------------------------

def test_a_non_local_book_is_refused(book, monkeypatch, tmp_path):
    """Cross-machine exclusivity cannot be seen from here, so rotation does not
    claim it. The maintainer's call: refuse rather than qualify the success."""
    import utils.books as books_mod
    from utils.unlock import change_book_passphrase

    monkeypatch.setattr(books_mod, "USER_DATA_DIR", tmp_path / "somewhere-else")

    with pytest.raises(RuntimeError, match="shared drive"):
        change_book_passphrase(NEW)

    assert verify_passphrase(book, OLD) is True


def test_rotation_refuses_while_an_assistant_write_is_active(book):
    """A running MCP call cannot be stopped by switching access off, so the
    lock refuses instead of racing it."""
    from utils import maintenance_lock
    from utils.unlock import change_book_passphrase

    with maintenance_lock.writer(book):
        with pytest.raises(maintenance_lock.MaintenanceBusy, match="still in use"):
            change_book_passphrase(NEW)

    assert verify_passphrase(book, OLD) is True


def test_any_other_thread_is_refused_during_maintenance(book):
    """Not only assistants. The desktop app runs concurrent Streamlit session
    threads, and a write started in one during a rotation would commit into the
    file the swap is about to unlink. The holder's own thread stays exempt,
    because it is the one doing the work."""
    import threading
    from utils import maintenance_lock

    refused = {}

    def other_thread():
        try:
            dbconn.get_connection().close()
            refused["result"] = "allowed"
        except dbconn.DatabaseLocked as exc:
            refused["result"] = str(exc)

    with maintenance_lock.hold(book):
        # The holder itself must still be able to work on the book.
        dbconn.get_connection().close()

        worker = threading.Thread(target=other_thread)
        worker.start()
        worker.join(timeout=5)

    assert "being maintained" in refused["result"], refused["result"]


def test_the_lock_is_released_after_a_rotation(book):
    from utils import maintenance_lock
    from utils.unlock import change_book_passphrase

    change_book_passphrase(NEW)

    assert not maintenance_lock.under_maintenance(book)


def test_the_lock_is_released_when_a_rotation_fails(book, monkeypatch):
    import database.crypto as crypto
    from utils import maintenance_lock
    from utils.unlock import change_book_passphrase

    def boom(*a, **k):
        raise RuntimeError("simulated")

    monkeypatch.setattr(crypto, "change_passphrase", boom)
    import utils.unlock as unlock_mod
    monkeypatch.setattr(unlock_mod, "change_passphrase", boom)

    with pytest.raises(RuntimeError, match="simulated"):
        change_book_passphrase(NEW)

    assert not maintenance_lock.under_maintenance(book), "a stuck lock blocks the book forever"


def test_two_maintenance_holds_cannot_overlap(book):
    from utils import maintenance_lock

    with maintenance_lock.hold(book):
        with pytest.raises(maintenance_lock.MaintenanceBusy, match="already running"):
            with maintenance_lock.hold(book):
                pass


# --- what the second review found ------------------------------------------

def test_an_assistant_write_holds_a_shared_lock_while_in_flight(book):
    """The wider tool guard must exclude maintenance for its whole lifetime."""
    from utils import maintenance_lock

    with maintenance_lock.writer(book):
        with pytest.raises(maintenance_lock.MaintenanceBusy):
            with maintenance_lock.hold(book):
                pass

    with maintenance_lock.hold(book):
        pass


def test_overlapping_writes_on_two_threads_keep_independent_shared_locks(book):
    """Finishing one MCP call must not release another call's shared lock."""
    import threading
    from utils import maintenance_lock

    second_in = threading.Event()
    first_out = threading.Event()
    seen = {}

    def second_writer():
        with maintenance_lock.writer(book):
            second_in.set()
            first_out.wait(timeout=5)
            try:
                with maintenance_lock.hold(book):
                    pass
            except maintenance_lock.MaintenanceBusy:
                seen["after_first_exited"] = "blocked"

    worker = threading.Thread(target=second_writer)
    with maintenance_lock.writer(book):
        worker.start()
        assert second_in.wait(timeout=5)
        with pytest.raises(maintenance_lock.MaintenanceBusy):
            with maintenance_lock.hold(book):
                pass
    first_out.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert seen["after_first_exited"] == "blocked"
    with maintenance_lock.hold(book):
        pass


def test_nesting_on_one_thread_reuses_the_same_shared_lock(book):
    """A guarded tool may call another guarded helper; the inner one finishing
    must not retract the outer one's claim."""
    from utils import maintenance_lock

    with maintenance_lock.writer(book):
        with maintenance_lock.writer(book):
            with pytest.raises(maintenance_lock.MaintenanceBusy):
                with maintenance_lock.hold(book):
                    pass
        with pytest.raises(maintenance_lock.MaintenanceBusy):
            with maintenance_lock.hold(book):
                pass

    with maintenance_lock.hold(book):
        pass


def test_an_assistant_write_is_refused_during_maintenance(book):
    from utils import maintenance_lock

    with maintenance_lock.hold(book):
        with pytest.raises(maintenance_lock.MaintenanceBusy):
            with maintenance_lock.writer(book):
                pass


def test_an_undeclared_assistant_write_cannot_reach_the_database(book):
    """The declaration is the contract; this is the enforcement. A tool that
    mutates without declaring it must fail rather than bypass the lock."""
    from models.client import Client

    dbconn.ASSISTANT_ACCESS_LEVEL = "post"
    try:
        with pytest.raises(Exception) as caught:
            Client(name="Undeclared", entity_type="S-Corp",
                   fiscal_year_end_month=12).save(seed_accounts=False)
        assert "readonly" in str(caught.value).lower() or \
               "read-only" in str(caught.value).lower(), str(caught.value)
    finally:
        dbconn.ASSISTANT_ACCESS_LEVEL = None


def test_coordination_file_contents_do_not_represent_lock_state(book):
    """The persistent sidecar contains no PID or stale state to reclaim."""
    from utils import maintenance_lock

    maintenance_lock.maintenance_path(book).write_text("arbitrary old bytes\n")

    with maintenance_lock.hold(book):
        pass
    assert not maintenance_lock.under_maintenance(book)


def test_a_live_holders_lock_is_not_reclaimed(book):
    from utils import maintenance_lock

    with maintenance_lock.hold(book):
        with pytest.raises(maintenance_lock.MaintenanceBusy):
            with maintenance_lock.hold(book):
                pass


def test_a_failure_after_the_swap_never_reports_failure(book, monkeypatch):
    """R7. The new key is live, so the only honest outcome is success plus a
    warning naming the follow-up."""
    from utils.unlock import change_book_passphrase

    def refuse(_key):
        raise RuntimeError("simulated key publication failure")

    monkeypatch.setattr(dbconn, "set_active_key", refuse)

    result = change_book_passphrase(NEW)

    assert any("could not switch" in w for w in result.warnings)
    assert verify_passphrase(book, NEW) is True


def test_an_interrupted_backup_conversion_repairs_itself(book, monkeypatch):
    """Crash between replacing a backup and its manifest: the backup is on the
    new key, its record says otherwise. Preserve and repair, never delete."""
    import services.backups as backups
    from database.crypto import rekey_file

    record = _backup()
    # Convert the file but not its manifest, exactly as an interrupted run would.
    rekey_file(record.database_path, derive_key(OLD), derive_key(NEW))
    dbconn.set_active_key(derive_key(NEW))

    repaired = backups.load_record(record.database_path)

    assert backups.opens_with_active_key(repaired) is True
    assert repaired.database_path.exists(), "it must never be deleted"


def test_interrupted_conversion_repairs_a_legacy_manifest(book, monkeypatch):
    """The transition marker supplies proof when the old manifest has no key
    fingerprint, as was true for backups made before v1.4.3."""
    import json

    import services.backups as backups

    record = _backup()
    payload = json.loads(record.manifest_path.read_text())
    payload.pop("key_fingerprint")
    record.manifest_path.write_text(json.dumps(payload))

    original_write = backups._write_manifest_atomic
    writes = 0

    def stop_before_final_manifest(path, content):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise SystemExit("simulated interruption")
        original_write(path, content)

    monkeypatch.setattr(backups, "_write_manifest_atomic", stop_before_final_manifest)
    with pytest.raises(SystemExit, match="simulated interruption"):
        backups.rekey_backups(derive_key(OLD), derive_key(NEW))

    dbconn.set_active_key(derive_key(NEW))
    monkeypatch.setattr(backups, "_write_manifest_atomic", original_write)
    repaired = backups.load_record(record.database_path)
    repaired_payload = json.loads(record.manifest_path.read_text())

    assert backups.opens_with_active_key(repaired) is True
    assert "rekey_in_progress" not in repaired_payload
    assert repaired_payload["key_fingerprint"] == repaired.key_fingerprint


def test_a_tampered_backup_is_still_rejected(book):
    """The repair above must not launder tampering into a fresh checksum."""
    import services.backups as backups

    record = _backup()
    with record.database_path.open("ab") as handle:
        handle.write(b"tampered")

    with pytest.raises(ValueError, match="checksum"):
        backups.load_record(record.database_path)


def test_a_windows_style_replace_refusal_is_explained(book, monkeypatch):
    """Verified on Windows 11 / NTFS: os.replace raises WinError 5 if anything
    holds the target open, including a read handle or a live SQLite connection.
    It fails safely, but "Access is denied" does not tell anyone what to do."""
    import database.crypto as crypto

    def refuse(src, dst):
        raise PermissionError(5, "Access is denied")

    with monkeypatch.context() as patched:
        patched.setattr(crypto.os, "replace", refuse)
        patched.setattr(crypto, "_on_windows", lambda: True)
        with pytest.raises(RuntimeError, match="Another program has this book open"):
            crypto.change_passphrase(book, derive_key(OLD), NEW)

    assert verify_passphrase(book, OLD) is True
    assert not (book.parent / (book.name + ".rekey.tmp")).exists()


def test_a_refused_swap_leaves_the_recovery_set_as_it_was(book, monkeypatch):
    """D4: the pre-swap backup is written under the NEW key. If the swap is
    refused, the live book is still on the old key, so leaving that backup in
    the managed set is the mixed tier the design exists to avoid."""
    import services.backups as backups
    import database.crypto as crypto
    from utils.unlock import change_book_passphrase

    before = {r.database_path.name for r in backups.list_backups()}

    # Patch the swap itself, not os.replace: crypto.os IS the os module, so
    # patching it there breaks the backup step before the swap is reached.
    def refuse(tmp_new, path):
        raise RuntimeError("Another program has this book open")

    with monkeypatch.context() as patched:
        patched.setattr(crypto, "_replace_or_explain", refuse)
        with pytest.raises(RuntimeError) as caught:
            change_book_passphrase(NEW)

    assert "moved to" in str(caught.value), "the user must be told where it went"
    after = {r.database_path.name for r in backups.list_backups()}
    assert after == before, "the managed set must be as it was found"
    assert verify_passphrase(book, OLD) is True


def test_the_quarantined_backup_is_kept_not_destroyed(book, monkeypatch):
    """Backups are preserved. It leaves the managed set; it does not vanish."""
    import database.crypto as crypto
    from utils.unlock import change_book_passphrase

    def refuse(tmp_new, path):
        raise RuntimeError("Another program has this book open")

    with monkeypatch.context() as patched:
        patched.setattr(crypto, "_replace_or_explain", refuse)
        with pytest.raises(RuntimeError):
            change_book_passphrase(NEW)

    quarantined = list((book.parent / "backups").rglob("failed-rotation/*.db"))
    assert quarantined, "the bytes must still exist somewhere"


def test_a_book_name_with_glob_characters_uses_the_same_lock_protocol(tmp_path):
    """User-controlled book names are literal paths, never glob patterns."""
    from utils import maintenance_lock

    book = tmp_path / "Client [2026] books.ledgertb"
    book.write_bytes(b"not really a book")

    with maintenance_lock.writer(book):
        with pytest.raises(maintenance_lock.MaintenanceBusy):
            with maintenance_lock.hold(book):
                pass

    with maintenance_lock.hold(book):
        pass


def test_a_posix_permission_error_is_not_blamed_on_another_program(book, monkeypatch):
    """On POSIX a rename is not refused for an open handle, so this is a real
    permission problem and the Windows advice would send someone chasing
    nothing."""
    import database.crypto as crypto

    def refuse(src, dst):
        raise PermissionError(13, "Permission denied")

    with monkeypatch.context() as patched:
        patched.setattr(crypto, "_on_windows", lambda: False)
        patched.setattr(crypto.os, "replace", refuse)
        with pytest.raises(RuntimeError, match="permissions"):
            crypto.change_passphrase(book, derive_key(OLD), NEW)
