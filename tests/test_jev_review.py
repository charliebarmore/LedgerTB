from streamlit.testing.v1 import AppTest
import streamlit as st
import pytest

from services import jev_categorization as jev
from tests.conftest import page_path
from tests.test_jev_categorization import response
from utils.import_review import ensure_row_ids, row_key, scope_import_state_to_client


def page(monkeypatch, client_id, accounts, fake_credential_vault):
    import utils.client_selector as selector
    monkeypatch.setattr(selector, "render_client_selector", lambda: client_id)
    monkeypatch.setattr(st, "page_link", lambda *a, **k: None)
    fake_credential_vault.update(categorization_provider="jev", typesafe_api_key="fake")
    at = AppTest.from_file(page_path("pages/4_Import_Transactions.py"), default_timeout=60)
    rows = ensure_row_ids([{"date": "2026-01-01", "description": "Cedar Paper receipt: printer paper",
                           "amount": -33.33, "bank_account_id": accounts["cash"], "include": False}])
    at.session_state["import_active_tab"] = "Review & Categorize"
    at.session_state["transactions_to_review"] = rows
    return at, rows[0]


def test_reruns_and_acceptance_preserve_inclusion(monkeypatch, client_id, accounts, fake_credential_vault):
    calls = []
    def send(payload, key):
        calls.append(payload)
        return response(payload, f"account_{accounts['expense']}")
    monkeypatch.setattr(jev, "send_request", send)
    at, row = page(monkeypatch, client_id, accounts, fake_credential_vault)
    at.run()
    assert not at.exception and not calls
    at.multiselect(key="jev_rows").set_value([row["uid"]]).run()
    at.checkbox(key="jev_consent").check().run()
    assert not calls
    at.button(key="jev_run").click().run()
    assert not at.exception and len(calls) == 1
    assert at.session_state[row_key("cat", row)] is None
    assert not at.session_state[row_key("include", row)]
    at.run()
    at.button(key="jev_run").click().run()
    assert len(calls) == 1
    next(b for b in at.button if b.label == "Accept account suggestion").click().run()
    assert not at.exception
    assert at.session_state[row_key("cat", row)] == accounts["expense"]
    assert not at.session_state[row_key("include", row)]
    assert next(m for m in at.metric if m.label == "Uncategorized").value == "0"
    at.session_state["transactions_to_review"][0]["description"] = "Changed evidence"
    at.run()
    assert not at.exception
    assert at.session_state[row_key("cat", row)] is None
    assert not any(b.label == "Accept account suggestion" for b in at.button)
    assert len(calls) == 1


def test_failure_keeps_rows_and_no_automatic_retry(monkeypatch, client_id, accounts, fake_credential_vault):
    calls = []
    def send(*args):
        calls.append(1)
        raise TimeoutError()
    monkeypatch.setattr(jev, "send_request", send)
    at, row = page(monkeypatch, client_id, accounts, fake_credential_vault)
    at.run()
    at.multiselect(key="jev_rows").set_value([row["uid"]]).run()
    at.checkbox(key="jev_consent").check().run()
    at.button(key="jev_run").click().run()
    at.run()
    assert not at.exception
    assert len(calls) == 1
    assert at.session_state["transactions_to_review"][0]["description"] == row["description"]
    assert not at.session_state[row_key("include", row)]
    at.button(key="jev_retry").click().run()
    assert len(calls) == 2


def test_accept_callback_rechecks_evidence_changed_since_display(
    monkeypatch, client_id, accounts, fake_credential_vault,
):
    calls = []
    def send(payload, key):
        calls.append(payload)
        return response(payload, f"account_{accounts['expense']}")
    monkeypatch.setattr(jev, "send_request", send)
    at, row = page(monkeypatch, client_id, accounts, fake_credential_vault)
    at.run()
    at.multiselect(key="jev_rows").set_value([row["uid"]]).run()
    at.checkbox(key="jev_consent").check().run()
    at.button(key="jev_run").click().run()
    at.session_state["transactions_to_review"][0]["description"] = "Changed after the suggestion was displayed"
    next(b for b in at.button if b.label == "Accept account suggestion").click().run()
    assert not at.exception and len(calls) == 1
    assert at.session_state[row_key("cat", row)] is None
    assert not at.session_state[row_key("include", row)]
    assert "jev_accepted" not in at.session_state["transactions_to_review"][0]
    assert not any(b.label == "Accept account suggestion" for b in at.button)


