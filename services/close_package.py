"""One-workbook close package: everything a reviewer needs to tie out a period.

Sheets: Summary, Trial Balance (the full worksheet columns), Transactions
(every journal line in the period), Adjusting Entries, and Receipts &
Disbursements per cash account. Built for the end of a close — the export
that hands the finished period to a tax file, a reviewer, or another system.
"""
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from typing import List

import openpyxl
from openpyxl.styles import Font

from database.connection import get_cursor
from models.reports import TrialBalanceWorksheetRow
from money import to_dollars

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
    lines = [
        (client_name, ""),
        ("Close package", f"{period_start.isoformat()} to {period_end.isoformat()}"),
        ("Generated", datetime.now().strftime("%Y-%m-%d %H:%M")),
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
    for col_idx in range(4, 12):
        letter = openpyxl.utils.get_column_letter(col_idx)
        cell = ws.cell(row=totals, column=col_idx, value=f"=SUM({letter}2:{letter}{totals-1})")
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
        for col_idx in range(3, 7):
            letter = openpyxl.utils.get_column_letter(col_idx)
            cell = ws.cell(row=totals, column=col_idx, value=f"=SUM({letter}2:{letter}{totals-1})")
            cell.font = _HEADER_FONT
            cell.number_format = _MONEY_FMT

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output
