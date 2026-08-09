"""Firm branding must persist, validate, and land on the deliverables."""
from datetime import date
from io import BytesIO

import openpyxl
import pypdfium2 as pdfium
import pytest

from models.reports import ReportGenerator
from services.branding import get_branding, normalize_hex, save_branding
from services.close_package import build_close_package, build_close_package_pdf
from tests.conftest import post_entry

def _make_png() -> bytes:
    from PIL import Image as PILImage

    buffer = BytesIO()
    PILImage.new("RGB", (40, 12), "#B85439").save(buffer, "PNG")
    return buffer.getvalue()


_PNG = _make_png()


def test_normalize_hex():
    assert normalize_hex("#b85439") == "#B85439"
    assert normalize_hex("B85439") == "#B85439"
    assert normalize_hex("red") == ""
    assert normalize_hex("") == ""


def test_branding_round_trip_with_logo(db):
    assert get_branding().is_branded is False
    save_branding("Charles J Barmore CPA PC", tagline="Evans, GA",
                  accent_hex="#B85439", logo=_PNG, logo_mime="image/png")
    branding = get_branding()
    assert branding.firm_name == "Charles J Barmore CPA PC"
    assert branding.accent_hex == "#B85439"
    assert branding.logo == _PNG

    # Re-saving without a logo keeps the stored one…
    save_branding("Charles J Barmore CPA PC", accent_hex="#B85439")
    assert get_branding().logo == _PNG
    # …and explicit removal drops it.
    save_branding("Charles J Barmore CPA PC", keep_existing_logo=False)
    assert get_branding().logo is None


def test_logo_validation(db):
    with pytest.raises(ValueError, match="PNG or JPEG"):
        save_branding("Firm", logo=_PNG, logo_mime="image/svg+xml")
    with pytest.raises(ValueError, match="2MB"):
        save_branding("Firm", logo=b"x" * (2 * 1024 * 1024 + 1),
                      logo_mime="image/png")


def test_close_package_carries_the_brand(client_id, accounts):
    post_entry(client_id, date(2026, 1, 15),
               [(accounts["cash"], 250, 0), (accounts["revenue"], 0, 250)])
    save_branding("Charles J Barmore CPA PC", tagline="cbarmorecpa.com",
                  accent_hex="#B85439", logo=_PNG, logo_mime="image/png")

    period = (date(2026, 1, 1), date(2026, 3, 31))
    tb_rows, _ = ReportGenerator.trial_balance_worksheet(client_id, *period)

    pdf = build_close_package_pdf(client_id, "Test Co", *period, tb_rows)
    doc = pdfium.PdfDocument(pdf.read())
    try:
        pages = []
        image_count = 0
        for page_index in range(len(doc)):
            page = doc[page_index]
            try:
                text_page = page.get_textpage()
                try:
                    pages.append(text_page.get_text_range())
                finally:
                    text_page.close()
                image_count += len(list(page.get_objects(
                    filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]
                )))
            finally:
                page.close()
        text = "\n".join(pages)
        assert "Charles J Barmore CPA PC" in text
        assert "cbarmorecpa.com" in text
        assert image_count >= 1  # the logo made the masthead
    finally:
        doc.close()

    wb = openpyxl.load_workbook(BytesIO(build_close_package(
        client_id, "Test Co", *period, tb_rows).read()))
    summary = {wb["Summary"].cell(row=i, column=1).value:
               wb["Summary"].cell(row=i, column=2).value
               for i in range(1, wb["Summary"].max_row + 1)}
    assert summary.get("Prepared by") == "Charles J Barmore CPA PC"


def test_unbranded_package_still_builds(client_id, accounts):
    post_entry(client_id, date(2026, 1, 15),
               [(accounts["cash"], 250, 0), (accounts["revenue"], 0, 250)])
    period = (date(2026, 1, 1), date(2026, 3, 31))
    tb_rows, _ = ReportGenerator.trial_balance_worksheet(client_id, *period)
    pdf = build_close_package_pdf(client_id, "Test Co", *period, tb_rows)
    assert pdf.read().startswith(b"%PDF")
