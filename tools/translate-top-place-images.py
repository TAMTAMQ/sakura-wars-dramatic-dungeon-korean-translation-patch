#!/usr/bin/env python3
"""Render Korean top_place labels while preserving each source label's style."""

from __future__ import annotations

import argparse
import colorsys
import json
import math
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


SIZE = (112, 24)
TEXT_HEIGHT = 21


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--preview", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--only", action="append", default=[])
    return parser.parse_args()


def luminance(color: tuple[int, int, int]) -> float:
    return color[0] * 0.299 + color[1] * 0.587 + color[2] * 0.114


def saturation(color: tuple[int, int, int]) -> float:
    return colorsys.rgb_to_hsv(*(component / 255 for component in color))[1]


def blend(a: tuple[int, int, int], b: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    return tuple(round(a[index] * (1 - amount) + b[index] * amount) for index in range(3))


def style_colors(source: Image.Image, base: Image.Image) -> dict[str, tuple[int, int, int]]:
    source_pixels = list(source.convert("RGB").get_flattened_data())
    base_pixels = list(base.convert("RGB").get_flattened_data())
    counts = Counter(source_pixels)
    base_colors = set(base_pixels)
    frequent = [color for color, count in counts.items() if count >= 2]
    if len(frequent) < 3:
        frequent = list(counts)
    if not frequent:
        return {"outline": (54, 35, 25), "highlight": (248, 246, 225), "fill": (194, 82, 111)}

    outline = min(frequent, key=lambda color: (luminance(color), -counts[color]))
    highlight = max(frequent, key=lambda color: (luminance(color), counts[color]))
    low = luminance(outline) + 18
    high = luminance(highlight) - 8
    candidates = [color for color in frequent if low <= luminance(color) <= high]
    if not candidates:
        candidates = frequent
    def distance_from_base(color: tuple[int, int, int]) -> int:
        return min(
            sum((color[channel] - base_color[channel]) ** 2 for channel in range(3))
            for base_color in base_colors
        )

    fill = max(
        candidates,
        key=lambda color: (
            distance_from_base(color),
            saturation(color),
            counts[color],
        ),
    )
    return {"outline": outline, "highlight": highlight, "fill": fill}


def fitting_font(font_path: Path, text: str) -> tuple[ImageFont.FreeTypeFont, tuple[int, int, int, int], int]:
    probe = Image.new("L", SIZE)
    draw = ImageDraw.Draw(probe)
    for size in range(19, 6, -1):
        font = ImageFont.truetype(str(font_path), size)
        box = draw.textbbox((0, 0), text, font=font, stroke_width=1)
        if box[2] - box[0] <= SIZE[0] - 2 and box[3] - box[1] <= TEXT_HEIGHT:
            return font, box, size
    raise ValueError(f"Korean label does not fit: {text}")


def render_label(
    base: Image.Image,
    text: str,
    font_path: Path,
    colors: dict[str, tuple[int, int, int]],
) -> tuple[Image.Image, int]:
    font, box, font_size = fitting_font(font_path, text)
    width = box[2] - box[0]
    height = box[3] - box[1]
    x = (SIZE[0] - width) // 2 - box[0]
    y = (TEXT_HEIGHT - height) // 2 - box[1]

    fill_mask = Image.new("L", SIZE, 0)
    outer_mask = Image.new("L", SIZE, 0)
    ImageDraw.Draw(fill_mask).text((x, y), text, font=font, fill=255)
    ImageDraw.Draw(outer_mask).text(
        (x, y), text, font=font, fill=255, stroke_width=1, stroke_fill=255
    )

    output = base.convert("RGB").copy()
    outline_layer = Image.new("RGB", SIZE, colors["outline"])
    output.paste(outline_layer, mask=outer_mask)

    gradient = Image.new("RGB", SIZE)
    gradient_pixels = []
    for row in range(SIZE[1]):
        amount = max(0.10, min(1.0, (row - y) / max(height * 0.58, 1)))
        color = blend(colors["highlight"], colors["fill"], amount)
        gradient_pixels.extend([color] * SIZE[0])
    gradient.putdata(gradient_pixels)
    output.paste(gradient, mask=fill_mask)
    return output, font_size


def build_preview(cards: list[tuple[str, Image.Image, Image.Image]], output: Path) -> None:
    scale = 3
    columns = 3
    label_height = 14
    image_height = SIZE[1] * scale
    cell_width = SIZE[0] * scale
    cell_height = label_height + image_height * 2 + 4
    rows = math.ceil(len(cards) / columns)
    sheet = Image.new("RGB", (cell_width * columns, cell_height * rows), (32, 32, 32))
    draw = ImageDraw.Draw(sheet)
    for index, (name, source, korean) in enumerate(cards):
        left = index % columns * cell_width
        top = index // columns * cell_height
        draw.text((left + 2, top + 1), name, fill=(255, 255, 255))
        sheet.paste(source.resize((cell_width, image_height), Image.Resampling.NEAREST), (left, top + label_height))
        sheet.paste(korean.resize((cell_width, image_height), Image.Resampling.NEAREST), (left, top + label_height + image_height + 4))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)


def main() -> None:
    args = parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    base = Image.open(args.base).convert("RGB")
    if base.size != SIZE:
        raise SystemExit(f"Base image must be {SIZE}, got {base.size}")
    selected = set(args.only)
    records = [record for record in spec["records"] if not selected or record["file"] in selected]
    if selected - {record["file"] for record in records}:
        raise SystemExit(f"Unknown --only files: {sorted(selected - {record['file'] for record in records})}")

    args.output_root.mkdir(parents=True, exist_ok=True)
    report_records = []
    cards = []
    for record in records:
        source_path = args.source_root / record["file"]
        source = Image.open(source_path).convert("RGB")
        if source.size != SIZE:
            raise ValueError(f"Unexpected source size: {source_path} {source.size}")
        colors = style_colors(source, base)
        for key, value in record.get("style", {}).items():
            colors[key] = tuple(value)
        output, font_size = render_label(base, record["korean"], args.font, colors)
        output_path = args.output_root / record["file"]
        output.save(output_path, format="PNG", optimize=False)
        cards.append((record["file"], source, output))
        report_records.append(
            {
                **record,
                "font_size": font_size,
                "colors": {key: list(value) for key, value in colors.items()},
                "output": str(output_path.resolve()),
            }
        )

    if args.preview:
        build_preview(cards, args.preview)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {
                    "base": str(args.base.resolve()),
                    "font": str(args.font.resolve()),
                    "translated_count": len(report_records),
                    "records": report_records,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    print(json.dumps({"translated_count": len(report_records), "output_root": str(args.output_root.resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
