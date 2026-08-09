"""Local-first extraction and parsing for PDF and image bank statements."""

import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Optional


MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES = 50
MAX_IMAGE_PIXELS = 40_000_000
MAX_AI_TEXT_CHARS = 80_000
SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}


@dataclass
class DocumentExtraction:
    text: str
    page_count: int
    ocr_pages: int = 0
    native_text_pages: int = 0
    warnings: list[str] = field(default_factory=list)


def _validate_document(filename: str, content: bytes) -> str:
    extension = Path(filename or "").suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError("Supported statement files are PDF, PNG, JPG, and JPEG.")
    if not content:
        raise ValueError("The uploaded statement is empty.")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("The statement exceeds the 25 MB local-processing limit.")
    if extension == ".pdf" and not content.startswith(b"%PDF"):
        raise ValueError("The uploaded file does not appear to be a valid PDF.")
    if extension == ".png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("The uploaded file does not appear to be a valid PNG image.")
    if extension in {".jpg", ".jpeg"} and not content.startswith(b"\xff\xd8\xff"):
        raise ValueError("The uploaded file does not appear to be a valid JPEG image.")
    return extension


def _vision_ocr(image_bytes: bytes) -> str:
    """Recognize text with Apple's on-device Vision framework."""
    import objc
    import Quartz

    objc.loadBundle(
        "Vision", globals(),
        bundle_path="/System/Library/Frameworks/Vision.framework",
    )
    request_class = objc.lookUpClass("VNRecognizeTextRequest")
    handler_class = objc.lookUpClass("VNImageRequestHandler")
    source = Quartz.CGImageSourceCreateWithData(image_bytes, None)
    if source is None:
        raise ValueError("macOS could not decode the statement image.")
    properties = Quartz.CGImageSourceCopyPropertiesAtIndex(source, 0, None) or {}
    width = int(properties.get(Quartz.kCGImagePropertyPixelWidth, 0) or 0)
    height = int(properties.get(Quartz.kCGImagePropertyPixelHeight, 0) or 0)
    if width and height and width * height > MAX_IMAGE_PIXELS:
        raise ValueError("The statement image exceeds the 40-megapixel safety limit.")
    image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
    if image is None:
        raise ValueError("macOS could not render the statement image.")

    request = request_class.alloc().init()
    request.setRecognitionLevel_(1)  # VNRequestTextRecognitionLevelAccurate
    request.setUsesLanguageCorrection_(True)
    handler = handler_class.alloc().initWithCGImage_options_(image, {})
    if not handler.performRequests_error_([request], None):
        raise RuntimeError("macOS Vision could not recognize text on this page.")

    observations = []
    for observation in request.results() or []:
        candidates = observation.topCandidates_(1)
        if not candidates:
            continue
        box = observation.boundingBox()
        observations.append((box.origin.y, box.origin.x, candidates[0].string()))

    # Vision coordinates start at the lower left. Group nearby observations into
    # visual rows so statement dates, descriptions, and amounts stay together.
    observations.sort(key=lambda item: (-item[0], item[1]))
    rows: list[tuple[float, list[tuple[float, str]]]] = []
    for y, x, text in observations:
        row = next((candidate for candidate in rows if abs(candidate[0] - y) < 0.012), None)
        if row is None:
            row = (y, [])
            rows.append(row)
        row[1].append((x, text))
    return "\n".join(
        " ".join(text for _, text in sorted(parts)) for _, parts in rows
    )


def extract_document(filename: str, content: bytes) -> DocumentExtraction:
    """Extract statement text locally, using OCR only where necessary."""
    extension = _validate_document(filename, content)
    if extension != ".pdf":
        text = _vision_ocr(content)
        if not text.strip():
            raise ValueError("No readable text was found in the statement image.")
        return DocumentExtraction(text=text, page_count=1, ocr_pages=1)

    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(content)
    except pdfium.PdfiumError as exc:
        if exc.err_code == pdfium.raw.FPDF_ERR_PASSWORD:
            raise ValueError(
                "Password-protected PDFs must be unlocked before import."
            ) from exc
        raise ValueError(f"The PDF could not be opened: {exc}") from exc
    except Exception as exc:
        raise ValueError(f"The PDF could not be opened: {exc}") from exc
    try:
        page_count = len(document)
        if page_count > MAX_PDF_PAGES:
            raise ValueError(f"The PDF has more than {MAX_PDF_PAGES} pages.")

        page_text = []
        ocr_pages = 0
        native_pages = 0
        warnings = []
        for page_index in range(page_count):
            page_number = page_index + 1
            page = document[page_index]
            try:
                text_page = page.get_textpage()
                try:
                    text = text_page.get_text_range().strip()
                finally:
                    text_page.close()
                if len(re.sub(r"\W", "", text)) >= 30:
                    native_pages += 1
                else:
                    width, height = page.get_size()
                    scale = 2.2
                    estimated_pixels = width * height * scale * scale
                    if estimated_pixels > MAX_IMAGE_PIXELS:
                        scale = math.sqrt(MAX_IMAGE_PIXELS / (width * height))
                    bitmap = page.render(scale=scale)
                    try:
                        encoded = BytesIO()
                        bitmap.to_pil().save(encoded, format="PNG")
                    finally:
                        bitmap.close()
                    text = _vision_ocr(encoded.getvalue()).strip()
                    ocr_pages += 1
                    if not text:
                        warnings.append(f"Page {page_number}: no readable text found.")
                page_text.append(f"--- Page {page_number} ---\n{text}")
            finally:
                page.close()
        combined = "\n".join(page_text).strip()
        if not re.search(r"[A-Za-z0-9]", combined):
            raise ValueError("No readable text was found in this PDF.")
        return DocumentExtraction(
            text=combined, page_count=page_count, ocr_pages=ocr_pages,
            native_text_pages=native_pages, warnings=warnings,
        )
    finally:
        document.close()