@pytest.mark.parametrize("next_client,next_book", [(1, "book-b"), (2, "book-a")])
def test_ledger_switch_clears_consent_selection_and_results(next_client, next_book):
    state = {"jev_results": {"k": "v"}, "jev_rows": ["x"], "jev_consent": True,
             "jev_known_rows": ["x"], "bulk_rows": ["x"], "review_page": 2,
             "transactions_to_review": [{"uid": "x", "include": False,
                                          "jev_accepted": {"key": "k", "account_id": 1}}],
             "cat_x": 1, "include_x": False, "unrelated_preference": "keep"}
    scope_import_state_to_client(state, 1, "book-a")
    scope_import_state_to_client(state, next_client, next_book)
    assert not any(k.startswith("jev_") for k in state)
    assert not {"bulk_rows", "review_page", "transactions_to_review", "cat_x", "include_x"} & state.keys()
    assert state["unrelated_preference"] == "keep"


def test_settings_provider_and_key_use_vault(monkeypatch, client_id, fake_credential_vault):
    import utils.client_selector as selector
    monkeypatch.setattr(selector, "render_client_selector", lambda: client_id)
    monkeypatch.setattr(st, "page_link", lambda *a, **k: None)
    at = AppTest.from_file(page_path("pages/12_Firm_Settings.py"), default_timeout=60).run()
    assert not at.exception
    at.selectbox(key="firm_categorization_provider").set_value("jev").run()
    next(b for b in at.button if b.label == "Save categorization provider").click().run()
    assert fake_credential_vault["categorization_provider"] == "jev"
    at.text_input(key="firm_typesafe_key").set_value("fake-user-key").run()
    next(b for b in at.button if b.label == "Save TypeSafe key").click().run()
    assert not at.exception
    assert fake_credential_vault["typesafe_api_key"] == "fake-user-key"
    at.run()
    next(b for b in at.button if b.label == "Remove TypeSafe key").click().run()
    assert "typesafe_api_key" not in fake_credential_vault


def test_off_and_anthropic_options_remain_available(monkeypatch, client_id, accounts, fake_credential_vault):
    at, row = page(monkeypatch, client_id, accounts, fake_credential_vault)
    fake_credential_vault["categorization_provider"] = "off"
    at.run()
    assert not at.exception
    assert not any(b.label == "Ask Jev for suggestions" for b in at.button)
    assert not any("transactions with AI" in b.label for b in at.button)
    fake_credential_vault["categorization_provider"] = "anthropic"
    at.run()
    assert not at.exception
    assert any("transactions with AI" in b.label for b in at.button)


def _accept_fixture(at, row):
    at.run()
    at.multiselect(key="jev_rows").set_value([row["uid"]]).run()
    at.checkbox(key="jev_consent").check().run()
    at.button(key="jev_run").click().run()
    next(b for b in at.button if b.label == "Accept account suggestion").click().run()
    assert not at.exception


def test_accepted_category_survives_leaving_review(monkeypatch, client_id, accounts, fake_credential_vault):
    monkeypatch.setattr(jev, "send_request", lambda p,k: response(p, f"account_{accounts['expense']}"))
    at, row = page(monkeypatch, client_id, accounts, fake_credential_vault)
    _accept_fixture(at, row)
    at.radio[0].set_value("Upload CSV").run()
    at.radio[0].set_value("Review & Categorize").run()
    assert not at.exception
    assert at.session_state[row_key("cat", row)] == accounts["expense"]
    assert not at.session_state[row_key("include", row)]


def test_unaccepted_result_survives_navigation_without_another_paid_call(monkeypatch, client_id, accounts, fake_credential_vault):
    calls = []
    def send(payload, key):
        calls.append(payload)
        return response(payload, f"account_{accounts['expense']}")
    monkeypatch.setattr(jev, "send_request", send)
    at, row = page(monkeypatch, client_id, accounts, fake_credential_vault)
    at.run()
    at.multiselect(key="jev_rows").set_value([row["uid"]]).run()
    at.checkbox(key="jev_consent").check().run()
    at.button(key="jev_run").click().run()
    at.radio[0].set_value("Upload CSV").run()
    at.radio[0].set_value("Review & Categorize").run()
    assert not at.exception and len(calls) == 1
    assert any(b.label == "Accept account suggestion" for b in at.button)
    assert at.session_state[row_key("cat", row)] is None
    assert not at.session_state[row_key("include", row)]


def test_stale_acceptance_clears_even_when_provider_off(monkeypatch, client_id, accounts, fake_credential_vault):
    monkeypatch.setattr(jev, "send_request", lambda p,k: response(p, f"account_{accounts['expense']}"))
    at, row = page(monkeypatch, client_id, accounts, fake_credential_vault)
    _accept_fixture(at, row)
    fake_credential_vault["categorization_provider"] = "off"
    at.session_state["transactions_to_review"][0]["description"] = "Evidence changed while Jev off"
    at.run()
    assert not at.exception
    assert at.session_state[row_key("cat", row)] is None


