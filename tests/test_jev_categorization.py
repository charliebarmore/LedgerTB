import copy
import urllib.error

import pytest

from models.account import Account
from services import jev_categorization as jev


def inputs(accounts, client_id):
    return jev.request_input(
        {"date": "2026-01-01", "description": "Cedar Paper receipt: printer paper", "amount": -33.33,
         "bank_account_id": accounts["cash"], "notes": "PRIVATE"},
        Account.get_all(client_id), client_id, "Synthetic design studio",
    )


def response(payload, choice=None):
    answers = {}
    for key, question in payload["questions"].items():
        options = question["criteria"]
        chosen = choice or next(iter(options))
        answers[key] = {"type": "choice", "choice": chosen, "confidence": 1.0,
                        "probabilities": {k: float(k == chosen) for k in options}}
    return {"model": "jev-test", "answers": answers, "usage": {"input_tokens": 123, "output_tokens": 45}}


def test_disclosure_allowlist_and_eligible_accounts(accounts, client_id):
    data = inputs(accounts, client_id)
    assert data["transaction"]["amount_cents"] == -3333
    assert "PRIVATE" not in str(data)
    assert {a["id"] for a in data["eligible_accounts"]} == {accounts["expense"], accounts["revenue"]}
    wrong = Account(id=99, client_id=client_id + 1, name="Other client", type="Expense")
    inactive = Account(id=98, client_id=client_id, name="Inactive", type="Expense", is_active=False)
    assert jev.eligible_accounts([wrong, inactive], client_id) == []


@pytest.mark.parametrize("outcome", list(jev.OUTCOMES) + ["account"])
def test_outcomes_only_suggest_never_write(accounts, client_id, outcome):
    from database.connection import get_connection
    data = inputs(accounts, client_id)
    cache = {}
    choice = f"account_{accounts['expense']}" if outcome == "account" else outcome
    jev.suggest({"key": data}, cache, api_key="fake", consent=True,
                transport=lambda p, k: response(p, choice))
    assert cache["key"]["outcome"] == outcome
    assert cache["key"]["requires_review"] is True
    assert cache["key"]["account_id"] == (accounts["expense"] if outcome == "account" else None)
    with get_connection() as conn:
        assert conn.execute("SELECT count(*) FROM journal_entries").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM imported_transactions").fetchone()[0] == 0


def test_consent_cache_retry_and_scope(accounts, client_id):
    data = inputs(accounts, client_id)
    key = jev.request_key(("book-a", client_id), data)
    cache, calls = {}, []
    def send(payload, secret):
        calls.append(payload)
        return response(payload)
    for consent, secret in [(False, "fake"), (True, "")]:
        jev.suggest({key: data}, cache, api_key=secret, consent=consent, transport=send)
    assert not calls
    jev.suggest({key: data}, cache, api_key="fake", consent=True, retry=True, transport=send)
    jev.suggest({key: data}, cache, api_key="fake", consent=True, retry=True, transport=send)
    assert len(calls) == 1  # successes are never retried
    assert jev.request_key(("book-b", client_id), data) != key
    assert jev.request_key(("book-a", client_id + 1), data) != key
    for field in ("description", "date", "amount_cents", "receipt_text", "bank_account_id", "is_transfer"):
        changed = copy.deepcopy(data)
        changed["transaction"][field] = "changed"
        assert jev.request_key(("book-a", client_id), changed) != key
    for field in ("business_context", "eligible_accounts"):
        changed = {**data, field: "changed"}
        assert jev.request_key(("book-a", client_id), changed) != key


@pytest.mark.parametrize("failure", [TimeoutError("SECRET"), urllib.error.URLError("SECRET"),
    urllib.error.HTTPError(jev.ENDPOINT, 401, "SECRET", {}, None),
    urllib.error.HTTPError(jev.ENDPOINT, 429, "SECRET", {}, None), ValueError("SECRET")])
def test_failures_safe_cached_and_explicit_retry(accounts, client_id, failure):
    data = inputs(accounts, client_id)
    calls, cache = [], {}
    def send(p, k):
        calls.append(1)
        raise failure
    for _ in range(2):
        jev.suggest({"key": data}, cache, api_key="fake", consent=True, transport=send)
    assert len(calls) == 1
    assert "SECRET" not in str(cache)
    jev.suggest({"key": data}, cache, api_key="fake", consent=True, retry=True, transport=lambda p,k: response(p))
    assert "error" not in cache["key"]


@pytest.mark.parametrize("defect", ["invented", "nan", "sum", "missing", "winner", "confidence", "question"])
def test_invalid_responses_fail_closed(accounts, client_id, defect):
    data, cache = inputs(accounts, client_id), {}
    def send(p, k):
        r = response(p)
        a = r["answers"]["key"]
        if defect == "invented": a["choice"] = "account_999999"
        if defect == "nan": a["probabilities"][a["choice"]] = float("nan")
        if defect == "sum": a["probabilities"][a["choice"]] = 0.2
        if defect == "missing": a["probabilities"].pop("split_required")
        if defect == "winner": a["choice"] = "split_required"
        if defect == "confidence": a["confidence"] = True
        if defect == "question": r["answers"]["wrong"] = r["answers"].pop("key")
        return r
    jev.suggest({"key": data}, cache, api_key="fake", consent=True, transport=send)
    assert cache["key"].get("error")
    assert "account_id" not in cache["key"]


