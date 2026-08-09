from utils.assistant_access import (
    LEGACY_SECRET_NAMES,
    credential_names,
    revoke_legacy_credentials,
)


def test_credential_names_are_stable_per_canonical_book_path(tmp_path):
    book_a = tmp_path / "folder" / ".." / "book-a.db"
    same_book = tmp_path / "book-a.db"
    book_b = tmp_path / "book-b.db"

    assert credential_names(book_a) == credential_names(same_book)
    assert credential_names(book_a) != credential_names(book_b)
    assert str(tmp_path) not in credential_names(book_a).key


def test_legacy_machine_wide_authorization_is_revoked(fake_credential_vault):
    from utils.secure_store import set_secret

    for name in LEGACY_SECRET_NAMES:
        set_secret(name, "legacy")

    assert revoke_legacy_credentials() is True
    assert not any(name in fake_credential_vault for name in LEGACY_SECRET_NAMES)
    assert revoke_legacy_credentials() is False
