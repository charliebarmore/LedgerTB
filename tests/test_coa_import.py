"""Tests for the chart-of-accounts CSV parser (services/coa_import.py)."""

from services.coa_import import parse_coa_csv, normalize_type


def test_parses_basic_csv():
    csv = "Account Number,Name,Type,Subtype\n1000,Cash,Asset,Cash\n4000,Revenue,Income,\n"
    accounts, errors = parse_coa_csv(csv)
    assert errors == []
    assert [a["number"] for a in accounts] == ["1000", "4000"]
    assert accounts[0] == {"number": "1000", "name": "Cash", "type": "Asset",
                           "subtype": "Cash", "description": None}
    # "Income" normalized to Revenue
    assert accounts[1]["type"] == "Revenue"


def test_flexible_headers_and_type_aliases():
    csv = "Acct #,Account Name,Category,Description\n6000,Rent,Expenses,Office rent\n"
    accounts, errors = parse_coa_csv(csv)
    assert not errors
    assert accounts[0]["type"] == "Expense"      # "Expenses" -> Expense
    assert accounts[0]["description"] == "Office rent"


def test_missing_required_column():
    accounts, errors = parse_coa_csv("Name,Type\nCash,Asset\n")  # no number column
    assert accounts == []
    assert errors and "Missing required column" in errors[0]


def test_bad_rows_are_reported_not_dropped_silently():
    csv = ("Number,Name,Type\n"
           "1000,Cash,Asset\n"
           "2000,,Liability\n"          # missing name
           "3000,Mystery,Wumbo\n"       # bad type
           "1000,Dup,Asset\n")          # duplicate number
    accounts, errors = parse_coa_csv(csv)
    assert [a["number"] for a in accounts] == ["1000"]   # only the good row
    assert len(errors) == 3
    assert any("missing name" in e for e in errors)
    assert any("unknown type" in e for e in errors)
    assert any("duplicate" in e for e in errors)


def test_blank_lines_ignored():
    accounts, errors = parse_coa_csv("Number,Name,Type\n1000,Cash,Asset\n,,\n")
    assert len(accounts) == 1 and not errors


def test_normalize_type():
    assert normalize_type("assets") == "Asset"
    assert normalize_type("INCOME") == "Revenue"
    assert normalize_type("nonsense") is None
