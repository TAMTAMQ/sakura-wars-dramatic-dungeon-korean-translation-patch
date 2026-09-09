#!/usr/bin/env python3
"""Translate the six top-screen title strips with their original color styles."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


SIZE = (96, 16)
TEXT_TOP = 1
TEXT_BOTTOM = 14


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--reference-root", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--preview", type=Path)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def blend(a: tuple[int, int, int], b: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    return tuple(round(a[i] * (1 - amount) + b[i] * amount) for i in range(3))


def clean_background(source: Image.Image, reference: Image.Image | None) -> Image.Image:
    """Recover the full-width luminous strip from both original text layouts.

    Dark glyphs occupy different positions in the two versions. A bright
    quantile across the interior rows recovers the underlying horizontal
    light profile; the original frame rows are copied verbatim.
    """
    source = source.convert("RGB")
    inputs = [source] + ([reference.convert("RGB")] if reference else [])
    profile = Image.new("RGB", (96, 1))
    for x in range(96):
        samples = [im.getpixel((x, y)) for im in inputs for y in range(3, 13)]
        samples.sort(key=lambda c: c[0] * 299 + c[1] * 587 + c[2] * 114)
        chosen = samples[int(len(samples) * .75):]
        profile.putpixel((x, 0), tuple(round(sum(c[k] for c in chosen) / len(chosen)) for k in range(3)))
    profile = profile.resize((96, 16)).filter(ImageFilter.GaussianBlur(1.6))
    output = source.copy()
    for y in range(1, 14):
        amount = {1: .15, 2: .72, 13: .68}.get(y, 1.0)
        for x in range(3, 93):
            output.putpixel((x, y), blend((24, 24, 24), profile.getpixel((x, y)), amount))
    return output


def fitting_font(path: Path, text: str):
    probe = Image.new("L", SIZE)
    draw = ImageDraw.Draw(probe)
    for size in range(12, 6, -1):
        font = ImageFont.truetype(str(path), size)
        box = draw.textbbox((0, 0), text, font=font, stroke_width=1)
        if box[2] - box[0] <= SIZE[0] - 8 and box[3] - box[1] <= TEXT_BOTTOM - TEXT_TOP:
            return font, box, size
    raise ValueError(f"Title does not fit: {text}")


def render(base: Image.Image, text: str, font_path: Path, colors):
    font, box, font_size = fitting_font(font_path, text)
    width, height = box[2] - box[0], box[3] - box[1]
    x = (SIZE[0] - width) // 2 - box[0]
    y = TEXT_TOP + (TEXT_BOTTOM - TEXT_TOP - height) // 2 - box[1]
    fill_mask = Image.new("L", SIZE, 0)
    light_mask = Image.new("L", SIZE, 0)
    ImageDraw.Draw(fill_mask).text((x, y), text, font=font, fill=255)
    ImageDraw.Draw(light_mask).text((x, y), text, font=font, fill=255, stroke_width=1, stroke_fill=255)
    output = base.copy()
    output.paste(Image.new("RGB", SIZE, colors["highlight"]), mask=light_mask)
    output.paste(Image.new("RGB", SIZE, colors["fill"]), mask=fill_mask)
    return output, font_size


def preview(cards, path: Path) -> None:
    scale = 5
    columns = 2
    cell_width = SIZE[0] * scale
    image_count = 3 if cards and cards[0][2] is not None else 2
    cell_height = 14 + SIZE[1] * scale * image_count + 4 * (image_count - 1)
    rows = math.ceil(len(cards) / columns)
    sheet = Image.new("RGB", (cell_width * columns, cell_height * rows), (32, 32, 32))
    draw = ImageDraw.Draw(sheet)
    for index, (name, source, reference, korean) in enumerate(cards):
        x = index % columns * cell_width
        y = index // columns * cell_height
        draw.text((x + 2, y + 1), name, fill="white")
        sheet.paste(source.resize((cell_width, SIZE[1] * scale), Image.Resampling.NEAREST), (x, y + 14))
        next_y = y + 18 + SIZE[1] * scale
        if reference is not None:
            sheet.paste(reference.resize((cell_width, SIZE[1] * scale), Image.Resampling.NEAREST), (x, next_y))
            next_y += 4 + SIZE[1] * scale
        sheet.paste(korean.resize((cell_width, SIZE[1] * scale), Image.Resampling.NEAREST), (x, next_y))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)


def main() -> None:
    args = parse_args()
    records = json.loads(args.spec.read_text(encoding="utf-8"))["records"]
    args.output_root.mkdir(parents=True, exist_ok=True)
    cards = []
    report_records = []
    for record in records:
        source = Image.open(args.source_root / record["file"]).convert("RGB")
        if source.size != SIZE:
            raise ValueError(f"Unexpected dimensions: {record['file']} {source.size}")
        reference = None
        if args.reference_root:
            reference = Image.open(args.reference_root / record["file"]).convert("RGB")
        base = clean_background(source, reference)
        colors = {key: tuple(value) for key, value in record["theme"].items()}
        output, font_size = render(base, record["korean"], args.font, colors)
        output.save(args.output_root / record["file"], format="PNG", optimize=False)
        cards.append((record["file"], source, reference, output))
        report_records.append({
            **record,
            "font_size": font_size,
            "colors": {key: list(value) for key, value in colors.items()},
        })
    if args.preview:
        preview(cards, args.preview)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({"translated_count": len(records), "records": report_records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"translated_count": len(records)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
