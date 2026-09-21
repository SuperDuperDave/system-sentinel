#!/usr/bin/env python3
"""Draw ``social-preview.png``: the card a shared link to this repository shows.

1280x640, the identity's field, the mark drawn by the tool's own
:func:`sentinel.launcher.render_mark`, the wordmark and one line, and a crop of
``desktop-hardware-errors.png`` in a thin frame. Nothing else, and no text in the
phosphor: it is light only.

Run it from the repository root, with Pillow installed:

    ./.venv/bin/python docs/screens/make_social_preview.py

The display face is Azeret Mono where a TTF or OTF of it is on disk. Only the WOFF2
the dashboard serves is in this repository and Pillow cannot read that, so what this
draws today is DejaVu Sans Mono (or whatever ``FACE`` finds), and the card says the
identity's shapes in a stand-in face. Drop an Azeret Mono TTF or OTF into
``dashboard/public/fonts/`` and it is used without changing anything here.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sentinel.launcher import render_mark  # noqa: E402  (after the path is set)

HERE = Path(__file__).resolve().parent
OUT = HERE / "social-preview.png"
SCREEN = HERE / "desktop-hardware-errors.png"

# docs/design/IDENTITY-DIRECTIONS-2026-09-20.md owns these values.
FIELD = (0x0B, 0x1A, 0x16)
LIT = (0x16, 0x34, 0x29)
DEEP = (0x06, 0x10, 0x0D)
INK = (0xEA, 0xF3, 0xEE)
BODY = (0xC3, 0xD6, 0xCD)
GRATICULE = (0x9C, 0xFF, 0xC4)
RULE = (0xEA, 0xF3, 0xEE, 36)  # ink at .14, the identity's hairline

SIZE = (1280, 640)
GUTTER = 64
MARK = 104
WORDMARK = "System Sentinel"
LINE = "A stethoscope for a Windows computer"

#: Where a TTF or OTF of the display face would be, then what to fall back to.
FACES = [
    *sorted(ROOT.glob("dashboard/public/fonts/*.[ot]tf")),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    Path("C:/Windows/Fonts/consola.ttf"),
]


def face(size: int) -> ImageFont.FreeTypeFont:
    for path in FACES:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    raise SystemExit(f"no display face found; looked in {[str(p) for p in FACES]}")


def field(size: tuple[int, int]) -> Image.Image:
    """The ground: the field, lit towards the upper left, deep towards the lower right."""
    w, h = 160, 80
    small = Image.new("RGB", (w, h))
    pixels = small.load()
    for y in range(h):
        for x in range(w):
            u, v = x / (w - 1), y / (h - 1)
            lit = max(0.0, 1.0 - (((u - 0.10) ** 2 + (v - 0.05) ** 2) ** 0.5) / 0.95) ** 1.6
            deep = max(0.0, 1.0 - (((u - 1.0) ** 2 + (v - 1.0) ** 2) ** 0.5) / 0.85) ** 1.8
            pixels[x, y] = tuple(
                round(base + (LIT[i] - base) * lit + (DEEP[i] - base) * deep) for i, base in enumerate(FIELD)
            )
    return small.resize(size, Image.LANCZOS)


def graticule(size: tuple[int, int], step: int = 40, zone: tuple[int, int] = (620, 380)) -> Image.Image:
    """Hairline squares in one zone, the upper left, fading out before the type begins."""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for x in range(0, zone[0] + step, step):
        draw.line([(x, 0), (x, zone[1])], fill=GRATICULE + (33,))
    for y in range(0, zone[1] + step, step):
        draw.line([(0, y), (zone[0], y)], fill=GRATICULE + (33,))
    mask = Image.new("L", size)
    pixels = mask.load()
    fade = [
        [1.0 if n < extent * 0.4 else max(0.0, 1.0 - (n - extent * 0.4) / (extent * 0.6)) for n in range(length)]
        for length, extent in zip(size, zone)
    ]
    for x in range(size[0]):
        for y in range(size[1]):
            pixels[x, y] = round(255 * fade[0][x] * fade[1][y])
    layer.putalpha(Image.composite(layer.getchannel("A"), Image.new("L", size), mask))
    return layer


def panel(box: tuple[int, int, int, int]) -> Image.Image:
    """The hardware-errors screen, cropped to the panel's shape and scaled to it."""
    x, y, w, h = box
    shot = Image.open(SCREEN).convert("RGB")
    left, top = 450, 12  # the content column, just inside the dashboard's rail
    width = min(shot.width - left, 2190)
    height = min(shot.height - top, round(width * h / w))
    width = round(height * w / h)
    return shot.crop((left, top, left + width, top + height)).resize((w, h), Image.LANCZOS)


def tracked(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font, fill, tracking: float) -> None:
    """Letter-spacing, which Pillow does not do: one glyph at a time."""
    x, y = xy
    for character in text:
        draw.text((x, y), character, font=font, fill=fill)
        x += draw.textlength(character, font=font) + tracking


def build() -> Image.Image:
    card = field(SIZE).convert("RGBA")
    card.alpha_composite(graticule(SIZE))

    box = (664, 96, 552, 448)
    card.paste(panel(box), (box[0], box[1]))
    ImageDraw.Draw(card).rectangle(
        (box[0] - 1, box[1] - 1, box[0] + box[2], box[1] + box[3]), outline=RULE, width=1
    )

    draw = ImageDraw.Draw(card)
    wordmark, line = face(38), face(21)
    top = 196
    card.alpha_composite(render_mark(MARK), (GUTTER, top))
    tracked(draw, (GUTTER + 2, top + MARK + 46), WORDMARK.upper(), wordmark, INK, 38 * 0.14)
    draw.text((GUTTER + 2, top + MARK + 116), LINE, font=line, fill=BODY)
    return card.convert("RGB")


if __name__ == "__main__":
    build().save(OUT, format="PNG", optimize=True)
    print(f"{OUT.relative_to(ROOT)} {Image.open(OUT).size[0]}x{Image.open(OUT).size[1]}")
