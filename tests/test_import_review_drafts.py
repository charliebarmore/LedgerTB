from datetime import date
import json
import pytest
from database import connection as dbconn
from services import import_review_drafts as drafts
from models.audit_log import AuditLog
from models.client import Client
from models.journal_entry import JournalEntry
from services.posting import post_transaction
from tests.test_jev_review import page
from utils.import_review import row_key


def row(accounts):
    return dict(
        date=date(2026, 9, 2),
        description="Fictional recovery paper",
        amount=-33.33,
        bank_account_id=accounts["cash"],
        selected_account_id=accounts["expense"],
        include=False,
        is_transfer=False,
        source_id="fictional-recovery",
        source_row_number=2,
        batch_id="saved-review",
        receipt_text="Fictional printer paper receipt",
        api_key="must-not-persist",
        jev_results={"private": "must-not-persist"},
        duplicate_override=True,
    )


def test_encrypted_review_roundtrip_conflict_and_atomic_audit(
    client_id, accounts, monkeypatch
):
    source = row(accounts)
    rev = drafts.save(client_id, [source])
    loaded_rev, rows = drafts.load(client_id)
    assert rev == loaded_rev and rows[0]["amount"] == -33.33 and not rows[0]["include"]
    assert not rows[0]["duplicate_override"] and "api_key" not in rows[0]
    with dbconn.get_cursor() as cur:
        payload = cur.execute("SELECT payload FROM import_review_drafts").fetchone()[0]
        assert json.loads(payload)["rows"][0]["amount_cents"] == -3333
        assert "must-not-persist" not in payload
        audit = cur.execute(
            "SELECT * FROM audit_log WHERE table_name='import_review_drafts'"
        ).fetchone()
        from utils.actor import current_actor

        assert audit["performed_by"] == current_actor()
    assert b"Fictional recovery paper" not in dbconn.DATABASE_PATH.read_bytes()
    assert JournalEntry.count(client_id) == 0
    with pytest.raises(drafts.ReviewConflict):
        drafts.save(client_id, [source])
    with pytest.raises(drafts.ReviewConflict):
        drafts.discard(client_id, "stale")

    def fail(*a, **k):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(AuditLog, "write", fail)
    with pytest.raises(RuntimeError):
        drafts.save(client_id, [source], expected_revision=rev)
    assert drafts.summary(client_id)["revision"] == rev
    with pytest.raises(RuntimeError):
        drafts.discard(client_id, rev)
    assert drafts.summary(client_id)["revision"] == rev


def test_review_is_client_scoped_and_engine_permissions_apply(
    client_id, accounts, monkeypatch
):
    second = Client(name="Other fictional client").save(seed_accounts=False)
    rev = drafts.save(client_id, [row(accounts)])
    assert drafts.load(second) is None
    monkeypatch.setattr(dbconn, "READ_ONLY", True)
    assert drafts.load(client_id)[0] == rev
    with pytest.raises(Exception):
        drafts.save(client_id, [row(accounts)], expected_revision=rev)
    with pytest.raises(Exception):
        drafts.discard(client_id, rev)
    monkeypatch.setattr(dbconn, "READ_ONLY", False)
    for level in ("read", "propose", "post"):
        monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", level)
        with pytest.raises(Exception):
            drafts.save(client_id, [row(accounts)], expected_revision=rev)
        with pytest.raises(Exception):
            drafts.discard(client_id, rev)
    monkeypatch.setattr(dbconn, "ASSISTANT_ACCESS_LEVEL", None)
    assert drafts.summary(client_id)["revision"] == rev


