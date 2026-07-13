from streamlit.testing.v1 import AppTest


def test_data_safety_page_renders_readiness_gate(db, monkeypatch):
    import utils.client_selector as selector

    monkeypatch.setattr(selector, "render_client_selector", lambda: None)
    at = AppTest.from_file("pages/9_Data_Safety.py", default_timeout=30).run()

    assert not at.exception
    assert any("Data Safety" in title.value for title in at.title)
    assert any("TEST DATA ONLY" in error.value for error in at.error)
