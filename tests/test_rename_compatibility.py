"""Compatibility barriers for the ProBooks-to-LedgerTB product rename."""

from pathlib import Path

import config
from database.crypto import derive_key


def test_new_environment_name_wins_and_legacy_name_falls_back(monkeypatch):
    monkeypatch.setenv("PROBOOKS_SAMPLE", "legacy")
    monkeypatch.delenv("LEDGERTB_SAMPLE", raising=False)
    assert config.app_env("SAMPLE") == "legacy"

    monkeypatch.setenv("LEDGERTB_SAMPLE", "current")
    assert config.app_env("SAMPLE") == "current"


def test_user_data_directory_selection_preserves_existing_install(tmp_path):
    current = tmp_path / "LedgerTB"
    legacy = tmp_path / "ProBooks"
    current.mkdir()
    legacy.mkdir()

    assert config.choose_user_data_dir(current, legacy) == current
    (legacy / "books.json").write_text("{}")
    assert config.choose_user_data_dir(current, legacy) == legacy

    # Once the new location contains data it must not flip back to an old copy.
    (current / "accounting.db").touch()
    assert config.choose_user_data_dir(current, legacy) == current


def test_existing_book_key_derivation_never_changes():
    # The digest fixes the legacy salt and iteration count as a release
    # invariant. Changing branding must never make encrypted books unreadable.
    assert derive_key("LedgerTB rename regression") == (
        "5116e5ab747aab25b33ae22249b0585a6c585072e8eaefc1ba6b2aa67caa8a84"  # pragma: allowlist secret
    )


def test_windows_installer_uses_a_fresh_identity():
    """Owner's call (2026-08-10): LedgerTB installs under its own AppId — a
    clean break, not an in-place upgrade of the never-released ProBooks."""
    installer = Path(__file__).parents[1] / "scripts" / "ledgertb.iss"
    text = installer.read_text()
    assert "AppId={{8EE4B706-D4BD-4A9E-97DB-219152E5C235}" in text
    assert "8F3A1C42-6B7D-4E19-9A5C-2D4E8B1F7A30" not in text  # ProBooks id
    assert '#define AppName "LedgerTB"' in text
    assert '#define AppExeName "LedgerTB.exe"' in text
    assert "ProBooks.exe" not in text
    assert "ProBooks.lnk" not in text


def test_release_build_clears_both_signing_aliases_during_packaging():
    script = Path(__file__).parents[1] / "scripts" / "build_release.sh"
    text = script.read_text()
    assert "LEDGERTB_CODESIGN_ID= PROBOOKS_CODESIGN_ID=" in text
    assert 'codesign --force --deep --options runtime' in text


def test_frozen_app_never_writes_bytecode_inside_signed_bundle():
    launcher = Path(__file__).parents[1] / "run_ledgertb.py"
    text = launcher.read_text()
    frozen_guard = 'if getattr(sys, "frozen", False):'
    assert text.index(frozen_guard) < text.index("import pandas")
    assert "sys.dont_write_bytecode = True" in text


def test_notarization_timestamps_and_packages_the_stapled_app():
    script = Path(__file__).parents[1] / "scripts" / "notarize.sh"
    text = script.read_text()
    assert "codesign --force --deep --timestamp --options runtime" in text
    assert text.index("xcrun stapler staple") < text.rindex(
        'ditto -c -k --keepParent "$APP" "$ZIP"'
    )