def test_saved_review_survives_new_session_without_request_or_duplicate_post(
    monkeypatch, client_id, accounts, fake_credential_vault
):
    from services import jev_categorization, review_categorization

    def forbidden(*a, **k):
        pytest.fail("resuming must not send AI requests")

    monkeypatch.setattr(jev_categorization, "send_request", forbidden)
    monkeypatch.setattr(review_categorization, "send_request", forbidden)
    at, _ = page(monkeypatch, client_id, accounts, fake_credential_vault)
    at.session_state["transactions_to_review"] = [row(accounts)]
    at.run()
    assert not at.exception
    at.button(key="review_save").click().run()
    assert not at.exception and drafts.summary(client_id)["row_count"] == 1
    fresh, _ = page(monkeypatch, client_id, accounts, fake_credential_vault)
    fresh.session_state["transactions_to_review"] = []
    fresh.run()
    fresh.button(key="review_resume").click().run()
    assert not fresh.exception
    restored = fresh.session_state["transactions_to_review"][0]
    assert (
        not restored["include"]
        and restored["selected_account_id"] == accounts["expense"]
    )
    assert not fresh.session_state[row_key("include", restored)]
    # Post independently, then resume an older copy: exact source is excluded.
    post_transaction(
        client_id,
        row(accounts),
        accounts["expense"],
        accounts["cash"],
        batch_id="saved-review",
    )
    another, _ = page(monkeypatch, client_id, accounts, fake_credential_vault)
    another.session_state["transactions_to_review"] = []
    another.run()
    another.button(key="review_resume").click().run()
    assert not another.exception
    restored = another.session_state["transactions_to_review"][0]
    assert restored["is_duplicate"] and not restored["include"]
    assert JournalEntry.count(client_id) == 1


@pytest.mark.parametrize("boundary", ["before_commit", "after_commit"])
def test_abrupt_exit_retains_complete_saved_copy(
    client_id, accounts, tmp_path, boundary
):
    import os, sys, subprocess
    from pathlib import Path

    source = row(accounts)
    revision = drafts.save(client_id, [source])
    source["description"] = "Updated fictional review"
    fixture = tmp_path / "fake-review-crash.json"
    fixture.write_text(
        json.dumps(
            dict(
                key=dbconn.get_active_key(),
                client_id=client_id,
                boundary=boundary,
                revision=revision,
                row=source,
            ),
            default=str,
        )
    )
    process = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parent / "helpers/review_crash_worker.py"),
            str(fixture),
        ],
        env=dict(
            os.environ,
            LEDGERTB_DB_PATH=str(dbconn.DATABASE_PATH),
            ANTHROPIC_API_KEY="test-key-never-used",
        ),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 73, process.stderr
    loaded_revision, rows = drafts.load(client_id)
    committed = boundary == "after_commit"
    assert rows[0]["description"] == (
        "Updated fictional review" if committed else "Fictional recovery paper"
    )
    assert (loaded_revision != revision) == committed
    with dbconn.get_cursor() as cur:
        assert cur.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert cur.execute(
            "SELECT count(*) FROM audit_log WHERE table_name='import_review_drafts'"
        ).fetchone()[0] == 1 + int(committed)
    assert JournalEntry.count(client_id) == 0


def test_resumed_ai_choice_is_revalidated_even_without_cached_response(
    client_id, accounts
):
    from models.account import Account
    from services import jev_categorization as jev
    from utils.jev_review import prepare_jev_review

    source = row(accounts)
    choices = Account.get_all(client_id)
    key = jev.request_key(
        (str(dbconn.DATABASE_PATH), client_id),
        jev.request_input(source, choices, client_id, ""),
    )
    source["jev_accepted"] = {"key": key, "account_id": accounts["expense"]}
    drafts.save(client_id, [source])
    _, rows = drafts.load(client_id)
    state = {}
    prepare_jev_review(
        rows,
        choices,
        client_id,
        dbconn.DATABASE_PATH,
        "",
        state,
        include_unaccepted=False,
    )
    assert rows[0]["selected_account_id"] == accounts["expense"]
    # Current context changed since the saved human acceptance.
    prepare_jev_review(
        rows,
        choices,
        client_id,
        dbconn.DATABASE_PATH,
        "Changed fictional business context",
        state,
        include_unaccepted=False,
    )
    assert rows[0]["selected_account_id"] == 0 and not rows[0]["include"]


def test_old_readonly_book_without_review_table_remains_readable(
    client_id, monkeypatch
):
    with dbconn.get_cursor(commit=True) as cur:
        cur.execute("DROP TABLE import_review_drafts")
    monkeypatch.setattr(dbconn, "READ_ONLY", True)
    assert drafts.summary(client_id) is None
