"""Client model tests for the extended client-info fields (migration 003)."""

import pytest

from models.client import Client


def test_client_extended_fields_roundtrip(db):
    cid = Client(
        name="Acme LLC", entity_type="S-Corporation", business_type="Professional Services",
        fiscal_year_end_month=6, tax_id="12-3456789", dba_name="Acme",
        address_line1="123 Main St", address_city="Riverton", address_state="GA", address_zip="30301",
        contact_name="Jane Doe", contact_email="jane@acme.com", contact_phone="706-555-0100",
        notes="Quarterly reviews",
        business_context=(
            "Design studio with February tile pre-sales and three payment "
            "processors that settle differently."
        ),
    ).save(seed_accounts=False)

    c = Client.get_by_id(cid)
    assert c.dba_name == "Acme"
    assert c.tax_id == "12-3456789"
    assert (c.address_line1, c.address_city, c.address_state, c.address_zip) == \
        ("123 Main St", "Riverton", "GA", "30301")
    assert (c.contact_name, c.contact_email, c.contact_phone) == \
        ("Jane Doe", "jane@acme.com", "706-555-0100")
    assert c.notes == "Quarterly reviews"
    assert "February tile pre-sales" in c.business_context
    assert c.fiscal_year_end_month == 6


def test_client_categorization_context_combines_structured_and_custom_data(db):
    client = Client(
        name="Tile Studio",
        entity_type="S-Corporation",
        business_type="Other",
        business_context="Accounts 2100 and 2110 are card liabilities.",
    )

    context = client.categorization_context()

    assert "S-Corporation" in context
    assert "Other" in context
    assert "2100 and 2110 are card liabilities" in context


def test_client_update_extended_fields(db):
    cid = Client(name="X", entity_type="LLC (Single-Member)").save(seed_accounts=False)
    c = Client.get_by_id(cid)
    c.contact_email = "new@x.com"
    c.address_city = "Augusta"
    c.business_context = "Updated categorization context."
    c.save(seed_accounts=False)

    c2 = Client.get_by_id(cid)
    assert c2.contact_email == "new@x.com"
    assert c2.address_city == "Augusta"
    assert c2.business_context == "Updated categorization context."


def test_client_minimal_still_works(db):
    """A client created with only a name must still round-trip (new fields None)."""
    cid = Client(name="Just A Name").save(seed_accounts=False)
    c = Client.get_by_id(cid)
    assert c.name == "Just A Name"
    assert c.tax_id is None and c.contact_email is None and c.notes is None
    assert c.business_context is None


def test_client_and_seed_chart_are_atomic(db, monkeypatch):
    def fail_seeding(*args, **kwargs):
        raise RuntimeError("simulated seed failure")

    monkeypatch.setattr("models.client.seed_chart_of_accounts_for_client", fail_seeding)
    with pytest.raises(RuntimeError, match="seed failure"):
        Client(name="Half Created").save(seed_accounts=True)

    assert all(c.name != "Half Created" for c in Client.get_all(active_only=False))
