from datetime import date
from io import BytesIO
from types import SimpleNamespace

import pytest
from reportlab.pdfgen import canvas

from services import document_import
from services.document_import import (
    extract_document,
    parse_statement_text,
    parse_statement_with_ai,
)


def _make_pdf(text: str = "", *, password: str = "") -> bytes:
    buffer = BytesIO()
    document = canvas.Canvas(buffer, encrypt=password or None)
    if text:
        document.drawString(72, 720, text)
    document.showPage()
    document.save()
    return buffer.getvalue()


def test_local_parser_handles_signed_amounts_running_balances_and_continuations():
    text = """
01/02 Beginning Balance 1,000.00
01/03 OFFICE DEPOT -45.67 954.33
additional merchant detail
01/05 CLIENT PAYMENT $250.00 1,204.33
01/08 CARD REFUND (12.25) 1,216.58
"""
    transactions, skipped = parse_statement_text(text, 2026, amount_strategy="first")

    assert skipped == ["01/02 Beginning Balance 1,000.00"]
    assert [row["date"] for row in transactions] == [
        date(2026, 1, 3), date(2026, 1, 5), date(2026, 1, 8)
    ]
    assert [row["amount"] for row in transactions] == [-45.67, 250, -12.25]
    assert transactions[0]["description"] == "OFFICE DEPOT"


def test_local_parser_can_choose_last_amount_and_reports_unparsed_rows():
    text = "01/03 VENDOR 45.00 955.00\n01/04 DESCRIPTION WITHOUT AN AMOUNT"
    transactions, skipped = parse_statement_text(text, 2026, amount_strategy="last")

    assert transactions[0]["amount"] == 955
    assert skipped == ["01/04 DESCRIPTION WITHOUT AN AMOUNT"]


def test_native_pdf_text_is_extracted_without_ocr(monkeypatch):
    content = _make_pdf(
        "01/15/2026 OFFICE DEPOT -123.45 Statement transaction text"
    )
    monkeypatch.setattr(
        document_import, "_vision_ocr",
        lambda _: (_ for _ in ()).throw(AssertionError("OCR should not run")),
    )

    result = extract_document("statement.pdf", content)

    assert result.page_count == 1
    assert result.native_text_pages == 1
    assert result.ocr_pages == 0
    assert "OFFICE DEPOT" in result.text


def test_image_and_scanned_pdf_use_local_ocr(monkeypatch):
    recognized = "01/15/2026 OFFICE DEPOT -123.45"

    def recognize_png(content):
        assert content.startswith(b"\x89PNG\r\n\x1a\n")
        return recognized

    monkeypatch.setattr(document_import, "_vision_ocr", recognize_png)

    image_result = extract_document("statement.png", b"\x89PNG\r\n\x1a\nplaceholder")
    assert image_result.text == recognized
    assert image_result.ocr_pages == 1

    pdf_result = extract_document("scan.pdf", _make_pdf())
    assert pdf_result.ocr_pages == 1
    assert recognized in pdf_result.text


def test_document_validation_rejects_mismatched_or_protected_inputs():
    with pytest.raises(ValueError, match="valid PDF"):
        extract_document("statement.pdf", b"not a pdf")
    with pytest.raises(ValueError, match="Supported"):
        extract_document("statement.docx", b"data")
    with pytest.raises(ValueError, match="Password-protected"):
        extract_document(
            "protected.pdf",
            _make_pdf("Private", password="secret"),  # pragma: allowlist secret
        )


def test_ai_parser_uses_structured_output_and_normalized_amounts(monkeypatch):
    tool_block = SimpleNamespace(
        type="tool_use",
        input={"transactions": [{
            "date": "2026-01-15", "description": "Office Depot", "amount": -123.45
        }]},
    )

    class FakeMessages:
        def create(self, **kwargs):
            assert kwargs["tool_choice"]["name"] == "extract_statement_transactions"
            assert "credit-card/liability" in kwargs["messages"][0]["content"]
            return SimpleNamespace(content=[tool_block])

    class FakeAnthropic:
        def __init__(self, api_key):
            assert api_key == "secret"  # pragma: allowlist secret
            self.messages = FakeMessages()

    monkeypatch.setattr("anthropic.Anthropic", FakeAnthropic)
    transactions = parse_statement_with_ai(
        "statement text",
        "Liability",
        "secret",  # pragma: allowlist secret
        "model",
    )

    assert transactions[0]["date"] == date(2026, 1, 15)
    assert transactions[0]["amount"] == -123.45
