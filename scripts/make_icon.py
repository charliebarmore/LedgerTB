"""Generate LedgerTB.icns (macOS) and LedgerTB.ico (Windows) app icons.

Draws a navy rounded-square mark with a white "LT" wordmark matching the app
theme (#1f3a5f), then builds multi-resolution .icns and .ico files via Pillow.
Reproducible: re-run `python scripts/make_icon.py` on macOS, Windows, or Linux.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

NAVY = (31, 58, 95)        # #1f3a5f — app primaryColor
WHITE = (255, 255, 255)
ROOT = Path(__file__).resolve().parent.parent
ICNS_OUT = ROOT / "LedgerTB.app" / "Contents" / "Resources" / "LedgerTB.icns"
ICO_OUT = ROOT / "LedgerTB.ico"

_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _base_png(px: int = 1024) -> Image.Image:
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = int(px * 0.08)
    radius = int(px * 0.22)
    d.rounded_rectangle([pad, pad, px - pad, px - pad], radius=radius, fill=NAVY)

    text = "LT"
    font = _load_font(int(px * 0.42))
    box = d.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    d.text(((px - tw) / 2 - box[0], (px - th) / 2 - box[1]), text, font=font, fill=WHITE)
    return img


def main() -> None:
    ICNS_OUT.parent.mkdir(parents=True, exist_ok=True)
    base = _base_png(1024)
    # Pillow writes the complete modern ICNS size set. This is also portable;
    # macOS 26's iconutil rejects even iconsets it just extracted itself.
    base.save(ICNS_OUT, format="ICNS")
    base.save(ICO_OUT, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

    print(f"Wrote {ICNS_OUT}")
    print(f"Wrote {ICO_OUT}")


if __name__ == "__main__":
    main()
