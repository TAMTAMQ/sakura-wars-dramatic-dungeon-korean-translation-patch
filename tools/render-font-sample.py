#!/usr/bin/env python3
"""Render source main-font records under candidate 12x12 bit layouts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw


FONT_PATH = "font/LD937714.dat"
SAMPLES = ("あ", "桜", "大", "神", "！", "A", "0")
LAYOUTS = (
    ("row/msb", "row", "msb"),
    ("row/lsb", "row", "lsb"),
    ("column/msb", "column", "msb"),
    ("column/lsb", "column", "lsb"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def bitmap(payload: bytes, axis: str, bit_order: str) -> Image.Image:
    bits = []
    for value in payload:
        positions = range(7, -1, -1) if bit_order == "msb" else range(8)
        bits.extend((value >> position) & 1 for position in positions)
    if len(bits) != 144:
        raise ValueError("expected exactly 144 bitmap bits")
    image = Image.new("1", (12, 12), 1)
    pixels = image.load()
    for y in range(12):
        for x in range(12):
            index = y * 12 + x if axis == "row" else x * 12 + y
            pixels[x, y] = 0 if bits[index] else 1
    return image


def main() -> None:
    args = parse_args()
    source = args.source.read_bytes()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    item = next(entry for entry in inventory["files"] if entry["path"] == FONT_PATH)
    data = source[int(item["start"]) : int(item["end"])]
    if len(data) % 20:
        raise ValueError("main font is not made of 20-byte records")
    records = {data[offset : offset + 2]: data[offset + 2 : offset + 20] for offset in range(0, len(data), 20)}

    scale = 8
    glyph_size = 12 * scale
    label_height = 18
    margin = 12
    panel_width = margin * 2 + len(SAMPLES) * (glyph_size + margin)
    panel_height = label_height * 2 + glyph_size + margin * 2
    output = Image.new("RGB", (panel_width, panel_height * len(LAYOUTS)), "#d8d8d8")
    draw = ImageDraw.Draw(output)

    for layout_index, (title, axis, bit_order) in enumerate(LAYOUTS):
        top = layout_index * panel_height
        draw.text((margin, top + 2), title, fill="black")
        for sample_index, character in enumerate(SAMPLES):
            code = character.encode("shift_jis")
            if len(code) == 1:
                code += b"\0"
            payload = records.get(code)
            if payload is None:
                continue
            x = margin + sample_index * (glyph_size + margin)
            y = top + label_height * 2
            glyph = bitmap(payload, axis, bit_order).resize(
                (glyph_size, glyph_size), Image.Resampling.NEAREST
            )
            output.paste(glyph.convert("RGB"), (x, y))
            draw.rectangle((x, y, x + glyph_size - 1, y + glyph_size - 1), outline="black")
            draw.text((x, top + label_height), code.hex().upper(), fill="black")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    output.save(temporary, format="PNG")
    temporary.replace(args.output)
    print(str(args.output.resolve()))


if __name__ == "__main__":
    main()
