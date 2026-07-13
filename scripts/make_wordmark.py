"""Generate the in-app ProBooks wordmark (assets/probooks-wordmark.png).

Same identity as the Dock icon (scripts/make_icon.py): a navy rounded-square
"PB" mark, followed by "ProBooks" in navy text. Rendered at 4x for crisp
scaling — st.logo displays it at ~32px tall. Also writes
assets/probooks-mark.png (the square mark alone) for the collapsed sidebar.
Reproducible: re-run `python scripts/make_wordmark.py` to regenerate.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

NAVY = (31, 58, 95)        # #1f3a5f — app primaryColor
WHITE = (255, 255, 255)
ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

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


def _square_mark(px: int) -> Image.Image:
    """The rounded-square PB mark, same proportions as the Dock icon."""
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    radius = int(px * 0.24)
    d.rounded_rectangle([0, 0, px - 1, px - 1], radius=radius, fill=NAVY)

    text = "PB"
    font = _load_font(int(px * 0.46))
    box = d.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    d.text(((px - tw) / 2 - box[0], (px - th) / 2 - box[1]), text, font=font, fill=WHITE)
    return img


def _wordmark(height: int = 128) -> Image.Image:
    mark = _square_mark(height)
    font = _load_font(int(height * 0.56))

    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    box = probe.textbbox((0, 0), "ProBooks", font=font)
    tw, th = box[2] - box[0], box[3] - box[1]

    gap = int(height * 0.22)
    img = Image.new("RGBA", (height + gap + tw + 4, height), (0, 0, 0, 0))
    img.paste(mark, (0, 0), mark)
    d = ImageDraw.Draw(img)
    d.text((height + gap - box[0], (height - th) / 2 - box[1]), "ProBooks",
           font=font, fill=NAVY)
    return img


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    _wordmark(128).save(ASSETS / "probooks-wordmark.png")
    _square_mark(128).save(ASSETS / "probooks-mark.png")
    print(f"Wrote {ASSETS / 'probooks-wordmark.png'} and {ASSETS / 'probooks-mark.png'}")


if __name__ == "__main__":
    main()
