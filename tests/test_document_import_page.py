from streamlit.testing.v1 import AppTest


def test_statement_upload_screen_renders(client_id, accounts, monkeypatch):
    import utils.client_selector as selector
    monkeypatch.setattr(selector, "render_client_selector", lambda: client_id)

    at = AppTest.from_file("pages/4_Import_Transactions.py", default_timeout=30).run()
    assert not at.exception

    at.radio[0].set_value("Upload Statement").run()
    assert not at.exception
    assert any("Upload PDF or Image Statement" in heading.value for heading in at.subheader)
    assert any("run locally on this Mac" in caption.value for caption in at.caption)
