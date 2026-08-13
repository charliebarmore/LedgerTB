"""MCP assistant access: read-only views that tie to the books, and the
read-only pin + vault unlock that gate them."""
from datetime import date

import pytest

from database import connection as dbconn
from database.connection import get_cursor
from models.transaction import ImportedTransaction
from models.fiscal_period import FiscalPeriod
from services import mcp_tools
from tests.conftest import post_entry


def _seed(client_id, accounts):
    post_entry(client_id, date(2026, 1, 15),
               [(accounts["cash"], 500, 0), (accounts["revenue"], 0, 500)])
    post_entry(client_id, date(2026, 2, 3),
               [(accounts["expense"], 120, 0), (accounts["cash"], 0, 120)])


def test_clients_accounts_and_trial_balance_tie(client_id, accounts):
    _seed(client_id, accounts)

    clients = mcp_tools.list_clients()
    assert any(c["client_id"] == client_id for c in clients)

    chart = mcp_tools.list_accounts(client_id)
    assert any(a["type"] == "Revenue" for a in chart)

    tb = mcp_tools.trial_balance(client_id)
    assert tb["balanced"] is True
    assert tb["total_debits"] == tb["total_credits"] > 0
    cash_row = next(r for r in tb["accounts"] if "Cash" in r["name"])
    assert cash_row["debit"] == 380.0  # 500 in - 120 out


def test_statements_and_ledger(client_id, accounts):
    _seed(client_id, accounts)

    inc = mcp_tools.income_statement(client_id, "2026-01-01", "2026-12-31")
    assert inc["total_revenue"] == 500.0
    assert inc["total_expenses"] == 120.0
    assert inc["net_income"] == 380.0

    bs = mcp_tools.balance_sheet(client_id, "2026-12-31")
    assert bs["balanced"] is True
    assert bs["total_assets"] == 380.0

    from models.account import Account
    cash_number = Account.get_by_id(accounts["cash"], client_id=client_id).account_number
    gl = mcp_tools.general_ledger(client_id, cash_number)
    assert gl["ending_balance"] == 380.0
    assert len([e for e in gl["entries"] if e["entry_id"]]) == 2


def test_statement_tools_optionally_return_prior_year_comparisons(
    client_id, accounts
):
    post_entry(client_id, date(2025, 1, 15),
               [(accounts["cash"], 200, 0),
                (accounts["revenue"], 0, 200)])
    _seed(client_id, accounts)

    income = mcp_tools.income_statement(
        client_id, "2026-01-01", "2026-12-31",
        compare_to_prior_year=True,
    )
    assert income["total_revenue"] == 500
    assert income["comparison"]["prior_period"] == {
        "start": "2025-01-01", "end": "2025-12-31"
    }
    assert income["comparison"]["total_revenue"]["prior"] == 200
    assert income["comparison"]["total_revenue"]["change"] == 300

    balance = mcp_tools.balance_sheet(
        client_id, "2026-12-31", compare_to_prior_year=True
    )
    assert balance["comparison"]["prior_as_of"] == "2025-12-31"
    assert balance["comparison"]["total_assets"]["prior"] == 200
    assert balance["comparison"]["prior_balanced"] is True

    trial = mcp_tools.trial_balance(
        client_id, "2026-12-31", compare_to_prior_year=True
    )
    assert trial["comparison"]["prior_as_of"] == "2025-12-31"
    prior_cash = next(
        row for row in trial["comparison"]["accounts"]
        if row["account_number"] == "1000"
    )
    assert prior_cash["prior_debit"] == 200

    # Existing clients get the original compact response unless they opt in.
    assert "comparison" not in mcp_tools.income_statement(
        client_id, "2026-01-01", "2026-12-31"
    )


def test_find_entries_and_detail(client_id, accounts):
    _seed(client_id, accounts)

    found = mcp_tools.find_entries(client_id, search="Test entry")
    assert found, "seeded entries should be findable"
    detail = mcp_tools.entry_detail(client_id, found[0]["entry_id"])
    assert len(detail["lines"]) == 2
    assert sum(l["debit"] for l in detail["lines"]) == sum(
        l["credit"] for l in detail["lines"])

    with pytest.raises(ValueError, match="No journal entry"):
        mcp_tools.entry_detail(client_id, 99999)
    with pytest.raises(ValueError, match="No client"):
        mcp_tools.trial_balance(999999)


def test_integrity_sweep_reports_clean_books_explicitly(client_id, accounts):
    _seed(client_id, accounts)
    result = mcp_tools.integrity_sweep(client_id, "2026-01-01", "2026-12-31")

    assert result == {
        "period": {"start": "2026-01-01", "end": "2026-12-31"},
        "checks_run": mcp_tools.INTEGRITY_CHECKS,
        "clean": True,
        "findings": [],
    }


def test_close_map_tools_read_and_propose_without_signing(client_id, accounts):
    FiscalPeriod(
        client_id=client_id, period_name="FY 2026", period_type="Year",
        start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
    ).save()
    _seed(client_id, accounts)

    readiness = mcp_tools.close_readiness(client_id, 2026)
    assert readiness["ready"] is False
    cash = next(row for row in readiness["accounts"] if row["account_number"] == "1000")
    assert cash["status"] == "Not started"

    proposal = mcp_tools.propose_close_explanation(
        client_id, 2026, cash["account_id"],
        "Cash agrees to the completed reconciliation.",
        "Compared ledger and statement ending balances.",
    )
    assert proposal["status"] == "pending"
    detail = mcp_tools.account_close_detail(client_id, 2026, cash["account_id"])
    assert detail["explanation"] == ""
    assert detail["pending_explanation_proposals"][0]["proposal_id"] == proposal["proposal_id"]


