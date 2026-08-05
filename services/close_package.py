"""Close package: everything a reviewer needs to tie out a finished period.

Two formats from the same underlying data — an Excel workbook for further
work, and a single multi-section PDF for the permanent file: Summary, final
Trial Balance (with the worksheet columns), Transactions (every journal line
in the period), Adjusting Entries, and Receipts & Disbursements per cash
account.
"""
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from typing import List

import openpyxl
from openpyxl.styles import Font
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from database.connection import get_cursor
from models.reports import TrialBalanceWorksheetRow
from money import to_dollars
from services.branding import FirmBranding, get_branding
from utils.dates import long_date, long_datetime

_HEADER_FONT = Font(bold=True)
_MONEY_FMT = "#,##0.00"


@dataclass
class CashActivityRow:
    account_number: str
    account_name: str
    beginning: float
    receipts: float
    disbursements: float

    @property
    def ending(self) -> float:
        return round(self.beginning + self.receipts - self.disbursements, 2)


def get_period_transactions(client_id: int, period_start: date, period_end: date) -> List[dict]:
    """Every journal line in the period, in entry order."""
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT je.entry_date, je.id AS entry_id, je.entry_type,
                   je.description, je.source_reference,
                   a.account_number, a.name AS account_name,
                   jel.debit, jel.credit, jel.memo
            FROM journal_entries je
            JOIN journal_entry_lines jel ON jel.journal_entry_id = je.id
            JOIN accounts a ON a.id = jel.account_id
            WHERE je.client_id = ?
              AND je.entry_date BETWEEN ? AND ?
            ORDER BY je.entry_date, je.id, jel.id
            """,
            (client_id, period_start.isoformat(), period_end.isoformat()),
        )
        rows = cursor.fetchall()
    return [
        {
            "entry_date": row["entry_date"],
            "entry_id": row["entry_id"],
            "entry_type": row["entry_type"],
            "description": row["description"] or "",
            "source_reference": row["source_reference"] or "",
            "account_number": row["account_number"],
            "account_name": row["account_name"],
            "debit": to_dollars(row["debit"]),
            "credit": to_dollars(row["credit"]),
            "memo": row["memo"] or "",
        }
        for row in rows
    ]


def get_cash_activity(client_id: int, period_start: date, period_end: date) -> List[CashActivityRow]:
    """Beginning balance, receipts, disbursements, ending — per cash account.

    Receipts and disbursements are the debits and credits hitting each cash
    account in the period: the cash-basis view of the books from the bank's
    side, which is what a receipts-and-disbursements engagement summarizes.
    """
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT a.account_number, a.name AS account_name,
                   COALESCE(SUM(CASE WHEN je.entry_date < ?
                       THEN jel.debit - jel.credit ELSE 0 END), 0) AS beginning,
                   COALESCE(SUM(CASE WHEN je.entry_date BETWEEN ? AND ?
                       THEN jel.debit ELSE 0 END), 0) AS receipts,
                   COALESCE(SUM(CASE WHEN je.entry_date BETWEEN ? AND ?
                       THEN jel.credit ELSE 0 END), 0) AS disbursements
            FROM accounts a
            LEFT JOIN journal_entry_lines jel ON jel.account_id = a.id
            LEFT JOIN journal_entries je ON je.id = jel.journal_entry_id
            WHERE a.client_id = ? AND a.type = 'Asset' AND a.subtype = 'Cash'
            GROUP BY a.id
            ORDER BY a.account_number
            """,
            (
                period_start.isoformat(),
                period_start.isoformat(), period_end.isoformat(),
                period_start.isoformat(), period_end.isoformat(),
                client_id,
            ),
        )
        rows = cursor.fetchall()
    return [
        CashActivityRow(
            account_number=row["account_number"],
            account_name=row["account_name"],
            beginning=to_dollars(row["beginning"]),
            receipts=to_dollars(row["receipts"]),
            disbursements=to_dollars(row["disbursements"]),
        )
        for row in rows
    ]


