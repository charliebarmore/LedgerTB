"""Crash/restore/window boundaries on disposable SQLCipher books and fake vaults."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from database import connection as dbc
from models.audit_log import AuditLog
from models.client import Client
from models.journal_entry import JournalEntry
from services import book_generation as generation, import_review_drafts as drafts
from services import review_recovery_store as recovery
from services.backups import create_backup, restore_backup
from services.posting import post_transaction
from tests.test_import_review_drafts import row
from tests.test_jev_review import page
from tests.test_review_transitions import button
from utils import desktop_review_status as channel
from utils.import_review import row_key


def restore_audit(conn):
    AuditLog.write(conn.cursor(), None, 'database_restore', 0, 'RESTORE',
                   new_values={'fixture': 'desktop-pilot'})


def test_recovery_is_encrypted_isolated_deduplicated_and_never_explicit_save(client_id, accounts):
    source = row(accounts)
    epoch = generation.current()
    saved = drafts.save(client_id, [source])
    first = recovery.save(client_id, 'one', [source], expected_generation=epoch, base_revision=saved)
    assert recovery.save(client_id, 'one', [source], expected_generation=epoch, base_revision=saved) == first
    changed = dict(source, description='Other window fictional purchase', include=True)
    second = recovery.save(client_id, 'two', [changed], expected_generation=epoch, base_revision=saved)
    other = Client(name='Another fictional client').save(seed_accounts=False)
    assert not recovery.list_copies(other)
    assert [c['revision'] for c in recovery.list_copies(client_id, 'one')] == [second]
    loaded, baseline = recovery.load(client_id, 'one', first)
    assert baseline == saved and loaded[0]['amount'] == -33.33 and not loaded[0]['include']
    assert 'api_key' not in loaded[0] and not loaded[0]['duplicate_override']
    assert drafts.load(client_id)[0] == saved
    with dbc.get_cursor() as cur:
        payload = cur.execute('SELECT payload FROM import_review_recovery WHERE window_id=?', ('one',)).fetchone()[0]
        assert json.loads(payload)['rows'][0]['amount_cents'] == -3333
        assert 'must-not-persist' not in payload
        assert cur.execute("SELECT COUNT(*) FROM audit_log WHERE table_name='import_review_recovery'").fetchone()[0] == 2
    assert b'Fictional recovery paper' not in dbc.DATABASE_PATH.read_bytes()
    assert JournalEntry.count(client_id) == 0
    with pytest.raises(drafts.ReviewConflict):
        recovery.load(other, 'one', first)
    with pytest.raises(drafts.ReviewConflict):
        recovery.discard(client_id, 'one', expected_generation=epoch, revision=second)
    recovery.discard(client_id, 'one', expected_generation=epoch, revision=first)
    assert len(recovery.list_copies(client_id)) == 1 and drafts.summary(client_id)


def test_recovery_audit_failure_permissions_and_bound_never_erase_existing(client_id, accounts, monkeypatch):
    epoch = generation.current()
    rev = recovery.save(client_id, 'one', [row(accounts)], expected_generation=epoch)
    with monkeypatch.context() as m:
        def fail(*a, **k):
            raise OSError('synthetic audit failure')
        m.setattr(AuditLog, 'write', fail)
        with pytest.raises(OSError):
            recovery.save(client_id, 'one', [dict(row(accounts), include=True)], expected_generation=epoch)
        with pytest.raises(OSError):
            recovery.discard(client_id, 'one', expected_generation=epoch)
    assert recovery.load(client_id, 'one', rev)[0][0]['include'] is False
    for level in ('read', 'propose', 'post'):
        with monkeypatch.context() as m:
            m.setattr(dbc, 'ASSISTANT_ACCESS_LEVEL', level)
            with pytest.raises(Exception):
                recovery.save(client_id, 'two', [row(accounts)], expected_generation=epoch)
            with pytest.raises(Exception):
                recovery.discard(client_id, 'one', expected_generation=epoch)
    with monkeypatch.context() as m:
        m.setattr(dbc, 'READ_ONLY', True)
        assert recovery.list_copies(client_id)
        with pytest.raises(Exception):
            recovery.discard(client_id, 'one', expected_generation=epoch)
    monkeypatch.setattr(recovery, 'MAX_COPIES', 1)
    with pytest.raises(ValueError, match='recovery copies'):
        recovery.save(client_id, 'two', [row(accounts)], expected_generation=epoch)
    assert recovery.load(client_id, 'one', rev)
    with monkeypatch.context() as m:
        m.setattr(dbc, 'ENCRYPTION_AVAILABLE', False)
        with pytest.raises(ValueError, match='encrypted book'):
            recovery.save(client_id, 'one', [row(accounts)], expected_generation=epoch)


@pytest.mark.parametrize('boundary', ['before_commit', 'after_commit'])
def test_crash_recovery_transaction_is_atomic(client_id, accounts, tmp_path, boundary):
    original = row(accounts)
    rev = recovery.save(client_id, 'crash-window', [original], expected_generation=generation.current())
    fixture = tmp_path / 'crash.json'
    fixture.write_text(json.dumps(dict(key=dbc.get_active_key(), client_id=client_id,
        mode='recovery', boundary=boundary, row=dict(original, description='Updated fictional checkpoint')), default=str))
    from tests.helpers.review_crash_process import run_worker
    result = run_worker(fixture, dbc.DATABASE_PATH)
    assert result.returncode == 73, result.stderr
    current = recovery.list_copies(client_id)[0]['revision']
    loaded, _ = recovery.load(client_id, 'crash-window', current)
    committed = boundary == 'after_commit'
    assert (current != rev) == committed
    assert loaded[0]['description'] == ('Updated fictional checkpoint' if committed else original['description'])
    with dbc.get_cursor() as cur:
        assert cur.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert cur.execute("SELECT COUNT(*) FROM audit_log WHERE table_name='import_review_recovery'").fetchone()[0] == 1 + committed
    assert not drafts.summary(client_id) and JournalEntry.count(client_id) == 0


def test_restore_invalidates_old_save_recovery_and_post_context(client_id, accounts, tmp_path):
    epoch = generation.current()
    source = dict(row(accounts), _book_generation=epoch)
    backup = create_backup(tmp_path / 'backups')
    restore_backup(backup.database_path, tmp_path / 'backups', audit=restore_audit)
    assert generation.current() != epoch
    with pytest.raises(generation.StaleBookError):
        drafts.save(client_id, [source], expected_generation=epoch)
    with pytest.raises(generation.StaleBookError):
        recovery.save(client_id, 'one', [source], expected_generation=epoch)
    with pytest.raises(generation.StaleBookError):
        post_transaction(client_id, source, accounts['expense'], accounts['cash'])
    assert JournalEntry.count(client_id) == 0


def test_post_checks_saved_revision_inside_transaction(client_id, accounts):
    epoch = generation.current()
    context = {'generation': epoch, 'revision': None}
    rev = drafts.save(client_id, [row(accounts)])
    with pytest.raises(drafts.ReviewConflict):
        post_transaction(client_id, row(accounts), accounts['expense'], accounts['cash'], review_context=context)
    assert JournalEntry.count(client_id) == 0
    context['revision'] = rev
    post_transaction(client_id, row(accounts), accounts['expense'], accounts['cash'], review_context=context)
    assert JournalEntry.count(client_id) == 1


def test_ui_recovery_resumes_with_no_cloud_request_and_preserves_exclusion(monkeypatch, client_id, accounts, fake_credential_vault):
    from services import jev_categorization, review_categorization
    def forbidden(*a, **k):
        pytest.fail('Recovery must never request AI')
    monkeypatch.setattr(jev_categorization, 'send_request', forbidden)
    monkeypatch.setattr(review_categorization, 'send_request', forbidden)
    at, _ = page(monkeypatch, client_id, accounts, fake_credential_vault)
    at.session_state.transactions_to_review = [row(accounts)]
    at.run()
    assert not at.exception
    assert len(recovery.list_copies(client_id)) == 1 and not drafts.summary(client_id)
    revision = recovery.list_copies(client_id)[0]['revision']
    at.run()
    assert recovery.list_copies(client_id)[0]['revision'] == revision
    fresh, _ = page(monkeypatch, client_id, accounts, fake_credential_vault)
    fresh.session_state.transactions_to_review = []
    fresh.run()
    button(fresh, 'Resume recovery copy').click().run()
    assert not fresh.exception
    resumed = fresh.session_state.transactions_to_review[0]
    assert not resumed['include'] and resumed['selected_account_id'] == accounts['expense']
    assert not fresh.session_state[row_key('include', resumed)]
    assert not drafts.summary(client_id) and JournalEntry.count(client_id) == 0


def test_other_window_restore_stops_ui_until_reload(monkeypatch, client_id, accounts, fake_credential_vault, tmp_path):
    at, _ = page(monkeypatch, client_id, accounts, fake_credential_vault)
    at.run()
    before = deepcopy(at.session_state.transactions_to_review)
    backup = create_backup(tmp_path / 'backups')
    restore_backup(backup.database_path, tmp_path / 'backups', audit=restore_audit)
    at.run()
    assert not at.exception
    assert at.session_state.transactions_to_review == before
    assert not any(b.label == 'Post Transactions' for b in at.button)
    button(at, 'Reload restored book').click().run()
    assert not at.exception and not at.session_state.transactions_to_review
    assert not any(b.label == 'Reload restored book' for b in at.button)
    assert JournalEntry.count(client_id) == 0


def test_saved_conflict_needs_explicit_keep_choice(monkeypatch, client_id, accounts, fake_credential_vault):
    at, _ = page(monkeypatch, client_id, accounts, fake_credential_vault)
    at.run()
    rev = drafts.save(client_id, [row(accounts)])
    at.run()
    assert button(at, 'Post Transactions').disabled
    before = deepcopy(at.session_state.transactions_to_review)
    button(at, 'Keep this window’s review').click().run()
    assert not at.exception and not button(at, 'Post Transactions').disabled
    assert at.session_state.transactions_to_review == before
    assert at.session_state.review_saved_revision == rev
    assert JournalEntry.count(client_id) == 0


def test_close_status_is_private_boolean_only_and_dynamic(tmp_path, monkeypatch):
    from webview.event import Event
    directory, path = channel.create_channel()
    try:
        assert Path(directory.name).stat().st_mode & 0o077 == 0
        monkeypatch.setenv(channel.ENV, str(path))
        window = SimpleNamespace(events=SimpleNamespace(closing=Event(None, should_lock=True)))
        channel.register_close_guard(window, path)
        window.events.closing.set()
        assert window.confirm_close is False
        channel.publish('fictional-window', True)
        window.events.closing.set()
        assert window.confirm_close is True
        channel.publish('other', True)
        channel.publish('fictional-window', False)
        assert channel.needs_confirmation(path)
        channel.publish('other', False)
        assert not channel.needs_confirmation(path)
        assert json.loads(path.read_text()) == {'review_open': False}
        path.write_text('broken')
        assert channel.needs_confirmation(path)
    finally:
        directory.cleanup()


def test_old_readonly_schema_has_no_recovery_ui(client_id, monkeypatch):
    with dbc.get_cursor(commit=True) as cur:
        cur.execute('DROP TABLE import_review_recovery')
        cur.execute('DROP TABLE book_generation')
    monkeypatch.setattr(dbc, 'READ_ONLY', True)
    assert generation.current() is None and not recovery.list_copies(client_id)


def test_prior_book_opens_readonly_without_attempting_new_migrations(monkeypatch, client_id, accounts, fake_credential_vault):
    with dbc.get_cursor(commit=True) as cur:
        cur.execute('DROP TABLE import_review_recovery')
        cur.execute('DROP TABLE book_generation')
        cur.execute('DROP TABLE import_review_drafts')
        cur.execute("DELETE FROM schema_migrations WHERE version IN ('026_import_review_drafts','027_review_recovery')")
    monkeypatch.setattr(dbc, 'READ_ONLY', True)
    at, _ = page(monkeypatch, client_id, accounts, fake_credential_vault)
    at.session_state.transactions_to_review = []
    at.run()
    assert not at.exception
    with dbc.get_cursor() as cur:
        assert cur.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0] == 25
    assert not drafts.summary(client_id) and not recovery.list_copies(client_id)


def test_checkpoint_failure_keeps_ui_review_and_explicit_save_usable(monkeypatch, client_id, accounts, fake_credential_vault):
    def fail(*a, **k):
        raise OSError('Fictional checkpoint storage failure')
    monkeypatch.setattr(recovery, 'save', fail)
    at, _ = page(monkeypatch, client_id, accounts, fake_credential_vault)
    at.session_state.transactions_to_review = [row(accounts)]
    at.run()
    assert not at.exception and len(at.session_state.transactions_to_review) == 1
    assert any('Recovery copy could not be updated' in c.value for c in at.caption)
    button(at, 'Save review for later').click().run()
    assert not at.exception and not drafts.load(client_id)[1][0]['include']
    assert JournalEntry.count(client_id) == 0
