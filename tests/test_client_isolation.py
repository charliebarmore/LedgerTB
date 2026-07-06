"""Client-isolation tests (M5).

Two clients live in the same database. These tests assert that a query or
mutation scoped to one client can never read or change another client's data,
and that the list methods are already client-scoped.
"""

from datetime import date

import pytest

from conftest import post_entry
from models.client import Client
from models.account import Account
from models.journal_entry import JournalEntry
from models.reports import ReportGenerator


@pytest.fixture
def two_clients(db):
    """Two fully-separate clients, each with accounts and one journal entry."""
    a = Client(name="Client A", entity_type="S-Corp", fiscal_year_end_month=12).save(seed_accounts=False)
    b = Client(name="Client B", entity_type="S-Corp", fiscal_year_end_month=12).save(seed_accounts=False)

    a_cash = Account(client_id=a, account_number="1000", name="A Cash", type="Asset"); a_cash.save()
    a_rev = Account(client_id=a, account_number="4000", name="A Revenue", type="Revenue"); a_rev.save()
    b_cash = Account(client_id=b, account_number="1000", name="B Cash", type="Asset"); b_cash.save()
    b_rev = Account(client_id=b, account_number="4000", name="B Revenue", type="Revenue"); b_rev.save()

    a_entry = post_entry(a, date(2025, 1, 1), [(a_cash.id, 100, 0), (a_rev.id, 0, 100)])
    b_entry = post_entry(b, date(2025, 1, 1), [(b_cash.id, 500, 0), (b_rev.id, 0, 500)])

    return {
        "a": a, "b": b,
        "a_cash": a_cash.id, "a_rev": a_rev.id,
        "b_cash": b_cash.id, "b_rev": b_rev.id,
        "a_entry": a_entry.id, "b_entry": b_entry.id,
    }


def test_list_methods_are_client_scoped(two_clients):
    d = two_clients
    a_accts = {x.id for x in Account.get_all(d["a"])}
    assert a_accts == {d["a_cash"], d["a_rev"]}
    assert d["b_cash"] not in a_accts

    a_entries = {e.id for e in JournalEntry.get_all(d["a"])}
    assert a_entries == {d["a_entry"]}
    assert d["b_entry"] not in a_entries


def test_account_get_by_id_rejects_cross_client(two_clients):
    d = two_clients
    assert Account.get_by_id(d["b_cash"], client_id=d["a"]) is None       # cross-client -> None
    assert Account.get_by_id(d["b_cash"], client_id=d["b"]).id == d["b_cash"]  # own client works
    assert Account.get_by_id(d["b_cash"]).id == d["b_cash"]               # unscoped still works


def test_entry_get_by_id_rejects_cross_client(two_clients):
    d = two_clients
    assert JournalEntry.get_by_id(d["b_entry"], client_id=d["a"]) is None
    assert JournalEntry.get_by_id(d["b_entry"], client_id=d["b"]).id == d["b_entry"]


def test_delete_scoped_to_wrong_client_is_noop(two_clients):
    d = two_clients
    # Attempt to delete B's entry as client A -- must NOT delete it.
    JournalEntry.delete(d["b_entry"], client_id=d["a"])
    assert JournalEntry.get_by_id(d["b_entry"]) is not None

    # The rightful client can delete it.
    JournalEntry.delete(d["b_entry"], client_id=d["b"])
    assert JournalEntry.get_by_id(d["b_entry"]) is None


def test_general_ledger_scoped_to_wrong_client_is_empty(two_clients):
    d = two_clients
    assert ReportGenerator.general_ledger(d["b_cash"], client_id=d["a"]) == []
    assert ReportGenerator.general_ledger(d["b_cash"], client_id=d["b"]) != []


def test_get_balance_scoped_to_wrong_client_is_zero(two_clients):
    d = two_clients
    assert Account.get_balance(d["b_cash"], client_id=d["a"]) == 0.0
    assert Account.get_balance(d["b_cash"], client_id=d["b"]) == 500.0
