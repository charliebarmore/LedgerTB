"""Assistant setup tools and the human review checkpoint.

Setup: an assistant at 'propose' may scaffold a client and its chart —
and can never alter either afterwards. Review: assistant-attributed audit
rows queue for a person, sign-off is append-only and audit-logged, and the
sidebar count drains to zero.
"""
import pytest

from database import connection as dbconn
from database.connection import get_cursor
from models import assistant_review
from models.account import Account
from services import mcp_tools


def _as_assistant(monkeypatch, level="propose"):
    from utils import actor as actor_mod

    monkeypatch.setattr(actor_mod, "_ASSISTANT", True)
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", level)


def test_create_client_seeds_chart_and_is_immutable_after(db, monkeypatch):
    _as_assistant(monkeypatch)
    result = mcp_tools.create_client("Northline Digital (Demo)",
                                     entity_type="LLC")
    assert result["accounts_seeded"] > 10  # default chart came with it

    with pytest.raises(ValueError, match="already exists"):
        mcp_tools.create_client("Northline Digital (Demo)")
    # Setup is INSERT-only: the assistant cannot alter the client afterwards.
    with pytest.raises(Exception):
        with get_cursor(commit=True) as cursor:
            cursor.execute("UPDATE clients SET name = 'Renamed' WHERE id = ?",
                           (result["client_id"],))


def test_import_accounts_maps_qb_names_and_reports_everything(db, monkeypatch):
    _as_assistant(monkeypatch)
    cid = mcp_tools.create_client("QB Import Co", seed_default_chart=False)["client_id"]

    result = mcp_tools.import_accounts(cid, [
        {"number": "1000", "name": "Operating Checking", "type": "Bank"},
        {"number": "2100", "name": "Visa", "type": "Credit Card"},
        {"number": "9999", "name": "Mystery", "type": "Suspense Widget"},
    ])
    assert result["created"] == 2
    assert result["errors"] and "Suspense Widget" in result["errors"][0]

    by_no = {a.account_number: a for a in Account.get_all(cid, active_only=False)}
    assert by_no["1000"].type == "Asset" and by_no["1000"].subtype == "Cash"
    assert by_no["2100"].type == "Liability"

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

    through = assistant_review.mark_reviewed(client_id)
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