def test_retry_enabled_immediately_after_failure(monkeypatch, client_id, accounts, fake_credential_vault):
    def fail(*args):
        raise TimeoutError()
    monkeypatch.setattr(jev, "send_request", fail)
    at, row = page(monkeypatch, client_id, accounts, fake_credential_vault)
    at.run()
    at.multiselect(key="jev_rows").set_value([row["uid"]]).run()
    at.checkbox(key="jev_consent").check().run()
    at.button(key="jev_run").click().run()
    assert not at.exception
    assert not at.button(key="jev_retry").disabled


def test_manual_override_survives_input_change_and_remount(monkeypatch, client_id, accounts, fake_credential_vault):
    monkeypatch.setattr(jev, "send_request", lambda p,k: response(p, f"account_{accounts['expense']}"))
    at, row = page(monkeypatch, client_id, accounts, fake_credential_vault)
    _accept_fixture(at, row)
    at.selectbox(key=row_key("cat", row)).set_value(accounts["revenue"]).run()
    at.session_state["transactions_to_review"][0]["description"] = "Confirmed customer payment"
    at.radio[0].set_value("Upload CSV").run()
    at.radio[0].set_value("Review & Categorize").run()
    assert not at.exception
    assert at.session_state[row_key("cat", row)] == accounts["revenue"]
    assert "jev_accepted" not in at.session_state["transactions_to_review"][0]


def test_explicit_clear_survives_remount_without_restoring_older_suggestion(monkeypatch, client_id, accounts, fake_credential_vault):
    monkeypatch.setattr(jev, "send_request", lambda p,k: response(p, f"account_{accounts['expense']}"))
    at, row = page(monkeypatch, client_id, accounts, fake_credential_vault)
    row["suggested_account_id"] = accounts["revenue"]  # pre-existing deterministic suggestion
    _accept_fixture(at, row)
    at.selectbox(key=row_key("cat", row)).set_value(None).run()
    at.radio[0].set_value("Upload CSV").run()
    at.radio[0].set_value("Review & Categorize").run()
    assert not at.exception
    assert at.session_state[row_key("cat", row)] is None


def test_stale_acceptance_after_unmount_with_provider_off(monkeypatch, client_id, accounts, fake_credential_vault):
    from models.client import Client
    monkeypatch.setattr(jev, "send_request", lambda p,k: response(p, f"account_{accounts['expense']}"))
    at, row = page(monkeypatch, client_id, accounts, fake_credential_vault)
    _accept_fixture(at, row)
    at.radio[0].set_value("Upload CSV").run()
    client = Client.get_by_id(client_id)
    client.business_context = "Evidence has been corrected"
    client.save()
    fake_credential_vault["categorization_provider"] = "off"
    at.radio[0].set_value("Review & Categorize").run()
    assert not at.exception
    assert at.session_state[row_key("cat", row)] is None


def test_off_without_accepted_jev_does_not_prepare_cloud_inputs(monkeypatch, client_id, accounts, fake_credential_vault):
    at, row = page(monkeypatch, client_id, accounts, fake_credential_vault)
    fake_credential_vault["categorization_provider"] = "off"
    def unexpected(*a, **k):
        raise AssertionError("No Jev preparation needed for local-only review")
    monkeypatch.setattr(jev, "request_input", unexpected)
    at.run()
    assert not at.exception


def test_large_import_prepares_only_chosen_previously_requested_or_accepted_rows(monkeypatch):
    from utils.jev_review import prepare_jev_review
    rows = [dict(uid=str(i), date='2026-01-01', description='Synthetic', amount=-1,
                 bank_account_id=1, include=False) for i in range(10_000)]
    state = {}
    calls = []
    original = jev.request_input
    def track(transaction, *args):
        calls.append(transaction['uid'])
        return original(transaction, *args)
    monkeypatch.setattr(jev, 'request_input', track)
    assert prepare_jev_review(rows, [], 1, 'fake-book', 'Synthetic', state) == ({}, {})
    assert calls == []
    state.update(jev_rows=['2', '3'], jev_known_rows=['7', 'removed-row'])
    prepared, keys = prepare_jev_review(rows, [], 1, 'fake-book', 'Synthetic', state)
    assert set(prepared) == set(keys) == {'2', '3', '7'}
    assert calls == ['2', '3', '7']
    assert state['jev_known_rows'] == ['7']
    assert all(not t['include'] for t in rows)
