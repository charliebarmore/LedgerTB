"""Draft entries: assistant proposals that only a human can post.

The contract under test: an MCP connection can file a draft but can never
touch the ledger (engine authorizer); approval posts a real, audited journal
entry; validation holds at both ends.
"""
from datetime import date

import pytest

from database import connection as dbconn
from models.account import Account
from models.audit_log import AuditLog
from models.client import Client
from models.draft_entry import DraftEntry, DraftLine
from models.journal_entry import JournalEntry
from services import mcp_tools
from tests.conftest import post_entry


def _numbers(client_id, accounts):
    cash = Account.get_by_id(accounts["cash"], client_id=client_id)
    revenue = Account.get_by_id(accounts["revenue"], client_id=client_id)
    return cash.account_number, revenue.account_number


def test_draft_validation_rejects_bad_proposals(client_id, accounts):
    cash_no, rev_no = _numbers(client_id, accounts)

    with pytest.raises(ValueError, match="balance"):
        DraftEntry(client_id=client_id, entry_date="2026-07-31", description="x",
                   lines=[DraftLine(cash_no, debit_cents=100),
                          DraftLine(rev_no, credit_cents=200)]).save()
    with pytest.raises(ValueError, match="two lines"):
        DraftEntry(client_id=client_id, entry_date="2026-07-31", description="x",
                   lines=[DraftLine(cash_no, debit_cents=100)]).save()
    with pytest.raises(ValueError, match="No account numbered"):
        DraftEntry(client_id=client_id, entry_date="2026-07-31", description="x",
                   lines=[DraftLine("9999", debit_cents=100),
                          DraftLine(rev_no, credit_cents=100)]).save()
    with pytest.raises(ValueError, match="ISO date"):
        DraftEntry(client_id=client_id, entry_date="07/31/2026", description="x",
                   lines=[DraftLine(cash_no, debit_cents=100),
                          DraftLine(rev_no, credit_cents=100)]).save()


def test_approve_posts_a_real_audited_entry(client_id, accounts):
    cash_no, rev_no = _numbers(client_id, accounts)
    draft = DraftEntry(
        client_id=client_id, proposed_by="Assistant (MCP)",
        entry_date="2026-07-31", description="Accrue July retainer",
        rationale="Deposit hit the bank on 7/31 per the feed.",
        lines=[DraftLine(cash_no, debit_cents=12_345),
               DraftLine(rev_no, credit_cents=12_345, memo="retainer")],
    )
    draft.save()
    assert DraftEntry.pending_count(client_id) == 1

    entry_id = draft.approve()
    assert draft.status == "approved" and draft.posted_entry_id == entry_id
    assert DraftEntry.pending_count(client_id) == 0

    entry = JournalEntry.get_by_id(entry_id, client_id=client_id)
    assert entry is not None
    assert f"Draft #{draft.id}" in entry.source_reference
    assert sum(l.debit for l in entry.lines) == pytest.approx(123.45)
    with pytest.raises(ValueError, match="pending"):
        draft.approve()  # no double-posting

    draft_logs = [
        log for log in AuditLog.get_all(client_id)
        if log.table_name == "draft_entries" and log.record_id == draft.id
    ]
    assert [log.action for log in draft_logs] == ["UPDATE", "INSERT"]
    assert draft_logs[0].old_values["status"] == "pending"
    assert draft_logs[0].new_values["status"] == "approved"
    assert draft_logs[0].new_values["posted_entry_id"] == entry_id


