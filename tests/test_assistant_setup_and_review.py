"""Assistant setup tools and the human review checkpoint.

Setup: an assistant at 'propose' may scaffold a client and its chart —
and can never alter either afterwards. Review: assistant-attributed audit
rows queue for a person, sign-off is append-only and audit-logged, and the
sidebar count drains to zero.
"""
from datetime import date

import pytest

from database import connection as dbconn
from database.connection import get_cursor
from utils import maintenance_lock
from models import assistant_review
from models.account import Account
from models.fiscal_period import FiscalPeriod
from models.client import Client
from services import mcp_tools
from utils.fiscal_dates import fiscal_year_bounds


def _as_assistant(monkeypatch, level="propose"):
    from utils import actor as actor_mod

    monkeypatch.setattr(actor_mod, "_ASSISTANT", True)
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", level)


def test_create_client_seeds_chart_and_is_immutable_after(db, monkeypatch):
    _as_assistant(monkeypatch)
    result = mcp_tools.create_client("Northline Digital (Demo)",
                                     entity_type="LLC")
    assert result["accounts_seeded"] > 10  # default chart came with it
    assert result["fiscal_year"]
    assert any(
        period.period_name == f"FY {result['fiscal_year']}"
        for period in FiscalPeriod.get_all(
            result["client_id"], period_type="Year"
        )
    )
    chart = mcp_tools.list_accounts(result["client_id"])
    cash = next(account for account in chart if account["subtype"] == "Cash")
    revenue = next(account for account in chart if account["type"] == "Revenue")
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", "post")
    mcp_tools.post_entry(
        result["client_id"], result["fiscal_year_period"]["start"],
        "Opening activity",
        [{"account_number": cash["number"], "debit": 1},
         {"account_number": revenue["number"], "credit": 1}],
    )
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", "propose")

    readiness = mcp_tools.close_readiness(
        result["client_id"], result["fiscal_year"]
    )
    first_account = readiness["accounts"][0]
    proposal = mcp_tools.propose_close_explanation(
        result["client_id"], result["fiscal_year"],
        first_account["account_id"], "No activity; balance remains zero."
    )
    assert readiness["fiscal_year"] == result["fiscal_year"]
    detail = mcp_tools.account_close_detail(
        result["client_id"], result["fiscal_year"],
        first_account["account_id"],
    )
    assert proposal["status"] == "pending"
    assert detail["pending_explanation_proposals"]

    with pytest.raises(ValueError, match="already exists"):
        mcp_tools.create_client("Northline Digital (Demo)")
    # Setup is INSERT-only: the assistant cannot alter the client afterwards.
    with pytest.raises(Exception):
        with get_cursor(commit=True) as cursor:
            cursor.execute("UPDATE clients SET name = 'Renamed' WHERE id = ?",
                           (result["client_id"],))


def test_assistant_can_idempotently_add_a_fiscal_year(db, monkeypatch):
    _as_assistant(monkeypatch)
    result = mcp_tools.create_client("Close Map Setup", initial_fiscal_year=2026)

    created = mcp_tools.ensure_fiscal_year(result["client_id"], 2025)
    again = mcp_tools.ensure_fiscal_year(result["client_id"], 2025)

    assert created["fiscal_year"] == 2025
    assert created["created"] is True
    assert again["created"] is False
    assert created["periods_added"] == 17
    assert again["periods_added"] == 0
    assert created["period"]["start"] == "2025-01-01"
    assert created["period"]["end"] == "2025-12-31"


def test_create_client_validates_year_before_writing_and_uses_current_fy(
    db, monkeypatch
):
    _as_assistant(monkeypatch)
    with pytest.raises(ValueError, match="between 1900 and 9999"):
        mcp_tools.create_client("Must Not Survive", initial_fiscal_year=1800)
    assert not any(c.name == "Must Not Survive" for c in Client.get_all())

    result = mcp_tools.create_client(
        "June Year End", fiscal_year_end_month=6, seed_default_chart=False
    )
    expected = fiscal_year_bounds(date.today(), 6)[1].year
    assert result["fiscal_year"] == expected