def test_close_detail_exposes_prior_year_context_without_counting_it_as_current(
    client_id, accounts
):
    from models import close_map

    prior = FiscalPeriod(
        client_id=client_id, period_name="FY 2025", period_type="Year",
        start_date=date(2025, 1, 1), end_date=date(2025, 12, 31),
    )
    prior.save()
    FiscalPeriod(
        client_id=client_id, period_name="FY 2026", period_type="Year",
        start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
    ).save()
    post_entry(client_id, date(2025, 6, 1), [
        (accounts["cash"], 200, 0), (accounts["revenue"], 0, 200),
    ])
    close_map.save_explanation(
        client_id, prior.id, accounts["cash"], "Cash agreed to 2025 support."
    )
    close_map.add_evidence(
        client_id, prior.id, accounts["cash"], "workpaper", "A-1",
        "2025 reconciliation",
    )
    close_map.signoff(client_id, prior.id, accounts["cash"], "preparer")
    close_map.signoff(client_id, prior.id, accounts["cash"], "reviewer")
    post_entry(client_id, date(2026, 1, 2), [
        (accounts["cash"], 25, 0), (accounts["revenue"], 0, 25),
    ])

    detail = mcp_tools.account_close_detail(client_id, 2026, accounts["cash"])
    assert detail["explanation"] == ""
    assert detail["evidence"] == []
    assert detail["prepared_by"] is None
    assert detail["reviewed_by"] is None
    assert detail["prior_year_context"]["fiscal_year"] == 2025
    assert detail["prior_year_context"]["period_name"] == "FY 2025"
    assert detail["prior_year_context"]["evidence"][0]["reference"] == "A-1"
    assert detail["prior_year_context"]["reviewed_by"]
    assert "fresh support" in detail["prior_year_context"]["current_year_requirement"]


def test_integrity_sweep_treats_prior_year_entries_as_history(client_id, accounts):
    post_entry(client_id, date(2025, 6, 15), [
        (accounts["cash"], 100, 0),
        (accounts["revenue"], 0, 100),
    ])
    post_entry(client_id, date(2026, 1, 15), [
        (accounts["cash"], 50, 0),
        (accounts["revenue"], 0, 50),
    ])

    result = mcp_tools.integrity_sweep(client_id, "2026-01-01", "2026-12-31")

    assert result["clean"] is True
    assert result["findings"] == []
    assert "pre_period_entries" not in result["checks_run"]


def test_integrity_sweep_reports_findings_in_same_envelope(client_id, accounts):
    _seed(client_id, accounts)
    ImportedTransaction(
        client_id=client_id,
        import_batch="unreviewed.csv",
        transaction_date=date(2026, 3, 1),
        description="Pending transaction",
        amount=-25,
        bank_account_id=accounts["cash"],
        status="Pending",
    ).save()

    result = mcp_tools.integrity_sweep(client_id, "2026-01-01", "2026-12-31")

    assert result["clean"] is False
    assert result["checks_run"] == mcp_tools.INTEGRITY_CHECKS
    assert result["period"] == {"start": "2026-01-01", "end": "2026-12-31"}
    assert result["findings"]
    for f in result["findings"]:
        assert {"severity", "check", "title", "detail"} <= set(f)


def test_read_only_pin_blocks_writes_but_not_reads(client_id, accounts, monkeypatch):
    _seed(client_id, accounts)
    monkeypatch.setattr(dbconn, "READ_ONLY", True)

    # Reads still work…
    assert mcp_tools.trial_balance(client_id)["balanced"] is True
    # …writes die at the database, regardless of code path.
    with pytest.raises(Exception, match="query_only|readonly|read-only"):
        with get_cursor(commit=True) as cursor:
            cursor.execute("UPDATE clients SET name = name WHERE id = ?",
                           (client_id,))


def test_vault_unlock_round_trip(client_id, monkeypatch):
    import mcp_server
    from utils import books
    from utils.secure_store import set_secret
    from utils.assistant_access import credential_names
    from services.backups import active_book_id

    monkeypatch.setattr(dbconn, "READ_ONLY", dbconn.READ_ONLY)  # restore later
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", dbconn.ASSISTANT_ACCESS_LEVEL)
    # Firm mode resolves the active book from the user registry; pin it to the
    # test database.
    test_db = dbconn.DATABASE_PATH
    monkeypatch.setattr(books, "active_book", lambda: test_db)
    monkeypatch.setattr(books, "is_local_book", lambda path: True)
    session_key = dbconn.get_active_key()
    assert session_key, "db fixture should have keyed the session"
    book_id = active_book_id()

    # Not enabled -> refuses (fake vault starts empty each test).
    assert mcp_server._unlock_from_vault() is False

    names = credential_names(test_db)
    set_secret(names.book_id, book_id)
    set_secret(names.level, "propose")
    set_secret(names.key, session_key)
    dbconn.clear_active_key()
    assert mcp_server._unlock_from_vault() is True
    assert dbconn.has_active_key()
    assert dbconn.ASSISTANT_ACCESS_LEVEL == "propose"
    assert mcp_tools.list_clients() is not None
