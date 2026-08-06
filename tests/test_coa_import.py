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
    # No number column is fine (QBO exports); no NAME column is not.
    accounts, errors = parse_coa_csv("Name,Type\nCash,Asset\n")
    assert len(accounts) == 1 and not errors
    assert accounts[0]["number"] == ""  # assigned later by the importer

    accounts, errors = parse_coa_csv("Number,Type\n1000,Asset\n")
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
    assert normalize_type("assets") == ("Asset", None)
    assert normalize_type("INCOME") == ("Revenue", None)
    assert normalize_type("Bank") == ("Asset", "Cash")
    assert normalize_type("nonsense") is None


def test_quickbooks_type_names_map_with_implied_subtypes():
    """A real QB export imports whole: Bank must land as Asset/Cash or every
    cash-keyed feature silently misses it; nothing may drop without an error."""
    from services.coa_import import parse_coa_csv

    csv_text = (
        "Account Number,Account Name,Type,Subtype\n"
        "1000,Operating Checking,Bank,\n"
        "1100,Client Receivables,Accounts Receivable (A/R),\n"
        "1500,Studio Equipment,Fixed Asset,\n"
        "2100,Visa Card,Credit Card,\n"
        "2200,Payroll Withholding,Other Current Liability,\n"
        "4900,Interest Earned,Other Income,\n"
        "5000,Production Costs,Cost of Goods Sold,\n"
        "6100,Rent,Expense,Occupancy\n"
        "9999,Mystery,Suspense Widget,\n"
    )
    accounts, errors = parse_coa_csv(csv_text)

    assert len(accounts) == 8
    by_no = {a["number"]: a for a in accounts}
    assert by_no["1000"]["type"] == "Asset" and by_no["1000"]["subtype"] == "Cash"
    assert by_no["1100"]["subtype"] == "Receivable"
    assert by_no["2100"]["type"] == "Liability" and by_no["2100"]["subtype"] == "Credit Card"
    assert by_no["5000"]["subtype"] == "Cost of Goods Sold"
    # An explicit subtype in the file beats the implied one.
    assert by_no["6100"]["subtype"] == "Occupancy"
    # The unmappable row is loudly reported, never silently dropped.
    assert len(errors) == 1
    assert "will NOT be imported" in errors[0] and "Suspense Widget" in errors[0]


def test_numberless_chart_gets_numbers_assigned_by_type():
    """QuickBooks Online exports often have no number column at all; the
    importer assigns by type range, collision-free, and reports what it chose."""
    from services.coa_import import assign_missing_numbers, parse_coa_csv

    csv_text = (
        "Account Name,Type\n"
        "Operating Checking,Bank\n"
        "Owner Draws,Equity\n"
        "Design Revenue,Income\n"
        "Software,Expense\n"
    )
    accounts, errors = parse_coa_csv(csv_text)
    assert not errors and len(accounts) == 4

    assigned = assign_missing_numbers(accounts, taken={"1000", "6000"})
    assert len(assigned) == 4
    by_name = {a["name"]: a for a in accounts}
    assert by_name["Operating Checking"]["number"] == "1010"  # 1000 taken
    assert by_name["Owner Draws"]["number"] == "3000"
    assert by_name["Design Revenue"]["number"] == "4000"
    assert by_name["Software"]["number"] == "6010"  # 6000 taken
    assert all(a["number_assigned"] for a in accounts)
