"""Generate the in-app LedgerTB wordmark (assets/ledgertb-wordmark.png).

The brand mark (branding/BRAND.md: the T-account that ties) followed by
"LedgerTB" — "Ledger" in Deep Teal, "TB" in Signal Orange — in Georgia,
the brand's serif fallback for Playfair Display. Rendered at 4x for crisp
scaling; st.logo displays it at ~32px tall on the light sidebar. Also writes
assets/ledgertb-mark.png (the square mark alone) for the collapsed sidebar.
Reproducible: re-run `python scripts/make_wordmark.py` to regenerate.
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_brand_assets import draw_mark

TEAL = (29, 67, 78)        # #1D434E Deep Teal
ORANGE = (232, 145, 58)    # #E8913A Signal Orange
ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _wordmark(height: int = 128) -> Image.Image:
    mark = draw_mark(height)
    font = _load_font(int(height * 0.52))

    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    ledger_box = probe.textbbox((0, 0), "Ledger", font=font)
    full_box = probe.textbbox((0, 0), "LedgerTB", font=font)
    ledger_w = ledger_box[2] - ledger_box[0]
    full_w = full_box[2] - full_box[0]
    text_h = full_box[3] - full_box[1]

    gap = int(height * 0.22)
    img = Image.new("RGBA", (height + gap + full_w + 4, height), (0, 0, 0, 0))
    img.paste(mark, (0, 0), mark)
    d = ImageDraw.Draw(img)
    x = height + gap - full_box[0]
    y = (height - text_h) / 2 - full_box[1]
    d.text((x, y), "Ledger", font=font, fill=TEAL)
    d.text((x + ledger_w, y), "TB", font=font, fill=ORANGE)
    return img


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    _wordmark(128).save(ASSETS / "ledgertb-wordmark.png")
    draw_mark(128).save(ASSETS / "ledgertb-mark.png")
    print(f"Wrote {ASSETS / 'ledgertb-wordmark.png'} and {ASSETS / 'ledgertb-mark.png'}")


if __name__ == "__main__":
    main()
