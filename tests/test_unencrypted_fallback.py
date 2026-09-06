import pytest
import os
from pathlib import Path
import subprocess
import sys

import config
import mcp_server
import run_ledgertb
from database import connection as dbconn
from utils import unlock


MESSAGE = config.UNENCRYPTED_REFUSAL_MESSAGE


def test_missing_driver_in_fresh_process_requires_opt_in(tmp_path):
    """Exercise stdlib SQLite, not only a patched flag on the SQLCipher driver."""
    code = """
import os
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
sys.modules['sqlcipher3'] = None
from database import connection as db
assert not db.ENCRYPTION_AVAILABLE
db.DATABASE_PATH = Path(sys.argv[2]) / 'demo.db'
backup = Path(sys.argv[2]) / 'backup.db'
for connect in (db.get_connection, lambda: db.open_keyed(backup)):
    try:
        connect()
    except db.DatabaseLocked:
        pass
    else:
        raise AssertionError('Database access without opt-in')
assert not db.DATABASE_PATH.exists()
assert not backup.exists()
os.environ['LEDGERTB_ALLOW_UNENCRYPTED'] = '1'
conn = db.get_connection()
conn.execute('CREATE TABLE demo (id INTEGER)')
conn.commit()
conn.close()
assert db.DATABASE_PATH.read_bytes().startswith(b'SQLite format 3')
"""
    env = dict(os.environ)
    env.pop("LEDGERTB_ALLOW_UNENCRYPTED", None)
    env.pop("PROBOOKS_ALLOW_UNENCRYPTED", None)
    # Config must not consult the real credential vault in the child either.
    env["ANTHROPIC_API_KEY"] = "unused-demo-value"
    result = subprocess.run(
        [sys.executable, "-c", code, str(Path(__file__).resolve().parents[1]), str(tmp_path)],
        env=env, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr


def _fallback_without_opt_in(monkeypatch):
    monkeypatch.setattr(dbconn, "ENCRYPTION_AVAILABLE", False)
    monkeypatch.delenv("LEDGERTB_ALLOW_UNENCRYPTED", raising=False)
    monkeypatch.delenv("PROBOOKS_ALLOW_UNENCRYPTED", raising=False)


def test_database_fallback_refuses_without_explicit_opt_in(tmp_path, monkeypatch):
    _fallback_without_opt_in(monkeypatch)
    monkeypatch.setattr(dbconn, "DATABASE_PATH", tmp_path / "demo.db")

    with pytest.raises(dbconn.DatabaseLocked) as refused:
        dbconn.get_connection()

    assert str(refused.value) == MESSAGE


def test_unlock_gate_refuses_fallback_without_explicit_opt_in(monkeypatch):
    _fallback_without_opt_in(monkeypatch)
    errors = []
    monkeypatch.setattr(unlock, "_require_local_session", lambda: None)
    monkeypatch.setattr(unlock.st, "error", errors.append)
    monkeypatch.setattr(
        unlock.st, "stop", lambda: (_ for _ in ()).throw(RuntimeError("stopped"))
    )

    with pytest.raises(RuntimeError, match="stopped"):
        unlock.require_unlock()

    assert errors == [MESSAGE]


@pytest.mark.parametrize("opt_in", [None, "1"])
def test_selfcheck_always_requires_encryption(monkeypatch, capsys, opt_in):
    _fallback_without_opt_in(monkeypatch)
    if opt_in is not None:
        monkeypatch.setenv("LEDGERTB_ALLOW_UNENCRYPTED", opt_in)

    assert run_ledgertb._selfcheck() == 1
    message = capsys.readouterr().out
    assert "SQLCipher is required for release builds" in message
    assert "demo opt-in does not apply" in message


def test_mcp_entry_refuses_fallback_without_explicit_opt_in(monkeypatch, capsys):
    _fallback_without_opt_in(monkeypatch)
    monkeypatch.setattr(
        mcp_server, "_unlock_from_vault",
        lambda: pytest.fail("vault must not be touched before refusal"),
    )

    assert mcp_server.main() == 1
    assert capsys.readouterr().err.strip() == MESSAGE


def test_opted_in_fallback_keeps_warning_and_database_access(tmp_path, monkeypatch):
    monkeypatch.setattr(dbconn, "ENCRYPTION_AVAILABLE", False)
    monkeypatch.setenv("PROBOOKS_ALLOW_UNENCRYPTED", "yes")
    monkeypatch.setattr(dbconn, "DATABASE_PATH", tmp_path / "demo.db")
    warnings = []
    monkeypatch.setattr(unlock, "_require_local_session", lambda: None)
    monkeypatch.setattr(unlock, "database_state", lambda _path: "missing")
    monkeypatch.setattr(
        unlock.st, "warning",
        lambda text, icon=None: warnings.append((text, icon)),
    )

    unlock.require_unlock()
    connection = dbconn.get_connection()
    connection.close()

    assert config.allow_unencrypted() is True
    assert warnings == [(
        "Encryption is off: the SQLCipher driver is not installed, so this "
        "database is stored unencrypted. Fine for evaluating with sample "
        "data; install `sqlcipher3` before keeping real books here.",
        "🔓",
    )]


def test_encrypted_build_ignores_unencrypted_opt_in(monkeypatch):
    monkeypatch.setattr(dbconn, "ENCRYPTION_AVAILABLE", True)
    monkeypatch.delenv("LEDGERTB_ALLOW_UNENCRYPTED", raising=False)
    monkeypatch.delenv("PROBOOKS_ALLOW_UNENCRYPTED", raising=False)
    monkeypatch.setattr(dbconn, "_active_key", None)

    with pytest.raises(dbconn.DatabaseLocked, match="unlock with the passphrase"):
        dbconn.get_connection()
