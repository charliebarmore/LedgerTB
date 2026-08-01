import streamlit as st
from streamlit.testing.v1 import AppTest


def _patched(monkeypatch):
    import utils.client_selector as selector

    monkeypatch.setattr(selector, "render_client_selector", lambda: None)
    monkeypatch.setattr(st, "page_link", lambda *a, **k: None)


def test_data_safety_page_renders_readiness_gate(db, monkeypatch):
    _patched(monkeypatch)
    at = AppTest.from_file("pages/9_Data_Safety.py", default_timeout=30).run()

    assert not at.exception
    assert any("Data Safety" in title.value for title in at.title)
    assert any("TEST DATA ONLY" in error.value for error in at.error)


def test_api_key_setup_lives_on_firm_settings_not_data_safety(db, monkeypatch):
    """The key is firm-level configuration; Data Safety keeps backups/encryption."""
    _patched(monkeypatch)

    safety = AppTest.from_file("pages/9_Data_Safety.py", default_timeout=30).run()
    assert not safety.exception
    assert not any(ti.key == "firm_settings_api_key" for ti in safety.text_input)

    firm = AppTest.from_file("pages/12_Firm_Settings.py", default_timeout=30).run()
    assert not firm.exception
    assert any(ti.key == "firm_settings_api_key" for ti in firm.text_input)
    assert any("AI categorization" in s.value for s in firm.subheader)