def test_correction_proposal_retains_original_to_posted_chain(client_id, accounts):
    cash_no, rev_no = _numbers(client_id, accounts)
    original = post_entry(
        client_id, date(2026, 7, 30),
        [(accounts["cash"], 125, 0), (accounts["revenue"], 0, 125)],
    )

    result = mcp_tools.propose_correction(
        client_id=client_id,
        original_entry_id=original.id,
        entry_date="2026-07-31",
        description="Reverse duplicate revenue posting",
        lines=[
            {"account_number": cash_no, "credit": 125},
            {"account_number": rev_no, "debit": 125},
        ],
        rationale="The bank activity was imported twice.",
    )
    assert result["original_entry_id"] == original.id

    draft = DraftEntry.get_by_id(result["draft_id"], client_id)
    assert draft.original_entry_id == original.id
    assert mcp_tools.list_drafts(client_id)[0]["original_entry_id"] == original.id
    assert DraftEntry.get_for_originals(client_id, [original.id]) == {
        original.id: [draft]
    }

    draft_log = next(
        log for log in AuditLog.get_all(client_id)
        if log.table_name == "draft_entries" and log.record_id == draft.id
    )
    assert draft_log.new_values["original_entry_id"] == original.id

    correction_id = draft.approve()
    correction = JournalEntry.get_by_id(correction_id, client_id=client_id)
    assert correction.source_reference.startswith(
        f"Correction of JE #{original.id} · Draft #{draft.id}"
    )
    stored = DraftEntry.get_by_id(draft.id, client_id)
    assert stored.original_entry_id == original.id
    assert stored.posted_entry_id == correction_id
    assert JournalEntry.get_by_id(original.id, client_id=client_id) is not None

    with pytest.raises(ValueError, match=f"correction draft #{draft.id}"):
        JournalEntry.delete(original.id, client_id=client_id)


def test_correction_proposal_rejects_cross_client_original(client_id, accounts):
    cash_no, rev_no = _numbers(client_id, accounts)
    original = post_entry(
        client_id, date(2026, 7, 30),
        [(accounts["cash"], 10, 0), (accounts["revenue"], 0, 10)],
    )
    other_client = Client(
        name="Other Co", entity_type="LLC", fiscal_year_end_month=12
    ).save(seed_accounts=False)

    with pytest.raises(ValueError, match="must belong to the selected client"):
        mcp_tools.propose_correction(
            client_id=other_client,
            original_entry_id=original.id,
            entry_date="2026-07-31",
            description="Invalid cross-client correction",
            lines=[
                {"account_number": cash_no, "credit": 10},
                {"account_number": rev_no, "debit": 10},
            ],
        )


def test_stale_draft_objects_cannot_double_post(client_id, accounts):
    cash_no, rev_no = _numbers(client_id, accounts)
    draft = DraftEntry(
        client_id=client_id, entry_date="2026-07-31", description="One only",
        lines=[DraftLine(cash_no, debit_cents=100),
               DraftLine(rev_no, credit_cents=100)],
    )
    draft.save()
    first = DraftEntry.get_by_id(draft.id, client_id)
    stale = DraftEntry.get_by_id(draft.id, client_id)

    first.approve()
    with pytest.raises(ValueError, match="pending"):
        stale.approve()

    assert JournalEntry.count(client_id) == 1
    assert DraftEntry.get_by_id(draft.id, client_id).posted_entry_id == first.posted_entry_id


def test_every_adjusting_draft_receives_next_aje_reference_on_approval(
    client_id, accounts
):
    cash_no, rev_no = _numbers(client_id, accounts)
    references = []
    for proposed_by in ("Assistant (MCP)", "Human-filed draft"):
        draft = DraftEntry(
            client_id=client_id,
            proposed_by=proposed_by,
            entry_date="2026-07-31",
            entry_type="Adjusting",
            description="Number this adjustment",
            lines=[
                DraftLine(cash_no, debit_cents=100),
                DraftLine(rev_no, credit_cents=100),
            ],
        )
        draft.save()
        entry_id = draft.approve()
        references.append(
            JournalEntry.get_by_id(entry_id, client_id=client_id).aje_reference
        )

    assert references == ["AJE-001", "AJE-002"]


def test_approval_rolls_back_entry_and_claim_when_draft_audit_fails(
    client_id, accounts, monkeypatch
):
    cash_no, rev_no = _numbers(client_id, accounts)
    draft = DraftEntry(
        client_id=client_id, entry_date="2026-07-31", description="Atomic",
        lines=[DraftLine(cash_no, debit_cents=100),
               DraftLine(rev_no, credit_cents=100)],
    )
    draft.save()
    original_write = AuditLog.write

    def fail_draft_resolution(cursor, client_id, table_name, record_id, action, **kwargs):
        if table_name == "draft_entries" and action == "UPDATE":
            raise RuntimeError("draft audit failed")
        return original_write(
            cursor, client_id, table_name, record_id, action, **kwargs
        )

    monkeypatch.setattr(AuditLog, "write", fail_draft_resolution)
    with pytest.raises(RuntimeError, match="draft audit failed"):
        draft.approve()

    assert JournalEntry.count(client_id) == 0
    stored = DraftEntry.get_by_id(draft.id, client_id)
    assert stored.status == "pending"
    assert stored.posted_entry_id is None


