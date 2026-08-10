"""Generate LedgerTB.icns (macOS) and LedgerTB.ico (Windows) app icons.

Draws the LedgerTB brand mark — the T-account that ties, per
branding/BRAND.md — via the shared geometry in make_brand_assets.py.
The .icns uses the full mark (Dock sizes); the .ico uses the simplified
variant (T + double rule), which stays legible at taskbar sizes where the
column entries would blur. Reproducible: re-run `python scripts/make_icon.py`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_brand_assets import draw_mark

ROOT = Path(__file__).resolve().parent.parent
ICNS_OUT = ROOT / "LedgerTB.app" / "Contents" / "Resources" / "LedgerTB.icns"
ICO_OUT = ROOT / "LedgerTB.ico"


def main() -> None:
    ICNS_OUT.parent.mkdir(parents=True, exist_ok=True)
    # Pillow writes the complete modern ICNS size set. This is also portable;
    # macOS 26's iconutil rejects even iconsets it just extracted itself.
    draw_mark(1024).save(ICNS_OUT, format="ICNS")
    draw_mark(256, simplified=True).save(
        ICO_OUT,
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"Wrote {ICNS_OUT}")
    print(f"Wrote {ICO_OUT}")


if __name__ == "__main__":
    main()
