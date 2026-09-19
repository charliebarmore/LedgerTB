from datetime import date
from io import BytesIO
import openpyxl
import pytest
from database import connection as dbconn
from models.fiscal_period import FiscalPeriod
from models.client import Client
from services import worksheet_export_cache as cache
from services.branding import save_branding
from tests.conftest import post_entry, page_path
from tests.test_accounting_pages import _select_client
from streamlit.testing.v1 import AppTest


def test_actual_inputs_invalidate_export_bytes(client_id, accounts, monkeypatch):
    start, end = date(2026, 1, 1), date(2026, 12, 31)
    post_entry(
        client_id, start, [(accounts["cash"], 100, 0), (accounts["revenue"], 0, 100)]
    )
    state = {}
    calls = []
    for name in (
        "build_worksheet_excel",
        "build_close_package_pdf",
        "build_close_package",
    ):
        original = getattr(cache, name)

        def spy(*a, _name=name, _original=original, **k):
            calls.append(_name)
            return _original(*a, **k)

        monkeypatch.setattr(cache, name, spy)
    import time

    began = time.perf_counter()
    first = cache.worksheet_exports(state, client_id, start, end)
    first_seconds = time.perf_counter() - began
    began = time.perf_counter()
    assert len(calls) == 3
    assert (
        cache.worksheet_exports(state, client_id, start, end) is first
        and len(calls) == 3
    )
    reuse_seconds = time.perf_counter() - began
    print(
        f"Fictional two-account export: build={first_seconds:.3f}s reuse={reuse_seconds:.3f}s; 3 builders initially, 0 on unchanged rerun"
    )
    workbook = openpyxl.load_workbook(BytesIO(first["worksheet"]))
    assert workbook.active["H6"].data_type == "f" and first["pdf"].startswith(b"%PDF")
    post_entry(
        client_id,
        start,
        [(accounts["expense"], 33.33, 0), (accounts["cash"], 0, 33.33)],
    )
    second = cache.worksheet_exports(state, client_id, start, end)
    assert second["key"] != first["key"] and len(calls) == 6
    save_branding(firm_name="Fictional New Firm", tagline="", accent_hex="#123456")
    branded = cache.worksheet_exports(state, client_id, start, end)
    assert branded["key"] != second["key"] and len(calls) == 9
    visible = cache.worksheet_exports(state, client_id, start, end, show_all=True)
    assert visible["key"] != branded["key"] and len(calls) == 12
    different_period = cache.worksheet_exports(
        state, client_id, start, date(2026, 6, 30)
    )
    assert different_period["key"] != visible["key"] and len(calls) == 15
    # Equal financial data in a different book identity must not share files.
    with dbconn.get_cursor(commit=True) as cur:
        cur.execute("UPDATE book_identity SET book_id=?", ("f" * 32,))
    other_book = cache.worksheet_exports(state, client_id, start, date(2026, 6, 30))
    assert other_book["key"] != different_period["key"] and len(calls) == 18
    client = Client.get_by_id(client_id)
    client.name = "=Fictional Client"
    client.save()
    renamed = cache.worksheet_exports(state, client_id, start, end)
    assert renamed["key"] != other_book["key"]
    assert (
        openpyxl.load_workbook(BytesIO(renamed["worksheet"])).active["A1"].data_type
        == "s"
    )

    def fail(*a, **k):
        raise OSError("failed build")

    monkeypatch.setattr(cache, "build_close_package_pdf", fail)
    with pytest.raises(OSError):
        cache.worksheet_exports(state, client_id, start, date(2026, 9, 30))
    assert "_worksheet_exports" not in state


def test_readonly_worksheet_rerun_reuses_exports_and_writes_nothing(
    client_id, accounts, monkeypatch
):
    FiscalPeriod.ensure_periods_exist(client_id, 2026, 12)
    post_entry(
        client_id,
        date(2026, 1, 1),
        [(accounts["cash"], 10, 0), (accounts["revenue"], 0, 10)],
    )
    _select_client(monkeypatch, client_id)
    monkeypatch.setattr(dbconn, "READ_ONLY", True)
    with dbconn.get_cursor() as cur:
        before = cur.execute("SELECT count(*) FROM audit_log").fetchone()[0]
    calls = []
    original = cache.build_worksheet_excel

    def spy(*a, **k):
        calls.append(1)
        return original(*a, **k)

    monkeypatch.setattr(cache, "build_worksheet_excel", spy)
    at = AppTest.from_file(
        page_path("pages/1_Trial_Balance_Worksheet.py"), default_timeout=60
    ).run()
    assert not at.exception
    assert len(calls) == 1
    assert next(b for b in at.button if b.label == "+ Add AJE").disabled
    at.run()
    assert not at.exception and len(calls) == 1
    with dbconn.get_cursor() as cur:
        assert cur.execute("SELECT count(*) FROM audit_log").fetchone()[0] == before


def test_readonly_empty_calendar_does_not_attempt_repairs(client_id, monkeypatch):
    _select_client(monkeypatch, client_id)
    monkeypatch.setattr(dbconn, "READ_ONLY", True)
    at = AppTest.from_file(
        page_path("pages/1_Trial_Balance_Worksheet.py"), default_timeout=60
    ).run()
    assert not at.exception
    assert any("No fiscal year" in m.value for m in at.info)
