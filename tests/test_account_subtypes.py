import json

import pytest

from constants import AccountSubtype, AccountType
from database.connection import get_cursor
from database.seed_data import (
    BUSINESS_TYPES,
    ENTITY_TYPES,
    get_accounts_for_client,
)
from models.account import Account


def test_every_new_seed_chart_uses_curated_subtypes():
    for entity_type in ENTITY_TYPES:
        for business_type in BUSINESS_TYPES:
            for _number, name, account_type, subtype in get_accounts_for_client(
                entity_type, business_type
            ):
                assert AccountSubtype.is_canonical(account_type, subtype), (
                    entity_type, business_type, name, account_type, subtype
                )


def test_new_accounts_normalize_safe_aliases_and_preserve_unknown_text(client_id):
    rent = Account(
        client_id=client_id,
        account_number="6100",
        name="Rent",
        type=AccountType.EXPENSE,
        subtype="Occupancy",
    )
    rent.save()
    custom = Account(
        client_id=client_id,
        account_number="6190",
        name="Special Cost",
        type=AccountType.EXPENSE,
        subtype="CPA Custom Group",
    )
    custom.save()

    assert Account.get_by_id(rent.id, client_id).subtype == (
        AccountSubtype.OPERATING_EXPENSE
    )
    assert Account.get_by_id(custom.id, client_id).subtype == "CPA Custom Group"


def test_bulk_subtype_assignment_is_atomic_and_audited(client_id):
    first = Account(
        client_id=client_id, account_number="6100", name="Rent",
        type=AccountType.EXPENSE,
    )
    second = Account(
        client_id=client_id, account_number="6200", name="Utilities",
        type=AccountType.EXPENSE, subtype="Occupancy",
    )
    liability = Account(
        client_id=client_id, account_number="2200", name="Accrued Expenses",
        type=AccountType.LIABILITY,
    )
    for account in (first, second, liability):
        account.save()

    assert Account.bulk_assign_subtype(
        client_id, [first.id, second.id], AccountSubtype.OPERATING_EXPENSE
    ) == 2
    assert {
        Account.get_by_id(first.id, client_id).subtype,
        Account.get_by_id(second.id, client_id).subtype,
    } == {AccountSubtype.OPERATING_EXPENSE}

    with get_cursor() as cursor:
        rows = cursor.execute(
            "SELECT record_id, old_values, new_values FROM audit_log "
            "WHERE table_name = 'accounts' AND action = 'UPDATE' "
            "AND record_id IN (?, ?) ORDER BY id",
            (first.id, second.id),
        ).fetchall()
    assert len(rows) == 2
    assert all(
        json.loads(row["new_values"])["subtype"]
        == AccountSubtype.OPERATING_EXPENSE
        for row in rows
    )

    with pytest.raises(ValueError, match="one account type"):
        Account.bulk_assign_subtype(
            client_id, [first.id, liability.id], AccountSubtype.OPERATING_EXPENSE
        )
    assert Account.get_by_id(liability.id, client_id).subtype is None