def _write_table(ws, headers, data_rows, money_cols):
    ws.append(headers)
    for cell in ws[1]:
        cell.font = _HEADER_FONT
    for row in data_rows:
        ws.append(row)
    for col_idx in money_cols:
        for row in ws.iter_rows(min_row=2, min_col=col_idx, max_col=col_idx):
            for cell in row:
                cell.number_format = _MONEY_FMT
    widths = {1: 12, 2: 34}
    for col_idx, width in widths.items():
        if col_idx <= ws.max_column:
            ws.column_dimensions[openpyxl.utils.get_column_letter(col_idx)].width = width


def build_close_package(
    client_id: int,
    client_name: str,
    period_start: date,
    period_end: date,
    tb_rows: List[TrialBalanceWorksheetRow],
) -> BytesIO:
    """Build the close-package workbook and return it as an in-memory file."""
    transactions = get_period_transactions(client_id, period_start, period_end)
    ajes = [t for t in transactions if t["entry_type"] == "Adjusting"]
    cash = get_cash_activity(client_id, period_start, period_end)

    wb = openpyxl.Workbook()

    # ---- Summary
    ws = wb.active
    ws.title = "Summary"
    total_dr = round(sum(r.adjusted_dr for r in tb_rows), 2)
    total_cr = round(sum(r.adjusted_cr for r in tb_rows), 2)
    branding = get_branding()
    lines = [
        (client_name, ""),
        ("Close package", f"{period_start.isoformat()} to {period_end.isoformat()}"),
        ("Generated", datetime.now().strftime("%Y-%m-%d %H:%M")),
    ]
    if branding.firm_name:
        lines.append(("Prepared by", branding.firm_name))
    lines += [
        ("", ""),
        ("Final trial balance — total debits", total_dr),
        ("Final trial balance — total credits", total_cr),
        ("In balance", "YES" if abs(total_dr - total_cr) < 0.01 else "OUT OF BALANCE"),
        ("Journal lines in period", len(transactions)),
        ("Adjusting entry lines", len(ajes)),
        ("", ""),
        ("Cash accounts", "Receipts / Disbursements"),
    ]
    for row in cash:
        lines.append((
            f"{row.account_number} - {row.account_name}",
            f"{row.receipts:,.2f} / {row.disbursements:,.2f}",
        ))
    for left, right in lines:
        ws.append([left, right])
    ws["A1"].font = _HEADER_FONT
    ws.column_dimensions["A"].width = 38
    ws.column_dimensions["B"].width = 28

    # ---- Trial Balance (final, with the worksheet's supporting columns)
    ws = wb.create_sheet("Trial Balance")
    _write_table(
        ws,
        ["Acct #", "Account Name", "Type", "Beg Dr", "Beg Cr",
         "Activity Dr", "Activity Cr", "AJE Dr", "AJE Cr",
         "Final Dr", "Final Cr"],
        [
            [r.account_number, r.account_name, r.account_type,
             r.beginning_dr or None, r.beginning_cr or None,
             r.period_debits or None, r.period_credits or None,
             r.aje_debits or None, r.aje_credits or None,
             r.adjusted_dr or None, r.adjusted_cr or None]
            for r in tb_rows
        ],
        money_cols=range(4, 12),
    )
    totals = ws.max_row + 1
    ws.cell(row=totals, column=1, value="TOTALS").font = _HEADER_FONT
    # Computed values, not =SUM() formulas: openpyxl writes no cached results,
    # so a formula cell reads as literal "=SUM(...)" text to anything that
    # opens the workbook without a calc engine (LedgerPDF renders exactly what
    # the file says) until a human opens and re-saves it in Excel.
    _tb_totals = [
        round(sum(r.beginning_dr for r in tb_rows), 2),
        round(sum(r.beginning_cr for r in tb_rows), 2),
        round(sum(r.period_debits for r in tb_rows), 2),
        round(sum(r.period_credits for r in tb_rows), 2),
        round(sum(r.aje_debits for r in tb_rows), 2),
        round(sum(r.aje_credits for r in tb_rows), 2),
        round(sum(r.adjusted_dr for r in tb_rows), 2),
        round(sum(r.adjusted_cr for r in tb_rows), 2),
    ]
    for col_idx, value in zip(range(4, 12), _tb_totals):
        cell = ws.cell(row=totals, column=col_idx, value=value)
        cell.font = _HEADER_FONT
        cell.number_format = _MONEY_FMT

    # ---- Transactions
    ws = wb.create_sheet("Transactions")
    _write_table(
        ws,
        ["Date", "Entry #", "Type", "Description", "Acct #", "Account",
         "Debit", "Credit", "Memo", "Source Ref"],
        [
            [t["entry_date"], t["entry_id"], t["entry_type"], t["description"],
             t["account_number"], t["account_name"],
             t["debit"] or None, t["credit"] or None, t["memo"],
             t["source_reference"]]
            for t in transactions
        ],
        money_cols=(7, 8),
    )
    ws.column_dimensions["D"].width = 40
    ws.column_dimensions["F"].width = 30

    # ---- Adjusting Entries
    ws = wb.create_sheet("Adjusting Entries")
    _write_table(
        ws,
        ["Date", "Entry #", "AJE Ref", "Description", "Acct #", "Account",
         "Debit", "Credit", "Memo"],
        [
            [t["entry_date"], t["entry_id"], t["source_reference"],
             t["description"], t["account_number"], t["account_name"],
             t["debit"] or None, t["credit"] or None, t["memo"]]
            for t in ajes
        ],
        money_cols=(7, 8),
    )
    ws.column_dimensions["D"].width = 40
    ws.column_dimensions["F"].width = 30

    # ---- Receipts & Disbursements
    ws = wb.create_sheet("Receipts & Disbursements")
    _write_table(
        ws,
        ["Acct #", "Cash Account", "Beginning", "Total Receipts",
         "Total Disbursements", "Ending"],
        [
            [row.account_number, row.account_name, row.beginning,
             row.receipts, row.disbursements, row.ending]
            for row in cash
        ],
        money_cols=range(3, 7),
    )
    if cash:
        totals = ws.max_row + 1
        ws.cell(row=totals, column=2, value="TOTALS").font = _HEADER_FONT
        _cash_totals = [
            round(sum(r.beginning for r in cash), 2),
            round(sum(r.receipts for r in cash), 2),
            round(sum(r.disbursements for r in cash), 2),
            round(sum(r.ending for r in cash), 2),
        ]
        for col_idx, value in zip(range(3, 7), _cash_totals):
            cell = ws.cell(row=totals, column=col_idx, value=value)
            cell.font = _HEADER_FONT
            cell.number_format = _MONEY_FMT

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output


