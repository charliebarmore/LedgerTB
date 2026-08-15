"""Help and update guidance stays useful without making a network request."""

import ast
from pathlib import Path

import streamlit as st
from streamlit.testing.v1 import AppTest

from tests.conftest import page_path
from version import APP_VERSION


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "pages" / "16_Help_and_Updates.py"


def test_help_page_requires_no_book_and_shows_local_version(monkeypatch):
    monkeypatch.setattr(st, "page_link", lambda *args, **kwargs: None)
    monkeypatch.setattr(st.sidebar, "page_link", lambda *args, **kwargs: None)
    at = AppTest.from_file(page_path(PAGE.relative_to(ROOT)), default_timeout=30).run()

    assert not at.exception
    assert any("Help & Updates" in title.value for title in at.title)
    assert any(f"Installed version: LedgerTB {APP_VERSION}" in caption.value
               for caption in at.caption)
    assert any("does not contact GitHub" in info.value for info in at.info)


def test_help_page_uses_links_without_network_client_imports():
    source = PAGE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )

    assert imported_roots.isdisjoint({"requests", "httpx", "urllib", "socket"})
    assert "st.link_button" in source
    assert "releases/latest" in source
    assert "template=bug_report.yml" in source
    assert "template=feature_request.yml" in source
    assert "/security/policy" in source


def test_help_links_are_discoverable_in_app_and_on_site():
    navigation = (ROOT / "utils" / "client_selector.py").read_text(encoding="utf-8")
    gate = (ROOT / "utils" / "unlock.py").read_text(encoding="utf-8")
    site = (ROOT / "site" / "index.html").read_text(encoding="utf-8")

    assert 'pages/16_Help_and_Updates.py", label="Help & Updates"' in navigation
    assert 'pages/16_Help_and_Updates.py"' in gate
    assert "Latest release" in site
    assert "Report a bug" in site
    assert "Request a feature" in site
