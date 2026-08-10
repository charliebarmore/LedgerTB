"""Render the LedgerTB mark to PNG at standard sizes.

The mark is pure rectangle geometry, so Pillow reproduces the SVGs exactly —
no SVG rasterizer needed. Run from the repo root:

    python scripts/make_brand_assets.py

Writes into branding/logos/. The wordmark and lockups are text-based SVGs
(Playfair Display via web fonts) and are not rendered here.
"""
from pathlib import Path

from PIL import Image, ImageDraw

TEAL = "#1D434E"
ORANGE = "#E8913A"
CREAM = "#FDFCEA"

OUT = Path(__file__).resolve().parent.parent / "branding" / "logos"

# Geometry from branding/logos/mark.svg, in its 256-unit space.
FIELD_RADIUS = 56
T_RECTS = [(48, 54, 160, 20), (118, 54, 20, 118)]
ENTRY_RECTS = [
    (60, 92, 46, 12), (60, 116, 34, 12), (60, 140, 42, 12),
    (150, 92, 38, 12), (150, 116, 46, 12), (150, 140, 30, 12),
]
RULE_RECTS = [(60, 186, 136, 6), (60, 198, 136, 6)]

# favicon.svg geometry (simplified for tiny sizes).
FAV_T = [(44, 52, 168, 28), (114, 52, 28, 116)]
FAV_RULES = [(56, 188, 144, 10), (56, 206, 144, 10)]


def draw_mark(size: int, *, field: bool = True, simplified: bool = False) -> Image.Image:
    """The LedgerTB mark at any size. Shared by the icon and wordmark
    generators so every surface renders the one brand identity."""
    return _draw(size, field=field, simplified=simplified)


def _draw(size: int, *, field: bool, simplified: bool = False) -> Image.Image:
    s = size / 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def rect(x, y, w, h, color, radius):
        d.rounded_rectangle(
            [x * s, y * s, (x + w) * s, (y + h) * s],
            radius=max(1, radius * s), fill=color,
        )

    if field:
        rect(0, 0, 256, 256, TEAL, FIELD_RADIUS)
    if simplified:
        for r in FAV_T:
            rect(*r, ORANGE, 4)
        for r in FAV_RULES:
            rect(*r, CREAM, 3)
    else:
        for r in T_RECTS:
            rect(*r, ORANGE, 3)
        for r in ENTRY_RECTS:
            rect(*r, CREAM, 3)
        for r in RULE_RECTS:
            rect(*r, CREAM, 2)
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size in (1024, 512, 256, 128):
        _draw(size, field=True).save(OUT / f"mark-{size}.png")
    for size in (64, 32, 16):
        _draw(size, field=True, simplified=True).save(OUT / f"favicon-{size}.png")
    # Multi-resolution .ico for the site.
    base = _draw(256, field=True, simplified=True)
    base.save(OUT / "favicon.ico",
              sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    print(f"Wrote PNG marks and favicon.ico to {OUT}")


if __name__ == "__main__":
    main()
