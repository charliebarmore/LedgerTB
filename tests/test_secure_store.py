from utils.secure_store import get_secret, migrate_legacy_secret, set_secret


def test_secure_store_roundtrip(monkeypatch):
    values = {}
    monkeypatch.setattr("keyring.set_password", lambda service, name, value: values.__setitem__((service, name), value))
    monkeypatch.setattr("keyring.get_password", lambda service, name: values.get((service, name)))

    set_secret("api", "secret")
    assert get_secret("api") == "secret"


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
