import plistlib
from pathlib import Path

BUNDLE_INFO = Path(__file__).resolve().parent.parent / "LedgerTB.app" / "Contents" / "Info.plist"


def test_bundle_prefers_arm64_over_x86_64():
    """LaunchServices starts a script-only bundle under Rosetta unless the
    Info.plist names an architecture order. An x86_64-first order sends the
    launcher back under translation, where the arm64 wheels in a repo venv
    cannot load and every page dies on `import pandas`.
    """
    with BUNDLE_INFO.open("rb") as handle:
        info = plistlib.load(handle)

    order = info["LSArchitecturePriority"]

    assert "arm64" in order, "the native slice must be offered"
    assert "x86_64" in order, "Intel Macs must still be able to launch"
    assert order.index("arm64") < order.index("x86_64"), (
        "arm64 must come first, or Apple Silicon launches under Rosetta"
    )
