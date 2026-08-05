"""Draft entries: assistant proposals that only a human can post.

The contract under test: an MCP connection can file a draft but can never
touch the ledger (engine authorizer); approval posts a real, audited journal
entry; validation holds at both ends.
"""
from datetime import date

import pytest

from database import connection as dbconn
from models.account import Account
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
