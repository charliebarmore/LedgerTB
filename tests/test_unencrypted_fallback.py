import pytest

import config
import mcp_server
import run_ledgertb
from database import connection as dbconn
from utils import unlock


MESSAGE = config.UNENCRYPTED_REFUSAL_MESSAGE


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


def test_selfcheck_refuses_fallback_without_explicit_opt_in(monkeypatch, capsys):
    _fallback_without_opt_in(monkeypatch)

    assert run_ledgertb._selfcheck() == 1
    assert capsys.readouterr().out.strip() == MESSAGE


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
