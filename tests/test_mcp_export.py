"""The file-export seam to workpaper tools (LedgerPDF pairing).

Contract: exports only land inside user-approved roots, work at read level
(with the action audit-logged), and the Excel carries computed totals — not
uncalculated =SUM() formulas that render as literal text in any tool without
a calc engine.
"""
from datetime import date
from io import BytesIO

import openpyxl
import pytest

from database import connection as dbconn
from models.audit_log import AuditLog
from services import mcp_tools
from tests.conftest import post_entry


def _seed(client_id, accounts):
    post_entry(client_id, date(2026, 1, 15),
               [(accounts["cash"], 500, 0), (accounts["revenue"], 0, 500)])
    post_entry(client_id, date(2026, 2, 3),
               [(accounts["expense"], 120, 0), (accounts["cash"], 0, 120)])


def test_export_refused_without_roots(client_id, accounts, tmp_path, monkeypatch):
    _seed(client_id, accounts)
    monkeypatch.delenv("PROBOOKS_MCP_EXPORT_ROOTS", raising=False)
    with pytest.raises(ValueError, match="export is off"):
        mcp_tools.export_close_package(client_id, "2026-01-01", "2026-03-31",
                                       str(tmp_path))


def test_export_refused_outside_roots(client_id, accounts, tmp_path, monkeypatch):
    _seed(client_id, accounts)
    approved = tmp_path / "approved"
    approved.mkdir()
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setenv("PROBOOKS_MCP_EXPORT_ROOTS", str(approved))
    with pytest.raises(ValueError, match="outside"):
        mcp_tools.export_close_package(client_id, "2026-01-01", "2026-03-31",
                                       str(elsewhere))
    with pytest.raises(ValueError, match="outside"):
        mcp_tools.export_close_package(client_id, "2026-01-01", "2026-03-31",
                                       str(approved / ".." / "elsewhere"))


def test_export_writes_both_files_at_read_level(client_id, accounts, tmp_path,
                                                monkeypatch):
    _seed(client_id, accounts)
    monkeypatch.setenv("PROBOOKS_MCP_EXPORT_ROOTS", str(tmp_path))
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", "read")

    result = mcp_tools.export_close_package(
        client_id, "2026-01-01", "2026-03-31", str(tmp_path / "binder-src"))

    pdf = open(result["pdf"], "rb").read()
    assert pdf.startswith(b"%PDF")

    wb = openpyxl.load_workbook(BytesIO(open(result["xlsx"], "rb").read()))
    assert set(wb.sheetnames) >= {"Summary", "Trial Balance", "Transactions"}

    # Gap-2 regression: the TOTALS row must be numbers, never formula text —
    # openpyxl caches no results, so "=SUM(...)" reads as a literal string in
    # any tool without a calc engine.
    tb = wb["Trial Balance"]
    totals_row = tb.max_row
    assert tb.cell(row=totals_row, column=1).value == "TOTALS"
    for col in range(4, 12):
        value = tb.cell(row=totals_row, column=col).value
        assert not (isinstance(value, str) and value.startswith("=")), \
            f"col {col} is an uncalculated formula"
        assert isinstance(value, (int, float))
    assert tb.cell(row=totals_row, column=10).value == pytest.approx(500.0)

    # The export is audit-logged even at read level.
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", None)
    counts = AuditLog.get_filtered_counts(client_id)
    assert counts["total"] > 0