def test_provider_uses_fake_vault_default_off(fake_credential_vault):
    assert jev.configured_provider() == "off"
    fake_credential_vault["categorization_provider"] = "jev"
    assert jev.configured_provider() == "jev"


def test_transport_timeout_auth_and_no_redirect(monkeypatch):
    import json
    class Reply:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return b'{"ok": true}'
    class Opener:
        def open(self, request, timeout):
            assert request.full_url == jev.ENDPOINT
            assert request.get_header("Authorization") == "Bearer fake"
            assert json.loads(request.data) == {"model": "test"}
            assert timeout == 30
            return Reply()
    monkeypatch.setattr(jev.urllib.request, "build_opener", lambda *handlers: Opener())
    assert jev.send_request({"model": "test"}, "fake") == {"ok": True}
    assert jev._NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.test") is None


def test_interrupted_request_cannot_repeat_on_rerun(accounts, client_id):
    data, cache, calls = inputs(accounts, client_id), {}, []
    def interrupt(p, k):
        calls.append(1)
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        jev.suggest({"key": data}, cache, api_key="fake", consent=True, transport=interrupt)
    jev.suggest({"key": data}, cache, api_key="fake", consent=True, transport=interrupt)
    assert len(calls) == 1 and "interrupted" in cache["key"]["error"]


def test_instruction_change_invalidates_cached_result(accounts, client_id, monkeypatch):
    data = inputs(accounts, client_id)
    original = jev.request_key(("book", client_id), data)
    monkeypatch.setattr(jev, "INSTRUCTIONS", jev.INSTRUCTIONS + " Updated policy.")
    assert original != jev.request_key(("book", client_id), data)


def test_staged_jev_review_preserves_identity_permissions_and_posting(accounts, client_id, monkeypatch):
    from database import connection as db
    from models.transaction import ImportedTransaction
    from models.audit_log import AuditLog
    from services import mcp_tools
    from services.posting import post_transaction
    from utils import actor
    monkeypatch.setattr(db, "ASSISTANT_ACCESS_LEVEL", "propose")
    actor.mark_as_assistant()
    row = {"date": "2026-01-01", "description": "Synthetic paper receipt", "amount": -33.33}
    staged = mcp_tools.propose_import(client_id, "1000", [row], "Synthetic statement")
    transaction = ImportedTransaction.get_by_status(client_id, "Pending")[0]
    identity = (transaction.row_fingerprint, transaction.idempotency_key)
    hydrated = {**row, "date": transaction.transaction_date, "bank_account_id": accounts["cash"], "include": False,
                "source_id": transaction.source_id, "source_filename": transaction.source_filename,
                "source_row_number": transaction.source_row_number,
                "row_fingerprint": identity[0], "idempotency_key": identity[1], "staged_id": transaction.id}
    before = copy.deepcopy(hydrated)
    data = jev.request_input(hydrated, Account.get_all(client_id), client_id, "Synthetic studio")
    cache = {}
    jev.suggest({"key": data}, cache, api_key="fake", consent=True,
                transport=lambda p,k: response(p, f"account_{accounts['expense']}"))
    assert hydrated == before
    for level in ("read", "propose"):
        monkeypatch.setattr(db, "ASSISTANT_ACCESS_LEVEL", level)
        with pytest.raises(Exception):
            post_transaction(client_id, hydrated, cache["key"]["account_id"], accounts["cash"], learn=False)
        assert ImportedTransaction.get_by_status(client_id, "Pending")[0].id == transaction.id
    monkeypatch.setattr(db, "ASSISTANT_ACCESS_LEVEL", None)
    monkeypatch.setattr(actor, "_ASSISTANT", False)
    entry, posted = post_transaction(client_id, hydrated, cache["key"]["account_id"], accounts["cash"], batch_id=staged["batch_id"])
    assert posted.id == transaction.id and posted.status == "Posted"
    assert (posted.row_fingerprint, posted.idempotency_key) == identity
    with db.get_connection() as conn:
        sums = conn.execute("SELECT SUM(debit), SUM(credit) FROM journal_entry_lines WHERE journal_entry_id = ?", (entry.id,)).fetchone()
        assert tuple(sums) == (3333, 3333)
        assert conn.execute("SELECT count(*) FROM imported_transactions").fetchone()[0] == 1
    history = AuditLog.get_history("journal_entries", entry.id)
    assert history and "(AI)" not in history[0].performed_by
    again = mcp_tools.propose_import(client_id, "1000", [row], "Synthetic statement")
    assert again["staged"] == 0
