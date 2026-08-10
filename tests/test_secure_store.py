import pytest

from utils.secure_store import (
    LEGACY_SERVICE_NAME,
    SERVICE_NAME,
    delete_secret,
    get_secret,
    migrate_legacy_secret,
    set_secret,
)

pytestmark = pytest.mark.real_vault


def test_secure_store_roundtrip(monkeypatch):
    values = {}
    monkeypatch.setattr("keyring.set_password", lambda service, name, value: values.__setitem__((service, name), value))
    monkeypatch.setattr("keyring.get_password", lambda service, name: values.get((service, name)))

    set_secret("api", "secret")
    assert get_secret("api") == "secret"


def test_probooks_vault_entry_is_copied_and_remains_readable(monkeypatch):
    values = {(LEGACY_SERVICE_NAME, "api"): "existing-secret"}
    monkeypatch.setattr("keyring.get_password",
                        lambda service, name: values.get((service, name)))
    monkeypatch.setattr("keyring.set_password",
                        lambda service, name, value:
                        values.__setitem__((service, name), value))

    assert get_secret("api") == "existing-secret"
    assert values[(SERVICE_NAME, "api")] == "existing-secret"
    assert values[(LEGACY_SERVICE_NAME, "api")] == "existing-secret"


def test_deleting_secret_clears_current_and_legacy_services(monkeypatch):
    values = {
        (SERVICE_NAME, "api"): "new",
        (LEGACY_SERVICE_NAME, "api"): "old",
    }
    monkeypatch.setattr("keyring.delete_password",
                        lambda service, name: values.pop((service, name)))

    delete_secret("api")
    assert values == {}


def test_legacy_secret_is_removed_only_after_verified_migration(tmp_path, monkeypatch):
    legacy = tmp_path / "secret"
    legacy.write_text("old-secret\n")
    values = {}
    monkeypatch.setattr("keyring.set_password", lambda service, name, value: values.__setitem__((service, name), value))
    monkeypatch.setattr("keyring.get_password", lambda service, name: values.get((service, name)))

    assert migrate_legacy_secret("api", legacy) == "old-secret"
    assert not legacy.exists()


def test_failed_legacy_migration_preserves_plaintext_for_recovery(tmp_path, monkeypatch):
    legacy = tmp_path / "secret"
    legacy.write_text("old-secret\n")
    monkeypatch.setattr("keyring.get_password", lambda *args: None)
    monkeypatch.setattr("keyring.set_password", lambda *args: (_ for _ in ()).throw(RuntimeError("vault down")))

    assert migrate_legacy_secret("api", legacy) is None
    assert legacy.exists()
