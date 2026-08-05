"""Mark-of-the-web detection for the frozen Windows build.

Windows tags every file extracted from a downloaded zip with an NTFS
Zone.Identifier stream naming the Internet zone. The .NET Framework then
refuses to load the bundled Python.Runtime.dll, pywebview's Windows backend
cannot start, and ProBooks dies with a traceback about clr_loader before any
window appears -- observed on clean Windows 11, 2026-08-05.

The installer sidesteps this entirely. These tests cover the detection that
turns the remaining zip case into a sentence the user can act on.
"""

import os
import sys

import pytest

import run_probooks

windows_only = pytest.mark.skipif(
    os.name != "nt", reason="alternate data streams are an NTFS/Windows feature"
)


@windows_only
def test_is_blocked_reads_an_internet_zone_tag(tmp_path):
    target = tmp_path / "downloaded.dll"
    target.write_bytes(b"not really a dll")
    # This is exactly what Explorer writes when it extracts a downloaded zip.
    with open(f"{target}:Zone.Identifier", "w", encoding="utf-8") as fh:
        fh.write("[ZoneTransfer]\nZoneId=3\nReferrerUrl=https://example.invalid/x.zip\n")

    assert run_probooks._is_blocked(target)


@windows_only
def test_is_blocked_ignores_a_local_file(tmp_path):
    target = tmp_path / "local.dll"
    target.write_bytes(b"not really a dll")

    assert not run_probooks._is_blocked(target)


@windows_only
def test_is_blocked_ignores_a_trusted_zone_tag(tmp_path):
    """ZoneId=1 is the local intranet -- a file from a firm's shared drive.
    Firm mode puts book files on exactly such a drive, so this must not be
    mistaken for an internet download."""
    target = tmp_path / "from_share.dll"
    target.write_bytes(b"not really a dll")
    with open(f"{target}:Zone.Identifier", "w", encoding="utf-8") as fh:
        fh.write("[ZoneTransfer]\nZoneId=1\n")

    assert not run_probooks._is_blocked(target)


@windows_only
def test_is_blocked_catches_the_untrusted_zone(tmp_path):
    """Zone 4 is an untrusted site. .NET refuses it for the same reason it
    refuses zone 3, so it must not slip through."""
    target = tmp_path / "untrusted.dll"
    target.write_bytes(b"not really a dll")
    with open(f"{target}:Zone.Identifier", "w", encoding="utf-8") as fh:
        fh.write("[ZoneTransfer]\nZoneId=4\n")

    assert run_probooks._is_blocked(target)


@windows_only
def test_is_blocked_survives_a_malformed_tag(tmp_path):
    target = tmp_path / "weird.dll"
    target.write_bytes(b"not really a dll")
    with open(f"{target}:Zone.Identifier", "w", encoding="utf-8") as fh:
        fh.write("[ZoneTransfer]\nZoneId=not-a-number\n")

    assert not run_probooks._is_blocked(target)


def test_is_blocked_survives_a_missing_file(tmp_path):
    assert not run_probooks._is_blocked(tmp_path / "does_not_exist.dll")


def test_running_from_source_is_never_reported_as_blocked():
    """The check is about a frozen bundle's own files. Running from source
    (the dev and test path) must always be a no-op."""
    assert not getattr(sys, "frozen", False)
    assert not run_probooks._webview_blocked_by_windows()


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("Failed to resolve Python.Runtime.Loader.Initialize from "
                     r"C:\x\_internal\pythonnet\runtime\Python.Runtime.dll"),
        ImportError("cannot load clr_loader"),
    ],
)
def test_clr_failures_are_recognised(exc):
    assert run_probooks._looks_like_clr_failure(exc)


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("journal entry does not balance"),
        OSError("port 8501 already in use"),
    ],
)
def test_unrelated_failures_are_not_swallowed(exc):
    """The backstop must not turn an ordinary crash into a misleading
    'Windows blocked this' message."""
    assert not run_probooks._looks_like_clr_failure(exc)


def test_blocked_message_tells_the_user_what_to_do():
    """Per the project's writing rule: say what happened and what to do, not
    what module failed."""
    msg = run_probooks._BLOCKED_MESSAGE
    assert "Unblock" in msg
    assert "installer" in msg.lower()
    for jargon in ("clr", "Python.Runtime", "assembly", "traceback", ".NET"):
        assert jargon not in msg