def test_reject_leaves_the_ledger_alone(client_id, accounts):
    cash_no, rev_no = _numbers(client_id, accounts)
    before = JournalEntry.count(client_id)
    draft = DraftEntry(client_id=client_id, entry_date="2026-07-31",
                       description="nope",
                       lines=[DraftLine(cash_no, debit_cents=100),
                              DraftLine(rev_no, credit_cents=100)])
    draft.save()
    draft.reject()
    assert draft.status == "rejected"
    assert JournalEntry.count(client_id) == before
    draft_logs = [
        log for log in AuditLog.get_all(client_id)
        if log.table_name == "draft_entries" and log.record_id == draft.id
    ]
    assert [log.action for log in draft_logs] == ["UPDATE", "INSERT"]
    assert draft_logs[0].new_values["status"] == "rejected"
    reviewed = DraftEntry.get_resolved(client_id)
    assert [item.id for item in reviewed] == [draft.id]
    assert reviewed[0].resolved_at and reviewed[0].resolved_by


def test_draft_inbox_mode_files_drafts_but_cannot_reach_the_ledger(
    client_id, accounts, monkeypatch
):
    cash_no, rev_no = _numbers(client_id, accounts)
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", "propose")

    # Reads work; proposing works (this is the MCP server's exact mode).
    assert mcp_tools.trial_balance(client_id)["balanced"] is True
    result = mcp_tools.propose_entry(
        client_id, "2026-07-31", "Assistant proposal",
        [{"account_number": cash_no, "debit": 24.00},
         {"account_number": rev_no, "credit": 24.00}],
        rationale="test",
    )
    assert result["status"] == "pending"

    # The ledger is unreachable — even through the real posting model.
    with pytest.raises(Exception, match="not authorized|prohibited|DatabaseError|denied"):
        post_entry(client_id, date(2026, 7, 31),
                   [(accounts["cash"], 1, 0), (accounts["revenue"], 0, 1)])

    drafts = mcp_tools.list_drafts(client_id)
    assert len(drafts) == 1 and drafts[0]["lines"][0]["debit"] == 24.0

    # Back in the app (normal mode), a human approves it.
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", None)
    draft = DraftEntry.get_by_id(drafts[0]["draft_id"], client_id)
    entry_id = draft.approve()
    assert JournalEntry.get_by_id(entry_id, client_id=client_id) is not None


def test_beginning_balance_draft_round_trips(db, client_id, accounts):
    """An opening-balance proposal files as Beginning Balance and keeps that
    type on the posted entry — the assistant should never have to mislabel
    it Regular and ask the human to fix it on approval."""
    from models.draft_entry import DraftEntry, DraftLine
    from models.journal_entry import JournalEntry
    import pytest

    draft = DraftEntry(
        client_id=client_id, proposed_by="Assistant (MCP)",
        entry_date="2026-01-01", entry_type="Beginning Balance",
        description="Opening balances",
        lines=[DraftLine(account_number="1000", debit_cents=500_00),
               DraftLine(account_number="3000", credit_cents=500_00)],
    )
    draft_id = draft.save()
    listed = mcp_tools.list_drafts(client_id)
    assert listed[0]["entry_type"] == "Beginning Balance"
    entry_id = DraftEntry.get_by_id(draft_id, client_id).approve()
    assert JournalEntry.get_by_id(entry_id).entry_type == "Beginning Balance"

    with pytest.raises(ValueError, match="entry_type"):
        DraftEntry(
            client_id=client_id, proposed_by="Assistant (MCP)",
            entry_date="2026-01-01", entry_type="Wumbo", description="x",
            lines=[DraftLine(account_number="1000", debit_cents=1),
                   DraftLine(account_number="3000", credit_cents=1)],
        ).save()
