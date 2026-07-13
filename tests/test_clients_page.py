"""Regression test for the Add Client form (0_Clients.py).

The entity-type / business-type description blurbs used to be stale because the
form was wrapped in st.form -- inside a form the selectbox return value (and
anything derived from it) doesn't refresh until submit. It's now a plain
container, so the description must track the current selection.
"""

import pytest


def _run_clients_page(monkeypatch, view="Add Client"):
    # Neutralize the sidebar nav (st.page_link) so AppTest can run this one page.
    import utils.client_selector as cs
    monkeypatch.setattr(cs, "render_client_selector", lambda *a, **k: 1)
    monkeypatch.setattr(cs, "apply_sidebar_style", lambda *a, **k: None)

    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file("pages/0_Clients.py", default_timeout=30)
    # The page defaults to the View Clients list; only the selected view
    # renders (unlike the old st.tabs, which rendered both).
    at.session_state["_clients_view_pending"] = view
    at.run()
    assert not at.exception
    return at


def _infos(at):
    return [i.value for i in at.info]


def test_add_client_entity_description_tracks_selection(db, monkeypatch):
    at = _run_clients_page(monkeypatch)

    # Default (index 0) shows the S-Corporation blurb...
    assert any(v.startswith("**S-Corporation**") for v in _infos(at))

    # ...select C-Corporation and the description must follow it (the old form
    # would have kept showing S-Corporation).
    at.selectbox(key="add_entity_type").set_value("C-Corporation").run()
    infos = _infos(at)
    assert any(v.startswith("**C-Corporation**") for v in infos)
    assert not any(v.startswith("**S-Corporation**") for v in infos)


def test_add_client_industry_description_tracks_selection(db, monkeypatch):
    at = _run_clients_page(monkeypatch)

    assert any(v.startswith("**Professional Services**") for v in _infos(at))

    at.selectbox(key="add_business_type").set_value("Real Estate (Rental)").run()
    infos = _infos(at)
    assert any(v.startswith("**Real Estate (Rental)**") for v in infos)
    assert not any(v.startswith("**Professional Services**") for v in infos)


def test_view_switcher_deep_link_and_default(db, monkeypatch):
    # Default view is the client list -- the add form is not rendered.
    at = _run_clients_page(monkeypatch, view="View Clients")
    assert at.radio(key="clients_view").value == "View Clients"
    assert not any(v.startswith("**S-Corporation**") for v in _infos(at))

    # A queued view (what the sidebar "Add client" button sets before
    # switch_page) must land directly on the add form.
    at = _run_clients_page(monkeypatch, view="Add Client")
    assert at.radio(key="clients_view").value == "Add Client"
    assert any(v.startswith("**S-Corporation**") for v in _infos(at))
