"""Assistant-staged imports: normalize anywhere, review and post in the app.

Contract: propose_import stages Pending rows with full import identity (so
duplicate protection holds), re-proposing is harmless, the assistant's
connection can only INSERT them, and posting through the normal flow ADOPTS
the staged row — one record, Pending → Posted, no double-counting.
"""
from datetime import date

import pytest

from database import connection as dbconn
from models.account import Account
from models.transaction import ImportedTransaction
from services import mcp_tools
from services.import_identity import classify_import_duplicates
from services.posting import post_transaction


ROWS = [
    {"date": "2026-07-03", "description": "ACME COFFEE", "amount": -12.50},
    {"date": "2026-07-07", "description": "CLIENT PAYMENT", "amount": 1500.00},
    {"date": "2026-07-11", "description": "OFFICE DEPOT", "amount": -84.20},
]


def _cash_number(client_id, accounts):
    return Account.get_by_id(accounts["cash"], client_id=client_id).account_number


def test_propose_import_stages_with_identity_and_is_idempotent(
    client_id, accounts, monkeypatch
):
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", "propose")  # the MCP mode
    cash_no = _cash_number(client_id, accounts)

    result = mcp_tools.propose_import(client_id, cash_no, ROWS, "July stmt")
    assert result["staged"] == 3 and result["skipped_already_known"] == 0

    staged = ImportedTransaction.get_by_status(client_id, "Pending")
    assert len(staged) == 3
    assert all(t.row_fingerprint and t.idempotency_key for t in staged)
    assert all(t.import_batch == result["batch_id"] for t in staged)

    # Re-proposing the same statement stages nothing new.
    again = mcp_tools.propose_import(client_id, cash_no, ROWS, "July stmt")
    assert again["staged"] == 0 and again["skipped_already_known"] == 3
    assert len(ImportedTransaction.get_by_status(client_id, "Pending")) == 3

    # The assistant cannot touch its own staged rows after filing them.
    with pytest.raises(Exception):
        with dbconn.get_cursor(commit=True) as cursor:
            cursor.execute(
                "UPDATE imported_transactions SET amount = 999999 WHERE client_id = ?",
                (client_id,))

    assert len(mcp_tools.list_staged_imports(client_id)) == 3


def test_propose_import_validation(client_id, accounts):
    cash_no = _cash_number(client_id, accounts)
    with pytest.raises(ValueError, match="cash or credit-card"):
        rev_no = Account.get_by_id(accounts["revenue"], client_id=client_id).account_number
        mcp_tools.propose_import(client_id, rev_no, ROWS)
    with pytest.raises(ValueError, match="ISO date"):
        mcp_tools.propose_import(client_id, cash_no,
                                 [{"date": "07/03/2026", "description": "x",
                                   "amount": 1}])
    with pytest.raises(ValueError, match="cannot be zero"):
        mcp_tools.propose_import(client_id, cash_no,
                                 [{"date": "2026-07-03", "description": "x",
                                   "amount": 0}])


def test_hydration_does_not_self_match_and_posting_adopts_the_row(
    client_id, accounts
):
    cash_no = _cash_number(client_id, accounts)
    result = mcp_tools.propose_import(client_id, cash_no, ROWS, "July stmt")

    staged = ImportedTransaction.get_by_status(client_id, "Pending")
    hydrated = [{
        "staged_id": t.id, "batch_id": t.import_batch,
        "date": t.transaction_date, "description": t.description,
        "amount": t.amount, "client_id": client_id,
        "bank_account_id": t.bank_account_id, "source_id": t.source_id,
        "source_filename": t.source_filename,
        "source_row_number": t.source_row_number,
        "row_fingerprint": t.row_fingerprint,
        "idempotency_key": t.idempotency_key,
    } for t in staged]

    # Without exclusion each row would match its own staged record.
    dup = classify_import_duplicates(
        hydrated, client_id, exclude_ids=frozenset(t.id for t in staged))
    assert dup == 0
    assert all(not r["is_duplicate"] for r in hydrated)

    before_ids = {t.id for t in staged}
    entry, txn = post_transaction(
        client_id=client_id,
        transaction=hydrated[0],
        target_account_id=accounts["expense"],
        bank_account_id=accounts["cash"],
        batch_id=hydrated[0]["batch_id"],
    )
    # Adopted, not duplicated: same record id, now Posted with its entry.
    assert txn.id in before_ids
    assert txn.status == "Posted" and txn.journal_entry_id == entry.id
    assert len(ImportedTransaction.get_by_status(client_id, "Pending")) == 2
    all_rows = (ImportedTransaction.get_by_status(client_id, "Pending")
                + ImportedTransaction.get_by_status(client_id, "Posted"))
    assert len(all_rows) == 3  # nothing double-recorded