# --------------------------------------------------------------------- PDF

_PDF_BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=8, leading=10)
_PDF_H1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=16, leading=20,
                         spaceAfter=6)
_PDF_H2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12, leading=15,
                         spaceBefore=6, spaceAfter=8)
_PDF_META = ParagraphStyle("meta", fontName="Helvetica", fontSize=10, leading=14,
                           textColor=colors.HexColor("#444444"))


def _money(value: float) -> str:
    return f"{value:,.2f}" if value else ""


def _wrap(text: str) -> Paragraph:
    return Paragraph((text or "").replace("&", "&amp;").replace("<", "&lt;"), _PDF_BODY)


def _pdf_table(headers, data_rows, col_widths, money_from: int,
               totals_row=None) -> Table:
    """A report table: bold repeating header, right-aligned money columns."""
    rows = [headers] + data_rows
    if totals_row is not None:
        rows.append(totals_row)
    table = Table(rows, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.black),
        ("ALIGN", (money_from, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#F4F4F0")]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if totals_row is not None:
        style += [
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("LINEABOVE", (0, -1), (-1, -1), 0.75, colors.black),
        ]
    table.setStyle(TableStyle(style))
    return table


def _logo_flowable(branding: FirmBranding, max_height: float):
    """The firm logo scaled to the masthead, or None."""
    if not branding.logo:
        return None
    try:
        reader = ImageReader(BytesIO(branding.logo))
        width, height = reader.getSize()
        scale = max_height / float(height)
        return Image(BytesIO(branding.logo),
                     width=width * scale, height=max_height)
    except Exception:
        return None  # a corrupt logo must never block the close package


def build_close_package_pdf(
    client_id: int,
    client_name: str,
    period_start: date,
    period_end: date,
    tb_rows: List[TrialBalanceWorksheetRow],
) -> BytesIO:
    """One presentable PDF: Summary, TB, Transactions, AJEs, R&D."""
    transactions = get_period_transactions(client_id, period_start, period_end)
    ajes = [t for t in transactions if t["entry_type"] == "Adjusting"]
    cash = get_cash_activity(client_id, period_start, period_end)
    period_label = f"{long_date(period_start)} to {long_date(period_end)}"

    branding = get_branding()
    accent = colors.HexColor(branding.accent_hex) if branding.accent_hex else colors.black
    heading_1 = ParagraphStyle("bh1", parent=_PDF_H1, textColor=accent)
    heading_2 = ParagraphStyle("bh2", parent=_PDF_H2, textColor=accent)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(letter),
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.6 * inch, bottomMargin=0.55 * inch,
        title=f"Close Package — {client_name}",
        author=branding.firm_name or "ProBooks",
    )

    footer_left = f"{client_name} — {period_label}"
    if branding.firm_name:
        footer_left = f"{branding.firm_name} · {footer_left}"

    def _footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawString(0.5 * inch, 0.3 * inch, footer_left)
        canvas.drawRightString(
            doc.pagesize[0] - 0.5 * inch, 0.3 * inch, f"Page {canvas.getPageNumber()}"
        )
        canvas.restoreState()

    total_dr = round(sum(r.adjusted_dr for r in tb_rows), 2)
    total_cr = round(sum(r.adjusted_cr for r in tb_rows), 2)
    balanced = abs(total_dr - total_cr) < 0.01

    masthead = []
    logo = _logo_flowable(branding, max_height=0.45 * inch)
    if logo:
        logo.hAlign = "LEFT"
        masthead += [logo, Spacer(1, 6)]
    if branding.firm_name:
        firm_line = branding.firm_name + (f" · {branding.tagline}" if branding.tagline else "")
        masthead.append(Paragraph(firm_line, _PDF_META))
        masthead.append(Spacer(1, 10))

    story = masthead + [
        Paragraph(client_name, heading_1),
        Paragraph("Close Package", _PDF_META),
        Paragraph(period_label, _PDF_META),
        Paragraph(f"Generated {long_datetime(datetime.now())}", _PDF_META),
        Spacer(1, 18),
        Paragraph("Summary", heading_2),
        _pdf_table(
            ["", ""],
            [
                ["Final trial balance — total debits", _money(total_dr)],
                ["Final trial balance — total credits", _money(total_cr)],
                ["In balance", "Yes" if balanced else "OUT OF BALANCE"],
                ["Journal lines in period", str(len(transactions))],
                ["Adjusting entry lines", str(len(ajes))],
            ],
            [3.4 * inch, 1.6 * inch], money_from=1,
        ),
        Spacer(1, 14),
        Paragraph("Cash Activity", heading_2),
        _pdf_table(
            ["Acct #", "Cash Account", "Beginning", "Total Receipts",
             "Total Disbursements", "Ending"],
            [[r.account_number, _wrap(r.account_name), _money(r.beginning),
              f"{r.receipts:,.2f}", f"{r.disbursements:,.2f}", _money(r.ending)]
             for r in cash] or [["", "No cash accounts", "", "", "", ""]],
            [0.6 * inch, 2.6 * inch, 1.1 * inch, 1.2 * inch, 1.5 * inch, 1.1 * inch],
            money_from=2,
            totals_row=(
                ["", "TOTALS",
                 _money(round(sum(r.beginning for r in cash), 2)),
                 f"{sum(r.receipts for r in cash):,.2f}",
                 f"{sum(r.disbursements for r in cash):,.2f}",
                 _money(round(sum(r.ending for r in cash), 2))]
                if cash else None
            ),
        ),
        PageBreak(),
    ]

    # ---- Final Trial Balance
    story.append(Paragraph("Final Trial Balance", heading_2))
    money_w = 0.72 * inch
    story.append(_pdf_table(
        ["Acct #", "Account Name", "Beg Dr", "Beg Cr", "Activity Dr",
         "Activity Cr", "AJE Dr", "AJE Cr", "Final Dr", "Final Cr"],
        [[r.account_number, _wrap(r.account_name),
          _money(r.beginning_dr), _money(r.beginning_cr),
          _money(r.period_debits), _money(r.period_credits),
          _money(r.aje_debits), _money(r.aje_credits),
          _money(r.adjusted_dr), _money(r.adjusted_cr)]
         for r in tb_rows],
        [0.6 * inch, 2.9 * inch] + [money_w] * 8, money_from=2,
        totals_row=["", "TOTALS",
                    _money(round(sum(r.beginning_dr for r in tb_rows), 2)),
                    _money(round(sum(r.beginning_cr for r in tb_rows), 2)),
                    _money(round(sum(r.period_debits for r in tb_rows), 2)),
                    _money(round(sum(r.period_credits for r in tb_rows), 2)),
                    _money(round(sum(r.aje_debits for r in tb_rows), 2)),
                    _money(round(sum(r.aje_credits for r in tb_rows), 2)),
                    _money(total_dr), _money(total_cr)],
    ))
    story.append(PageBreak())

    # ---- Transactions
    story.append(Paragraph("Transactions", heading_2))
    if transactions:
        story.append(_pdf_table(
            ["Date", "Entry #", "Type", "Description", "Acct #", "Account",
             "Debit", "Credit", "Memo"],
            [[t["entry_date"], str(t["entry_id"]), _wrap(t["entry_type"]),
              _wrap(t["description"]), t["account_number"],
              _wrap(t["account_name"]), _money(t["debit"]),
              _money(t["credit"]), _wrap(t["memo"])]
             for t in transactions],
            [0.75 * inch, 0.55 * inch, 0.75 * inch, 2.5 * inch, 0.55 * inch,
             1.9 * inch, 0.8 * inch, 0.8 * inch, 1.4 * inch], money_from=6,
        ))
    else:
        story.append(Paragraph("No transactions in this period.", _PDF_BODY))
    story.append(PageBreak())

    # ---- Adjusting Entries
    story.append(Paragraph("Adjusting Journal Entries", heading_2))
    if ajes:
        story.append(_pdf_table(
            ["Date", "Entry #", "AJE Ref", "Description", "Acct #", "Account",
             "Debit", "Credit", "Memo"],
            [[t["entry_date"], str(t["entry_id"]), _wrap(t["source_reference"]),
              _wrap(t["description"]), t["account_number"],
              _wrap(t["account_name"]), _money(t["debit"]),
              _money(t["credit"]), _wrap(t["memo"])]
             for t in ajes],
            [0.75 * inch, 0.55 * inch, 0.75 * inch, 2.5 * inch, 0.55 * inch,
             1.9 * inch, 0.8 * inch, 0.8 * inch, 1.4 * inch], money_from=6,
        ))
    else:
        story.append(Paragraph("No adjusting entries in this period.", _PDF_BODY))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    buffer.seek(0)
    return buffer