def test_ensure_fiscal_year_repairs_a_partial_calendar(db, monkeypatch):
    client_id = Client(
        name="Partial Calendar", fiscal_year_end_month=12
    ).save(seed_accounts=False)
    FiscalPeriod(
        client_id=client_id,
        period_name="FY 2025 - Jan",
        period_type="Month",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
    ).save()

    _as_assistant(monkeypatch)
    result = mcp_tools.ensure_fiscal_year(client_id, 2025)

    matching = [
        period for period in FiscalPeriod.get_all(client_id)
        if period.period_name == "FY 2025"
        or period.period_name.startswith("FY 2025 - ")
    ]
    assert result["created"] is True
    assert result["periods_added"] == 16
    assert len(matching) == 17


def test_create_client_rolls_back_when_period_setup_fails(db, monkeypatch):
    _as_assistant(monkeypatch)

    def fail_period_setup(*_args, **_kwargs):
        raise RuntimeError("period setup failed")

    monkeypatch.setattr(FiscalPeriod, "ensure_periods_exist", fail_period_setup)
    with pytest.raises(RuntimeError, match="period setup failed"):
        mcp_tools.create_client("Atomic Setup")

    assert not any(c.name == "Atomic Setup" for c in Client.get_all())


def test_import_accounts_maps_qb_names_and_reports_everything(db, monkeypatch):
    _as_assistant(monkeypatch)
    cid = mcp_tools.create_client("QB Import Co", seed_default_chart=False)["client_id"]

    result = mcp_tools.import_accounts(cid, [
        {"number": "1000", "name": "Operating Checking", "type": "Bank"},
        {"number": "2100", "name": "Visa", "type": "Credit Card"},
        {"number": "1500", "name": "Accumulated Depreciation",
         "type": "Fixed Asset"},
        {"number": "1510", "name": "Accum Dep - Vehicles", "type": "Fixed Asset",
         "subtype": "Contra Asset"},
        {"number": "9999", "name": "Mystery", "type": "Suspense Widget"},
    ])
    assert result["created"] == 4
    assert result["errors"] and "Suspense Widget" in result["errors"][0]
    assert len(result["warnings"]) == 1
    assert "1500" in result["warnings"][0]
    assert "Accumulated Depreciation" in result["warnings"][0]

    by_no = {a.account_number: a for a in Account.get_all(cid, active_only=False)}
    assert by_no["1000"].type == "Asset" and by_no["1000"].subtype == "Cash"
    assert by_no["2100"].type == "Liability"
    assert by_no["1500"].subtype == "Accumulated Depreciation"
    assert by_no["1510"].subtype == "Accumulated Depreciation"

    again = mcp_tools.import_accounts(cid, [
        {"number": "1000", "name": "Operating Checking", "type": "Bank"}])
    assert again["created"] == 0 and again["skipped_existing"] == ["1000"]


def test_setup_tools_denied_at_read_level(db, monkeypatch):
    _as_assistant(monkeypatch, level="read")
    with pytest.raises(Exception):
        mcp_tools.create_client("Should Not Exist")


def test_review_queue_counts_ai_work_and_signoff_drains_it(
    client_id, accounts, monkeypatch
):
    cash = Account.get_by_id(accounts["cash"], client_id=client_id)

    assert assistant_review.unreviewed_count(client_id) == 0

    _as_assistant(monkeypatch)
    mcp_tools.propose_import(client_id, cash.account_number, [
        {"date": "2026-07-03", "description": "AI ROW", "amount": -5.00},
    ], "review test")

    from utils import actor as actor_mod
    monkeypatch.setattr(actor_mod, "_ASSISTANT", False)
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", None)

    n = assistant_review.unreviewed_count(client_id)
    assert n >= 1
    actions = assistant_review.unreviewed_actions(client_id)
    assert all(a.actor.endswith("(AI)") for a in actions)

    through = assistant_review.mark_reviewed(client_id, actions[-1].audit_id)
    assert through == max(a.audit_id for a in actions)
    assert assistant_review.unreviewed_count(client_id) == 0
    mark = assistant_review.latest_mark(client_id)
    assert mark and not mark["reviewed_by"].endswith("(AI)")

    # New assistant work after sign-off queues again.
    _as_assistant(monkeypatch)
    mcp_tools.propose_entry(client_id, "2026-07-31", "post-signoff draft", [
        {"account_number": cash.account_number, "debit": 1.00},
        {"account_number": Account.get_by_id(accounts["revenue"],
                                             client_id=client_id).account_number,
         "credit": 1.00},
    ])
    monkeypatch.setattr(actor_mod, "_ASSISTANT", False)
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", None)
    assert assistant_review.unreviewed_count(client_id) >= 1