_DATE_START = re.compile(
    r"^\s*(?P<date>(?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01])"
    r"(?:[/-](?:\d{2}|\d{4}))?|\d{4}-\d{1,2}-\d{1,2})\b"
)
_MONEY = re.compile(
    r"(?<![\w/])(?P<amount>[+-]?\$?\(?\d+(?:,\d{3})*\.\d{2}\)?-?)(?!\w)"
)
_NON_TRANSACTION_ROW = re.compile(
    r"\b(beginning|opening|ending|closing|previous|new|daily|available) balance\b",
    re.IGNORECASE,
)


def _parse_date(value: str, statement_year: int) -> Optional[date]:
    value = value.strip()
    formats = ["%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%m-%d-%y"]
    if value.count("/") == 1 or value.count("-") == 1:
        value = f"{value}/{statement_year}" if "/" in value else f"{value}-{statement_year}"
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _parse_money(value: str) -> float:
    cleaned = value.replace("$", "").replace(",", "").strip()
    negative = cleaned.startswith("-") or cleaned.endswith("-") or (
        cleaned.startswith("(") and cleaned.endswith(")")
    )
    cleaned = cleaned.strip("+-()").strip()
    amount = float(cleaned)
    return -abs(amount) if negative else amount


def parse_statement_text(
    text: str,
    statement_year: int,
    amount_strategy: str = "first",
) -> tuple[list[dict], list[str]]:
    """Parse date-led statement rows without sending data off the Mac.

    This intentionally handles only rows whose date and monetary values can be
    identified with confidence. The UI exposes the extracted text and parsed
    table for correction; ambiguous debit/credit tables should use the explicit
    AI-assisted option or a bank CSV export.
    """
    if amount_strategy not in {"first", "last"}:
        raise ValueError("Amount strategy must be 'first' or 'last'.")
    records = []
    current = None
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        match = _DATE_START.match(line)
        if match:
            if current:
                records.append(current)
            current = line
        elif current and line and not line.startswith("--- Page"):
            current += " " + line
    if current:
        records.append(current)

    transactions = []
    skipped = []
    batch_id = str(uuid.uuid4())[:8]
    for record in records:
        date_match = _DATE_START.match(record)
        parsed_date = _parse_date(date_match.group("date"), statement_year)
        remainder = record[date_match.end():].strip()
        amounts = list(_MONEY.finditer(remainder))
        if not parsed_date or not amounts or _NON_TRANSACTION_ROW.search(remainder):
            skipped.append(record[:160])
            continue
        chosen = amounts[0] if amount_strategy == "first" else amounts[-1]
        description = remainder[:chosen.start()].strip(" -|:")
        if not description:
            description = remainder[:160]
        transactions.append({
            "date": parsed_date,
            "description": description[:200],
            "amount": _parse_money(chosen.group("amount")),
            "batch_id": batch_id,
            "source_text": record[:500],
        })
    return transactions, skipped


_PARSE_TOOL = {
    "name": "extract_statement_transactions",
    "description": "Extract posted bank or credit-card transactions from statement text.",
    "input_schema": {
        "type": "object",
        "properties": {
            "transactions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                        "description": {"type": "string"},
                        "amount": {
                            "type": "number",
                            "description": "Negative for money out/purchases; positive for money in/payments or credits",
                        },
                    },
                    "required": ["date", "description", "amount"],
                },
            }
        },
        "required": ["transactions"],
    },
}


def parse_statement_with_ai(text: str, account_type: str, api_key: str, model: str) -> list[dict]:
    """Parse locally extracted text via Anthropic after explicit UI consent."""
    if not api_key:
        raise ValueError("An Anthropic API key is required for AI-assisted parsing.")
    if len(text) > MAX_AI_TEXT_CHARS:
        raise ValueError(
            "Extracted statement text is too large for AI-assisted parsing. Split the PDF by month."
        )
    from anthropic import Anthropic

    account_guidance = (
        "For this credit-card/liability statement, purchases and fees are negative; "
        "payments and credits are positive."
        if account_type == "Liability"
        else "For this bank/asset statement, withdrawals are negative and deposits are positive."
    )
    response = Anthropic(api_key=api_key).messages.create(
        model=model,
        max_tokens=8000,
        tools=[_PARSE_TOOL],
        tool_choice={"type": "tool", "name": "extract_statement_transactions"},
        messages=[{"role": "user", "content": (
            "Extract only posted transaction rows. Exclude beginning/ending balances, daily balances, "
            "subtotals, rewards summaries, and payments-due summaries. Preserve statement descriptions. "
            f"{account_guidance}\n\nSTATEMENT TEXT:\n{text}"
        )}],
    )
    tool_use = next((block for block in response.content if block.type == "tool_use"), None)
    if tool_use is None:
        raise ValueError("AI parsing did not return structured transactions.")
    batch_id = str(uuid.uuid4())[:8]
    transactions = []
    for row in tool_use.input.get("transactions", []):
        try:
            parsed_date = date.fromisoformat(row["date"])
            amount = float(row["amount"])
            description = str(row["description"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if description:
            transactions.append({
                "date": parsed_date, "description": description[:200],
                "amount": amount, "batch_id": batch_id,
            })
    if not transactions:
        raise ValueError("No valid transactions were found in the AI parsing result.")
    return transactions
