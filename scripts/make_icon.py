"""Generate ProBooks.icns (the macOS Dock/app icon).

Draws a navy rounded-square mark with a white "PB" wordmark matching the app
theme (#1f3a5f), then builds a multi-resolution .icns via the macOS iconutil.
Reproducible: re-run `python scripts/make_icon.py` to regenerate.
"""

import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

NAVY = (31, 58, 95)        # #1f3a5f — app primaryColor
WHITE = (255, 255, 255)
ROOT = Path(__file__).resolve().parent.parent
ICNS_OUT = ROOT / "ProBooks.app" / "Contents" / "Resources" / "ProBooks.icns"

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

    text = "PB"
    font = _load_font(int(px * 0.42))
    box = d.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    d.text(((px - tw) / 2 - box[0], (px - th) / 2 - box[1]), text, font=font, fill=WHITE)
    return img


def main() -> None:
    ICNS_OUT.parent.mkdir(parents=True, exist_ok=True)
    base = _base_png(1024)

    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "ProBooks.iconset"
        iconset.mkdir()
        # macOS iconset requires these exact names/sizes.
        for size in (16, 32, 128, 256, 512):
            base.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
            base.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ICNS_OUT)], check=True)

    print(f"Wrote {ICNS_OUT}")


if __name__ == "__main__":
    main()
