"""Fictional CPA review journeys, through the real page and encrypted engine."""

from datetime import date
import pytest
from tests.test_jev_review import page
from utils.import_review import ensure_row_ids, row_key
from services import posting
from models.journal_entry import JournalEntry
from database import connection as dbconn


def review(monkeypatch, client_id, accounts, fake_credential_vault, rows):
    at, _ = page(monkeypatch, client_id, accounts, fake_credential_vault)
    fake_credential_vault["categorization_provider"] = "off"
    rows = ensure_row_ids(
        [
            dict(
                date=date(2026, 9, 2),
                amount=-12.34,
                bank_account_id=accounts["cash"],
                batch_id="fictional-scenarios",
                source_id="fictional-statement",
                source_row_number=i + 2,
                **r,
            )
            for i, r in enumerate(rows)
        ]
    )
    at.session_state["transactions_to_review"] = rows
    at.run()
    assert not at.exception
    return at, rows


def test_failed_included_and_excluded_rows_are_reported_truthfully(
    monkeypatch, client_id, accounts, fake_credential_vault
):
    def fail(*a, **k):
        raise OSError("private internal path /secret/book.db")

    monkeypatch.setattr(posting, "post_transaction", fail)
    at, rows = review(
        monkeypatch,
        client_id,
        accounts,
        fake_credential_vault,
        [
            dict(
                description="Fictional purchase pending save",
                include=True,
                selected_account_id=accounts["expense"],
            ),
            dict(description="Excluded unknown purchase", include=False),
        ],
    )
    next(b for b in at.button if b.label == "Post Transactions").click().run()
    assert not at.exception
    messages = " ".join(e.value for e in [*at.warning, *at.error, *at.info])
    assert "1 failed" in messages and "1 excluded" in messages
    assert "all 1 row(s) were excluded" not in messages
    assert "/secret/book.db" not in messages
    assert len(at.session_state["transactions_to_review"]) == 2
    assert JournalEntry.count(client_id) == 0


def test_read_only_review_disables_post_and_preserves_staged_work(
    monkeypatch, client_id, accounts, fake_credential_vault
):
    at, rows = review(
        monkeypatch,
        client_id,
        accounts,
        fake_credential_vault,
        [
            dict(
                description="Fictional supplies",
                include=True,
                selected_account_id=accounts["expense"],
            )
        ],
    )
    monkeypatch.setattr(dbconn, "READ_ONLY", True)
    at.run()
    assert not at.exception
    assert next(b for b in at.button if b.label == "Post Transactions").disabled
    assert len(at.session_state["transactions_to_review"]) == 1
    assert JournalEntry.count(client_id) == 0


def test_success_failed_and_unresolved_rows_survive_partial_post(
    monkeypatch, client_id, accounts, fake_credential_vault
):
    original = posting.post_transaction

    def one_failure(*a, **k):
        if k["transaction"]["description"] == "Temporary failure":
            raise TimeoutError()
        return original(*a, **k)

    monkeypatch.setattr(posting, "post_transaction", one_failure)
    at, rows = review(
        monkeypatch,
        client_id,
        accounts,
        fake_credential_vault,
        [
            dict(
                description="Paper receipt",
                include=True,
                selected_account_id=accounts["expense"],
            ),
            dict(
                description="Temporary failure",
                include=True,
                selected_account_id=accounts["expense"],
            ),
            dict(description="Missing receipt", include=True),
            dict(description="Excluded personal item", include=False),
        ],
    )
    next(b for b in at.button if b.label == "Post Transactions").click().run()
    assert not at.exception
    assert JournalEntry.count(client_id) == 1
    assert {r["description"] for r in at.session_state["transactions_to_review"]} == {
        "Temporary failure",
        "Missing receipt",
    }
    monkeypatch.setattr(posting, "post_transaction", original)
    next(b for b in at.button if b.label == "Post Transactions").click().run()
    assert not at.exception and JournalEntry.count(client_id) == 2
    assert len(at.session_state["transactions_to_review"]) == 1
    with dbconn.get_cursor() as cur:
        assert (
            cur.execute("SELECT SUM(debit-credit) FROM journal_entry_lines").fetchone()[
                0
            ]
            == 0
        )
        assert (
            cur.execute(
                "SELECT count(*) FROM audit_log WHERE table_name='journal_entries' AND action='INSERT'"
            ).fetchone()[0]
            == 2
        )


