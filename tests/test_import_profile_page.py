from streamlit.testing.v1 import AppTest
import streamlit as st

from models.import_profile import ImportProfile


SAVED_FORMAT_CSV = (
    "Posted Date,Merchant,Net Amount\n"
    "07/01/2026,COFFEE SHOP,-12.50\n"
)


def _page(monkeypatch, client_id, bank_account_id, content=SAVED_FORMAT_CSV):
    import utils.client_selector as selector

    monkeypatch.setattr(selector, "render_client_selector", lambda: client_id)
    monkeypatch.setattr(st, "page_link", lambda *args, **kwargs: None)
    page = AppTest.from_file(
        "pages/4_Import_Transactions.py", default_timeout=60
    )
    page.session_state["import_active_tab"] = "Upload CSV"
    page.session_state["csv_multi_account_mode"] = False
    page.session_state["csv_bank_account"] = bank_account_id
    page.session_state["csv_content"] = content
    page.session_state["csv_raw_content"] = content
    page.session_state["csv_filename"] = "bank-export.csv"
    return page.run()


def test_saved_mapping_round_trips_through_import_widgets(
    client_id, accounts, monkeypatch
):
    page = _page(monkeypatch, client_id, accounts["cash"])
    assert not page.exception

    page.selectbox(key="csv_date_column").set_value("Posted Date").run()
    page.selectbox(key="csv_description_column").set_value("Merchant").run()
    page.radio(key="csv_amount_format").set_value("Single Amount Column").run()
    page.selectbox(key="csv_amount_column").set_value("Net Amount").run()
    page.selectbox(key="csv_sign_convention").set_value("flip").run()
    page.button(key="save_csv_import_profile").click().run()

    saved = ImportProfile.list_for_account(client_id, accounts["cash"])[0]
    assert saved.name == "bank export"
    assert saved.date_column == "Posted Date"
    assert saved.description_column == "Merchant"
    assert saved.amount_column == "Net Amount"
    assert saved.sign_convention == "flip"

    fresh = _page(monkeypatch, client_id, accounts["cash"])
    assert not fresh.exception
    assert fresh.selectbox(key="csv_date_column").value == "Posted Date"
    assert fresh.selectbox(key="csv_description_column").value == "Merchant"
    assert fresh.selectbox(key="csv_amount_column").value == "Net Amount"
    assert fresh.selectbox(key="csv_sign_convention").value == "flip"
    assert fresh.selectbox(key="csv_import_profile_id").value == saved.id
    assert any("Saved mapping applied" in caption.value for caption in fresh.caption)


def test_incompatible_saved_mapping_falls_back_to_detection(
    client_id, accounts, monkeypatch
):
    ImportProfile(
        client_id=client_id,
        bank_account_id=accounts["cash"],
        name="Bank website",
        date_column="Posted Date",
        description_column="Merchant",
        amount_format="single",
        amount_column="Net Amount",
        sign_convention="bank",
    ).save()
    ordinary_csv = "Date,Description,Amount\n07/01/2026,Coffee,-12.50\n"

    page = _page(
        monkeypatch, client_id, accounts["cash"], content=ordinary_csv
    )

    assert not page.exception
    assert page.selectbox(key="csv_import_profile_id").value == 0
    assert page.selectbox(key="csv_date_column").value == "Date"
    assert page.selectbox(key="csv_description_column").value == "Description"
    assert page.selectbox(key="csv_amount_column").value == "Amount"
    assert any(
        "No saved format matched" in info.value
        for info in page.info
    )


def test_multiple_formats_auto_match_their_complete_headers(
    client_id, accounts, monkeypatch
):
    first = ImportProfile(
        client_id=client_id,
        bank_account_id=accounts["cash"],
        name="Bank website",
        date_column="Posted Date",
        description_column="Merchant",
        amount_format="single",
        amount_column="Net Amount",
        sign_convention="bank",
        header_signature=ImportProfile.signature_for_columns(
            ["Posted Date", "Merchant", "Net Amount"]
        ),
    )
    first.save()
    second = ImportProfile(
        client_id=client_id,
        bank_account_id=accounts["cash"],
        name="Accounting system",
        date_column="Transaction Date",
        description_column="Memo",
        amount_format="single",
        amount_column="Value",
        sign_convention="flip",
        header_signature=ImportProfile.signature_for_columns(
            ["Transaction Date", "Memo", "Value", "Reference"]
        ),
    )
    second.save()
    second_csv = (
        "Transaction Date,Memo,Value,Reference\n"
        "07/02/2026,Deposit,125.00,ABC123\n"
    )

    page = _page(monkeypatch, client_id, accounts["cash"], content=second_csv)

    assert not page.exception
    assert page.selectbox(key="csv_import_profile_id").value == second.id
    assert page.selectbox(key="csv_date_column").value == "Transaction Date"
    assert page.selectbox(key="csv_description_column").value == "Memo"
    assert page.selectbox(key="csv_amount_column").value == "Value"
    assert page.selectbox(key="csv_sign_convention").value == "flip"
    assert any(
        "Automatically matched saved format" in caption.value
        for caption in page.caption
    )
