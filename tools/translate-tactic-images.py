#!/usr/bin/env python3
"""Draw the leader-tactic glyphs (風林火山心体技) as their Korean readings.

Two assets carry them: the menu icons (icon/taicho, NCER cells on a framed
grey panel) and the 3D effect textures EF_STD_075_taicho_* (raw 4bpp 64x64
with a white outline). Each Korean glyph takes its colours row by row from
the original glyph, so the tint and its vertical shading survive; the icon
panel behind an erased glyph is rebuilt from the grey pixels on the same row.
"""

from __future__ import annotations

import argparse
import colorsys
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from cpk_raw_textures import digest, rebuild_texture, render_texture

READINGS = {"FU": "풍", "RIN": "림", "KA": "화", "ZAN": "산", "SHIN": "심", "TAI": "체", "GI": "기"}
# icon/taicho cells come in bright/dim pairs in this order.
ICON_ORDER = ["FU", "RIN", "KA", "ZAN", "SHIN", "GI", "TAI"]
EFFECT_BOX = (6, 6, 58, 58)
ICON_BOX = (6, 6, 26, 26)


def saturation(rgb: tuple[int, int, int]) -> float:
    return colorsys.rgb_to_hsv(*(c / 255 for c in rgb))[1]


def glyph_mask(text: str, font_path: Path, box: tuple[int, int, int, int],
               size: tuple[int, int]) -> Image.Image:
    """Render text as large as fits box, centred, as an L mask of the image size."""
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    canvas = Image.new("L", size, 0)
    for size in range(64, 6, -1):
        font = ImageFont.truetype(str(font_path), size)
        bbox = ImageDraw.Draw(canvas).textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= width and bbox[3] - bbox[1] <= height:
            break
    x = left + (width - (bbox[2] - bbox[0])) // 2 - bbox[0]
    y = top + (height - (bbox[3] - bbox[1])) // 2 - bbox[1]
    ImageDraw.Draw(canvas).text((x, y), text, font=font, fill=255)
    return canvas.point(lambda v: 255 if v >= 128 else 0)


def row_colours(image: Image.Image, pixels: set[tuple[int, int]]) -> dict[int, tuple[int, int, int]]:
    """Average colour of the given pixels on each row, filled in across gaps."""
    sums: dict[int, list[int]] = {}
    for x, y in pixels:
        r, g, b, _ = image.getpixel((x, y))
        entry = sums.setdefault(y, [0, 0, 0, 0])
        entry[0] += r
        entry[1] += g
        entry[2] += b
        entry[3] += 1
    rows = {y: (s[0] // s[3], s[1] // s[3], s[2] // s[3]) for y, s in sums.items()}
    if not rows:
        raise ValueError("glyph has no coloured pixels")
    known = sorted(rows)
    return {y: rows[min(known, key=lambda k: abs(k - y))] for y in range(image.height)}


def bbox_of(pixels: set[tuple[int, int]]) -> tuple[int, int, int, int]:
    xs = [x for x, _ in pixels]
    ys = [y for _, y in pixels]
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def effect_texture(original: Image.Image, text: str, font: Path) -> Image.Image:
    opaque = {(x, y) for y in range(original.height) for x in range(original.width)
              if original.getpixel((x, y))[3] >= 128}
    # The outline is the near-white rim; the body is everything tinted.
    body = {p for p in opaque if saturation(original.getpixel(p)[:3]) >= 0.15}
    outline_colour = max((original.getpixel(p)[:3] for p in opaque - body), key=sum, default=(255, 255, 255))
    colours = row_colours(original, body)
    # One box for every glyph: fitting each to its kanji's own extent leaves
    # 산 and 심 small, because 山 and 心 are wide and short.
    mask = glyph_mask(text, font, EFFECT_BOX, original.size)
    outline = mask.filter(ImageFilter.MaxFilter(5))
    result = Image.new("RGBA", original.size, (0, 0, 0, 0))
    for y in range(original.height):
        for x in range(original.width):
            if mask.getpixel((x, y)):
                result.putpixel((x, y), colours[y] + (255,))
            elif outline.getpixel((x, y)):
                result.putpixel((x, y), outline_colour + (255,))
    return result


def icon_cell(original: Image.Image, text: str, font: Path) -> Image.Image:
    image = original.convert("RGBA")
    # Inside the frame, the glyph is the tinted part of a grey panel.
    inner = (4, 4, image.width - 4, image.height - 4)
    glyph = {(x, y) for y in range(inner[1], inner[3]) for x in range(inner[0], inner[2])
             if image.getpixel((x, y))[3] >= 128 and saturation(image.getpixel((x, y))[:3]) >= 0.2}
    colours = row_colours(image, glyph)
    panel = {}
    for y in range(inner[1], inner[3]):
        greys = [image.getpixel((x, y))[:3] for x in range(inner[0], inner[2])
                 if (x, y) not in glyph and image.getpixel((x, y))[3] >= 128]
        panel[y] = tuple(sum(c[i] for c in greys) // len(greys) for i in range(3)) if greys else (255, 255, 255)
    for x, y in glyph:
        image.putpixel((x, y), panel[y] + (255,))
    mask = glyph_mask(text, font, ICON_BOX, image.size)
    for y in range(image.height):
        for x in range(image.width):
            if mask.getpixel((x, y)):
                image.putpixel((x, y), colours[y] + (255,))
    return image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--icon-source-root", type=Path, required=True,
                        help="work/visual-png (Japanese icon/taicho.cells)")
    parser.add_argument("--edit-root", type=Path, required=True, help="assets/visual/edits/nitrofs")
    parser.add_argument("--replacement-root", type=Path, required=True, help="assets/visual/replacements")
    parser.add_argument("--font", type=Path, required=True)
    args = parser.parse_args()
    rom = args.source.read_bytes()
    files = {item["path"]: item for item in json.loads(args.inventory.read_text(encoding="utf-8"))["files"]}
    report = {"font_sha256": digest(args.font.read_bytes()), "effects": {}, "icon_cells": 0}

    for name, text in READINGS.items():
        texture = files[f"EF_STD_075_taicho_{name}.ntft"]
        palette = files[f"EF_STD_075_taicho_{name}.ntfp"]
        raw = rom[texture["start"]:texture["end"]]
        colours = rom[palette["start"]:palette["end"]]
        original = render_texture(raw, colours, 64, 64, 4).convert("RGBA")
        png = args.edit_root / f"EF_STD_075_taicho_{name}.png"
        png.parent.mkdir(parents=True, exist_ok=True)
        effect_texture(original, text, args.font).save(png)
        rebuilt = rebuild_texture(raw, colours, png, 64, 64, 4)
        output = args.replacement_root / f"EF_STD_075_taicho_{name}.ntft"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(rebuilt)
        report["effects"][name] = {"text": text, "sha256": digest(rebuilt)}

    cell_root = args.icon_source_root / "icon" / "taicho.cells"
    edit_cells = args.edit_root / "icon" / "taicho.cells"
    edit_cells.mkdir(parents=True, exist_ok=True)
    for pair, name in enumerate(ICON_ORDER):
        for variant in (0, 1):
            cell = f"cell-{pair * 2 + variant:03d}.png"
            icon_cell(Image.open(cell_root / cell), READINGS[name], args.font).save(edit_cells / cell)
            report["icon_cells"] += 1
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
