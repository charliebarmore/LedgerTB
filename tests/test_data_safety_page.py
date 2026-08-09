import streamlit as st
from streamlit.testing.v1 import AppTest

from tests.conftest import page_path


def _patched(monkeypatch):
    import utils.client_selector as selector

    monkeypatch.setattr(selector, "render_client_selector", lambda: None)
    monkeypatch.setattr(st, "page_link", lambda *a, **k: None)


def test_stale_backup_alone_is_a_warning_not_a_red_banner(db, monkeypatch):
    """A missing backup must never read as 'TEST DATA ONLY' over real books."""
    import services.production_readiness as pr

    _patched(monkeypatch)
    checks = [
        pr.SafetyCheck("book_encrypted", "The book itself is encrypted", True, "ok"),
        pr.SafetyCheck("backup", "Recent verified backup", False,
                       "No backup exists yet.", required=False),
    ]
    monkeypatch.setattr(pr, "get_safety_checks", lambda: checks)
    at = AppTest.from_file(page_path("pages/9_Data_Safety.py"), default_timeout=30).run()

    assert not at.exception
    assert any("Data Safety" in title.value for title in at.title)
    assert any("no recent verified backup" in w.value for w in at.warning)
    assert not at.error
    all_text = " ".join([w.value for w in at.warning] + [e.value for e in at.error]
                        + [s.value for s in at.success])
    assert "TEST DATA" not in all_text and "production" not in all_text.lower()


def test_failed_protection_shows_the_red_banner(db, monkeypatch):
    import services.production_readiness as pr

    _patched(monkeypatch)
    checks = [
        pr.SafetyCheck("book_encrypted", "The book itself is encrypted", False,
                       "This book is NOT encrypted."),
    ]
    monkeypatch.setattr(pr, "get_safety_checks", lambda: checks)
    at = AppTest.from_file(page_path("pages/9_Data_Safety.py"), default_timeout=30).run()

    assert not at.exception
    assert any("not fully protected" in e.value for e in at.error)


def test_api_key_setup_lives_on_firm_settings_not_data_safety(db, monkeypatch):
    """The key is firm-level configuration; Data Safety keeps backups/encryption."""
    _patched(monkeypatch)

    safety = AppTest.from_file(page_path("pages/9_Data_Safety.py"), default_timeout=30).run()
    assert not safety.exception
    assert not any(ti.key == "firm_settings_api_key" for ti in safety.text_input)

    firm = AppTest.from_file(page_path("pages/12_Firm_Settings.py"), default_timeout=30).run()
    assert not firm.exception
    assert any(ti.key == "firm_settings_api_key" for ti in firm.text_input)
    assert any("AI categorization" in s.value for s in firm.subheader)


def test_plaintext_migration_copy_can_be_removed_from_data_safety(db, monkeypatch):
    import sqlite3

    from database import connection as dbconn
    from database.crypto import plaintext_backup_path

    _patched(monkeypatch)
    backup = plaintext_backup_path(dbconn.DATABASE_PATH)
    conn = sqlite3.connect(backup)
    conn.execute("CREATE TABLE sensitive (value TEXT)")
    conn.commit()
    conn.close()

    at = AppTest.from_file(
        page_path("pages/9_Data_Safety.py"), default_timeout=30
    ).run()
    assert not at.exception
    assert any("Unencrypted migration copy found" in w.value for w in at.warning)

    at.text_input(key="plaintext_backup_delete_confirm").input(
        "DELETE PLAINTEXT"
    )
    at.button(key="delete_plaintext_migration_backup").click().run()

    assert not at.exception
    assert not backup.exists()
    assert any("after verifying the encrypted book" in s.value for s in at.success)


def test_legacy_backups_can_be_adopted_from_data_safety(db, monkeypatch):
    """Drive the actual Adopt click — rendering-only assertions can't catch a
    crash in the handler (the Create-book NameError lesson)."""
    import services.backups as backups_mod

    _patched(monkeypatch)
    monkeypatch.setattr(backups_mod, "legacy_backup_count", lambda *a, **k: 2)
    calls = []
    monkeypatch.setattr(
        backups_mod, "adopt_legacy_backups",
        lambda *a, **k: calls.append(1) or {
            "adopted": ["probooks-a.db", "probooks-b.db"], "skipped": []})

    at = AppTest.from_file(page_path("pages/9_Data_Safety.py"),
                           default_timeout=30).run()
    assert not at.exception
    assert any("older backup" in i.value for i in at.info)

    at.button(key="adopt_legacy_backups").click().run()
    assert not at.exception
    assert calls, "the Adopt button never reached the adoption service"
    assert any("Adopted 2 backup(s)" in s.value for s in at.success)
