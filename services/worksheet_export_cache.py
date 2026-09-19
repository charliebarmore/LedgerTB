"""One bounded export bundle per UI session, keyed by actual book/report inputs.

No global Streamlit cache: financial files never cross sessions or books. Read
fresh report inputs on every rerun; only expensive PDF/XLSX rendering is reused.
"""

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path

from database import connection as dbconn
from models.client import Client
from models.reports import ReportGenerator
from services.close_package import (
    consistent_export_window,
    load_close_package_snapshot,
    build_close_package,
    build_close_package_pdf,
)
from services.preferences import get_date_format
from services.worksheet_export import build_worksheet_excel


def _encode(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest()}
    raise TypeError(f"Unsupported report fingerprint value: {type(value).__name__}")


def worksheet_exports(state, client_id, start, end, *, show_all=False):
    with consistent_export_window():
        client = Client.get_by_id(client_id)
        with dbconn.get_cursor() as cur:
            book_id = cur.execute(
                "SELECT book_id FROM book_identity WHERE id=1"
            ).fetchone()[0]
        rows, _ = ReportGenerator.trial_balance_worksheet(
            client_id, start, end, show_all_accounts=show_all
        )
        close_rows, _ = ReportGenerator.trial_balance_worksheet(client_id, start, end)
        snapshot = load_close_package_snapshot(client_id, start, end)
        inputs = asdict(snapshot)
        # Timestamp describes when the bytes were built, not a changed ledger.
        inputs.pop("generated_at")
        key = hashlib.sha256(
            json.dumps(
                {
                    "schema": 1,
                    "book": str(Path(dbconn.DATABASE_PATH).resolve()),
                    "book_id": book_id,
                    "client_id": client_id,
                    "client_name": client.name,
                    "fye": client.fiscal_year_end_month,
                    "start": start,
                    "end": end,
                    "show_all": show_all,
                    "date_format": get_date_format(),
                    "worksheet_rows": rows,
                    "close_rows": close_rows,
                    "snapshot": inputs,
                },
                sort_keys=True,
                default=_encode,
                allow_nan=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        cached = state.get("_worksheet_exports")
        if cached and cached["key"] == key:
            return cached
        # Drop a stale bundle before rendering. A failed build cannot serve
        # bytes describing a previous ledger or period.
        state.pop("_worksheet_exports", None)
        result = dict(
            key=key,
            close_rows=close_rows,
            worksheet=build_worksheet_excel(
                client.name, start, end, rows, snapshot.comparative_trial_balance
            ),
            pdf=build_close_package_pdf(
                client_id, client.name, start, end, close_rows, snapshot=snapshot
            ).getvalue(),
            package=build_close_package(
                client_id, client.name, start, end, close_rows, snapshot=snapshot
            ).getvalue(),
        )
        state["_worksheet_exports"] = result
        return result