def test_review_checkpoint_covers_only_the_displayed_page(client_id, monkeypatch):
    from models.audit_log import AuditLog
    from utils import actor as actor_mod

    _as_assistant(monkeypatch)
    # An assistant only ever writes inside a declared tool invocation, which is
    # what publishes its claim to the maintenance lock. Simulating its writes
    # without that would exercise a path production does not have.
    with maintenance_lock.writer(dbconn.DATABASE_PATH), get_cursor(commit=True) as cursor:
        for number in range(205):
            AuditLog.write(
                cursor=cursor,
                client_id=client_id,
                table_name="draft_entries",
                record_id=number + 1,
                action="INSERT",
                new_values={"sequence": number + 1},
            )
    monkeypatch.setattr(actor_mod, "_ASSISTANT", False)
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", None)

    displayed = assistant_review.unreviewed_actions(client_id)
    assert len(displayed) == 200
    assert assistant_review.unreviewed_count(client_id) == 205

    through = assistant_review.mark_reviewed(
        client_id, displayed[-1].audit_id
    )
    remaining = assistant_review.unreviewed_actions(client_id)
    assert through == displayed[-1].audit_id
    assert len(remaining) == 5
    assert all(action.audit_id > through for action in remaining)


def test_review_checkpoint_rejects_stale_or_foreign_target(
    client_id, monkeypatch
):
    from models.audit_log import AuditLog
    from models.client import Client
    from utils import actor as actor_mod

    _as_assistant(monkeypatch)
    with maintenance_lock.writer(dbconn.DATABASE_PATH):
        own_id = AuditLog.log_event(client_id, "EXPORT", "own_assistant_event")
        other_client = Client(name="Other Review Client")
        other_client.save(seed_accounts=False)
        foreign_id = AuditLog.log_event(
            other_client.id, "EXPORT", "foreign_assistant_event"
        )
    monkeypatch.setattr(actor_mod, "_ASSISTANT", False)
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", None)

    with pytest.raises(ValueError, match="no longer available"):
        assistant_review.mark_reviewed(client_id, foreign_id)

    assert assistant_review.mark_reviewed(client_id, own_id) == own_id
    with pytest.raises(ValueError, match="no longer available"):
        assistant_review.mark_reviewed(client_id, own_id)


def test_review_checkpoint_rolls_back_when_audit_write_fails(
    client_id, monkeypatch
):
    from models.audit_log import AuditLog
    from utils import actor as actor_mod

    _as_assistant(monkeypatch)
    with maintenance_lock.writer(dbconn.DATABASE_PATH):
        assistant_id = AuditLog.log_event(
            client_id, "EXPORT", "assistant_event_before_failed_review"
        )
    monkeypatch.setattr(actor_mod, "_ASSISTANT", False)
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", None)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(AuditLog, "write", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        assistant_review.mark_reviewed(client_id, assistant_id)

    assert assistant_review.latest_mark(client_id) is None
    assert assistant_review.unreviewed_count(client_id) == 1


def test_review_audit_events_actually_write(client_id):
    """Regression: the audit_log CHECK predated the REVIEW action, so every
    Book Review audit event silently failed at the database until 015."""
    from models.audit_log import AuditLog

    AuditLog.log_event(client_id, "REVIEW", "category_consistency_review",
                       {"probe": True})
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) AS n FROM audit_log WHERE client_id = ? "
            "AND action = 'REVIEW'", (client_id,))
        assert cursor.fetchone()["n"] >= 1


def test_import_accounts_assigns_numbers_when_absent(db, monkeypatch):
    _as_assistant(monkeypatch)
    cid = mcp_tools.create_client("Numberless Co", seed_default_chart=False)["client_id"]

    result = mcp_tools.import_accounts(cid, [
        {"name": "Operating Checking", "type": "Bank"},
        {"name": "Design Revenue", "type": "Income"},
    ])
    assert result["created"] == 2 and not result["errors"]
    assigned = {a["name"]: a["number"] for a in result["numbers_assigned"]}
    assert assigned["Operating Checking"].startswith("1")
    assert assigned["Design Revenue"].startswith("4")
    by_no = {a.account_number: a for a in Account.get_all(cid, active_only=False)}
    assert by_no[assigned["Operating Checking"]].subtype == "Cash"
