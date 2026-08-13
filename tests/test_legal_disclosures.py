"""Legal disclosures stay visible and aligned across every distribution surface."""
from html.parser import HTMLParser
from pathlib import Path

import streamlit as st
from streamlit.testing.v1 import AppTest

from tests.conftest import page_path


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        target = values.get("href") if tag in {"a", "link"} else values.get("src")
        if target:
            self.targets.append(target)


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_repository_disclosures_cover_the_product_risks():
    disclaimer = _read("DISCLAIMER.md")
    privacy = _read("PRIVACY.md")
    terms = _read("TERMS.md")

    for phrase in (
        "not accounting, tax,\nlegal, investment, audit, assurance",
        "does not create a CPA-client",
        "A balanced trial\nbalance is not proof",
        "AI-generated suggestions and agent actions can be incomplete",
        'provided **"AS IS" and "WITH ALL FAULTS,"',
    ):
        assert phrase in disclaimer

    for phrase in (
        "no Ledger Labs cloud account or hosted bookkeeping database",
        "source build without SQLCipher warns",
        "does not include product analytics",
        "MCP client you connect",
        "static site hosted by Cloudflare Pages",
        "requests fonts from Google Fonts",
    ):
        assert phrase in privacy

    assert "Open-source license controls the code" in terms
    assert "Nothing in\nthese terms reduces or restricts the rights" in terms


def test_readme_and_bundle_point_to_every_disclosure():
    readme = _read("README.md")
    spec = _read("LedgerTB.spec")
    for name in ("DISCLAIMER.md", "PRIVACY.md", "TERMS.md"):
        assert name in readme
        assert f'("{name}", ".")' in spec


def test_marketing_site_puts_disclosures_before_downloads():
    index = _read("site/index.html")
    notice = index.index("Before using LedgerTB")
    first_download = index.index("Download for Mac")
    assert notice < first_download

    for name in ("terms.html", "disclaimer.html", "privacy.html"):
        assert index.count(f'href="{name}"') >= 2
        assert (SITE / name).is_file()

    assert "software, not accounting, tax, legal" in index
    assert "provided “as is” without warranties" in index


def test_every_local_legal_page_asset_and_link_exists():
    for page_name in ("disclaimer.html", "privacy.html", "terms.html"):
        parser = _Links()
        parser.feed(_read(f"site/{page_name}"))
        for target in parser.targets:
            if target.startswith(("https://", "mailto:", "#")):
                continue
            if target == "/":
                expected = SITE / "index.html"
            else:
                expected = SITE / target.lstrip("/")
            assert expected.exists(), f"{page_name}: missing {target}"


def test_in_app_legal_page_requires_no_book(monkeypatch):
    monkeypatch.setattr(st, "page_link", lambda *args, **kwargs: None)
    monkeypatch.setattr(st.sidebar, "page_link", lambda *args, **kwargs: None)
    at = AppTest.from_file(page_path("pages/15_Legal.py"), default_timeout=30).run()

    assert not at.exception
    assert any("Legal & Disclosures" in title.value for title in at.title)
    assert any("general-purpose software" in warning.value for warning in at.warning)
    text = " ".join(item.value for item in at.markdown)
    assert "does not create a CPA-client" in text
    assert "AS IS" in text
