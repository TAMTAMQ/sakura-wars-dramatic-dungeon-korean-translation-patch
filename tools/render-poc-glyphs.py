#!/usr/bin/env python3
"""Render the packed Hangul glyph records from a built development PoC ROM."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--label-font", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rom = args.rom.read_bytes()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    report = json.loads(args.report.read_text(encoding="utf-8"))
    item = next(
        entry for entry in inventory["files"] if entry["path"] == "font/LD937714.dat"
    )
    data = rom[int(item["start"]) : int(item["end"])]
    records = {
        data[offset : offset + 2].hex(): data[offset + 2 : offset + 20]
        for offset in range(0, len(data), 20)
    }
    mapping = report.get("mapping", report.get("font", {}).get("added_glyphs"))
    if not mapping:
        raise ValueError("PoC report contains no glyph mapping")

    scale = 10
    cell = 12 * scale
    margin = 16
    label_height = 28
    image = Image.new(
        "RGB", (margin + len(mapping) * (cell + margin), cell + label_height + margin * 2), "#d8d8d8"
    )
    draw = ImageDraw.Draw(image)
    label_font = ImageFont.truetype(str(args.label_font), 16)
    for index, (character, code) in enumerate(mapping.items()):
        payload = records[code]
        glyph = Image.new("1", (12, 12), 1)
        pixels = glyph.load()
        for y in range(12):
            for x in range(12):
                bit = y * 12 + x
                pixels[x, y] = 0 if payload[bit // 8] & (1 << (bit % 8)) else 1
        x = margin + index * (cell + margin)
        y = margin + label_height
        enlarged = glyph.resize((cell, cell), Image.Resampling.NEAREST).convert("RGB")
        image.paste(enlarged, (x, y))
        draw.rectangle((x, y, x + cell - 1, y + cell - 1), outline="black")
        draw.text((x, margin), f"{character} {code.upper()}", font=label_font, fill="black")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    image.save(temporary, format="PNG")
    temporary.replace(args.output)
    print(str(args.output.resolve()))


if __name__ == "__main__":
    main()
