import pytest

from models.account import Account
from models.audit_log import AuditLog
from models.client import Client
from models.import_profile import ImportProfile


def _profile(client_id, bank_account_id, **overrides):
    values = {
        "client_id": client_id,
        "bank_account_id": bank_account_id,
        "name": "Bank website",
        "date_column": "Posted Date",
        "description_column": "Merchant",
        "amount_format": "single",
        "amount_column": "Net Amount",
        "sign_convention": "bank",
    }
    values.update(overrides)
    return ImportProfile(**values)


def test_account_can_store_multiple_named_formats(client_id, accounts):
    profile = _profile(client_id, accounts["cash"])
    first_id = profile.save()

    second = _profile(
        client_id,
        accounts["cash"],
        name="Accounting export",
        date_column="Transaction Date",
        description_column="Memo",
        amount_column="Value",
    )
    second_id = second.save()

    saved_formats = ImportProfile.list_for_account(client_id, accounts["cash"])
    assert [profile.name for profile in saved_formats] == [
        "Accounting export",
        "Bank website",
    ]
    assert {profile.id for profile in saved_formats} == {first_id, second_id}

    saved = ImportProfile.get_by_id(client_id, first_id)
    assert saved.id == first_id
    assert saved.amount_column == "Net Amount"

    updated = _profile(
        client_id,
        accounts["cash"],
        id=first_id,
        amount_format="separate",
        amount_column=None,
        debit_column="Money Out",
        credit_column="Money In",
        sign_convention="flip",
    )
    assert updated.save() == first_id
    saved = ImportProfile.get_by_id(client_id, first_id)
    assert saved.amount_format == "separate"
    assert saved.amount_column is None
    assert saved.debit_column == "Money Out"
    assert saved.sign_convention == "flip"

    actions = [
        log.action for log in AuditLog.get_history("import_profiles", first_id)
    ]
    assert actions == ["UPDATE", "INSERT"]


def test_format_names_are_unique_per_account_case_insensitively(client_id, accounts):
    _profile(client_id, accounts["cash"], name="Bank Download").save()
    with pytest.raises(ValueError, match="already exists"):
        _profile(client_id, accounts["cash"], name="bank download").save()


def test_profile_is_client_scoped_and_requires_importable_account(db):
    client_a = Client(name="A").save(seed_accounts=False)
    client_b = Client(name="B").save(seed_accounts=False)
    bank_b = Account(
        client_id=client_b, account_number="1000", name="Bank", type="Asset"
    )
    bank_b.save()
    expense_a = Account(
        client_id=client_a, account_number="6000", name="Expense", type="Expense"
    )
    expense_a.save()

    with pytest.raises(ValueError, match="belong to the client"):
        _profile(client_a, bank_b.id).save()
    with pytest.raises(ValueError, match="asset or liability"):
        _profile(client_a, expense_a.id).save()
    assert ImportProfile.list_for_account(client_a, bank_b.id) == []


def test_profile_resolves_only_when_all_saved_columns_exist(client_id, accounts):
    profile = _profile(client_id, accounts["cash"])
    detected = {
        "date": "Date",
        "description": "Description",
        "amount": "Amount",
        "debit": None,
        "credit": None,
    }

    applied = profile.resolve_columns(
        ["Posted Date", "Merchant", "Net Amount"], detected
    )
    assert applied["applied"] is True
    assert applied["date_column"] == "Posted Date"
    assert applied["amount_column"] == "Net Amount"

    fallback = profile.resolve_columns(
        ["Date", "Description", "Amount"], detected
    )
    assert fallback["applied"] is False
    assert fallback["missing"] == ["Posted Date", "Merchant", "Net Amount"]
    assert fallback["date_column"] == "Date"
    assert fallback["amount_column"] == "Amount"


def test_profiles_match_exact_header_signature_and_reject_ambiguity(client_id, accounts):
    bank = _profile(
        client_id,
        accounts["cash"],
        name="Bank CSV",
        header_signature=ImportProfile.signature_for_columns(
            ["Posted Date", "Merchant", "Net Amount"]
        ),
    )
    bank.save()
    alternate = _profile(
        client_id,
        accounts["cash"],
        name="Alternate CSV",
        header_signature=ImportProfile.signature_for_columns(
            ["Posted Date", "Merchant", "Net Amount", "Reference"]
        ),
    )
    alternate.save()
    profiles = ImportProfile.list_for_account(client_id, accounts["cash"])

    assert ImportProfile.match_for_columns(
        profiles, ["Posted Date", "Merchant", "Net Amount"]
    ).id == bank.id
    assert ImportProfile.match_for_columns(
        profiles, ["Posted Date", "Merchant", "Net Amount", "Reference"]
    ).id == alternate.id
    # Both mappings are technically compatible without an exact signature;
    # guessing would be unsafe.
    assert ImportProfile.match_for_columns(
        profiles, ["Posted Date", "Merchant", "Net Amount", "Other"]
    ) is None


def test_profile_delete_is_audited(client_id, accounts):
    profile = _profile(client_id, accounts["cash"])
    profile.save()

    assert ImportProfile.delete(client_id, profile.id) is True
    assert ImportProfile.get_by_id(client_id, profile.id) is None
    assert AuditLog.get_history("import_profiles", profile.id)[0].action == "DELETE"
    assert ImportProfile.delete(client_id, profile.id) is False