@pytest.mark.parametrize("provider", ["manual", "jev", "anthropic", "openai"])
def test_realistic_bank_and_card_review_journey(
    monkeypatch, client_id, accounts, fake_credential_vault, provider
):
    import copy
    import json
    from pathlib import Path
    from money import to_dollars
    from services import jev_categorization as jev, review_categorization as ai
    from tests.test_review_categorization import response
    from models.transaction import ImportedTransaction

    scenarios = json.loads(
        (Path(__file__).parent / "fixtures/import_review_journeys.json").read_text()
    )
    by_description = {r["description"]: r for r in scenarios}
    calls = []

    def choice(row):
        expected = by_description[row["description"]]["outcome"]
        return f"account_{accounts[expected]}" if expected in accounts else expected

    def jev_send(payload, key):
        calls.append("jev")
        answers = {}
        for k, q in payload["questions"].items():
            chosen = choice(payload["state"]["transactions"][k])
            answers[k] = dict(
                type="choice",
                choice=chosen,
                confidence=0.95,
                probabilities={c: float(c == chosen) for c in q["criteria"]},
            )
        return dict(model="fictional-jev", answers=answers, usage={})

    def ai_send(p, payload, key):
        calls.append(p)
        state = json.loads(
            payload["input"][0]["content"]
            if p == "openai"
            else payload["messages"][0]["content"]
        )
        result = response(p, payload)
        block = (
            result["output"][0]["content"][0] if p == "openai" else result["content"][0]
        )
        block["text"] = json.dumps(
            dict(
                suggestions=[
                    dict(
                        request_id=k,
                        choice=choice(row),
                        reason="Fictional labeled workflow outcome.",
                    )
                    for k, row in state["transactions"].items()
                ]
            )
        )
        return result

    monkeypatch.setattr(jev, "send_request", jev_send)
    monkeypatch.setattr(ai, "send_request", ai_send)
    at, _ = page(monkeypatch, client_id, accounts, fake_credential_vault)
    fake_credential_vault.update(
        categorization_provider="off" if provider == "manual" else provider,
        anthropic_api_key="fake",
        openai_api_key="fake",
    )
    rows = ensure_row_ids(
        [
            dict(
                date=date(2026, 9, 2),
                description=r["description"],
                amount=to_dollars(r["amount_cents"]),
                bank_account_id=accounts[r["source"]],
                include=False,
                batch_id="fictional-journey",
                source_id=f'fictional-{r["source"]}',
                source_row_number=i + 2,
            )
            for i, r in enumerate(scenarios)
        ]
    )
    at.session_state["transactions_to_review"] = rows
    at.run()
    assert not at.exception
    if provider != "manual":
        at.multiselect(key="bulk_rows").set_value([r["uid"] for r in rows]).run()
        at.checkbox(
            key="jev_consent" if provider == "jev" else "ai_review_consent"
        ).check().run()
        at.button(key="jev_run" if provider == "jev" else "ai_review_run").click().run()
        assert not at.exception and calls == [provider]
        assert all(at.session_state[row_key("cat", r)] is None for r in rows)
        assert all(not at.session_state[row_key("include", r)] for r in rows)
        for row, scenario in zip(rows, scenarios):
            if scenario["outcome"] not in ("expense", "revenue"):
                continue
            prefix = "jev_accept_" if provider == "jev" else "ai_review_accept_"
            next(
                b
                for b in at.button
                if b.key and b.key.startswith(prefix + row["uid"] + "_")
            ).click().run()
        assert not at.exception and calls == [provider]
    for row, scenario in zip(rows, scenarios):
        if not scenario["post"]:
            continue
        target = scenario.get("manual", scenario["outcome"])
        if scenario["outcome"] == "transfer_review":
            at.checkbox(key=row_key("xfer", row)).check().run()
        if provider == "manual" or scenario.get("manual"):
            at.selectbox(key=row_key("cat", row)).set_value(accounts[target]).run()
        at.checkbox(key=row_key("include", row)).check().run()
    # Keep unresolved rows included: they must survive posting, without fabricated categories.
    for row, scenario in zip(rows, scenarios):
        if not scenario["post"]:
            at.checkbox(key=row_key("include", row)).check().run()
    next(b for b in at.button if b.label == "Post Transactions").click().run()
    assert not at.exception
    assert JournalEntry.count(client_id) == 7
    assert len(at.session_state["transactions_to_review"]) == 2
    assert {r["description"] for r in at.session_state["transactions_to_review"]} == {
        s["description"] for s in scenarios if not s["post"]
    }
    posted = ImportedTransaction.get_by_status(client_id, "Posted")
    assert len(posted) == 7 and all(
        r.row_fingerprint and r.idempotency_key for r in posted
    )
    # An identical source retry cannot create a second journal or import record.
    first = next(r for r in rows if r["description"] == scenarios[0]["description"])
    posting.post_transaction(
        client_id,
        copy.deepcopy(first),
        accounts["expense"],
        accounts["cash"],
        batch_id="retry",
    )
    assert JournalEntry.count(client_id) == 7
    with dbconn.get_cursor() as cur:
        assert (
            cur.execute("SELECT SUM(debit-credit) FROM journal_entry_lines").fetchone()[
                0
            ]
            == 0
        )
        assert (
            cur.execute(
                "SELECT count(*) FROM audit_log WHERE table_name='journal_entries' AND action='INSERT'"
            ).fetchone()[0]
            == 7
        )
        for scenario in scenarios:
            if not scenario["post"]:
                continue
            target = accounts[scenario.get("manual", scenario["outcome"])]
            entry = next(
                e
                for e in JournalEntry.get_all(client_id)
                if scenario["description"] in e.description
            )
            lines = cur.execute(
                "SELECT account_id,debit,credit FROM journal_entry_lines WHERE journal_entry_id=?",
                (entry.id,),
            ).fetchall()
            target_line = next(l for l in lines if l["account_id"] == target)
            # Expense refunds credit the expense; card purchases debit expense / credit liability.
            assert (
                target_line["debit"] - target_line["credit"]
                == -scenario["amount_cents"]
            )
