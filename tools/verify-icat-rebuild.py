from __future__ import annotations

import argparse
import importlib.util
import json
import struct
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont


def load_converter(path: Path):
    spec = importlib.util.spec_from_file_location("visual_converter", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--converter", type=Path, required=True)
    parser.add_argument("--png-root", type=Path, required=True)
    parser.add_argument("--replacement-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--render-root", type=Path, required=True)
    parser.add_argument("--sheet", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    converter = load_converter(args.converter)
    args.render_root.mkdir(parents=True, exist_ok=True)
    records = []
    rows = []
    font = ImageFont.load_default()
    for desired_path in sorted(args.png_root.glob("eyecatch0*.png")):
        replacement = args.replacement_root / f"{desired_path.stem}.bin"
        source = args.source_root / f"{desired_path.stem}.bin"
        rebuilt, _ = converter.render_custom(replacement)
        rebuilt.save(args.render_root / desired_path.name)
        desired_image = Image.open(desired_path)
        desired = desired_image.convert("RGB")
        actual = rebuilt.convert("RGB")
        parsed = converter.chunks(source.read_bytes())
        palette_raw = parsed[b"CLUT"][1]
        palette = [converter.rgb555(value) for value in struct.unpack(f"<{len(palette_raw) // 2}H", palette_raw)]
        cache = {}
        desired_indices = []
        for color in desired.getdata():
            if color not in cache:
                cache[color] = min(
                    range(len(palette)),
                    key=lambda index: sum((color[channel] - palette[index][channel]) ** 2 for channel in range(3)),
                )
            desired_indices.append(cache[color])
        actual_indices = list(rebuilt.getdata())
        mismatched = 0
        outside = 0
        max_delta = 0
        for y in range(desired.height):
            for x in range(desired.width):
                position = y * desired.width + x
                a = desired.getpixel((x, y))
                b = actual.getpixel((x, y))
                if desired_indices[position] != actual_indices[position]:
                    mismatched += 1
                    max_delta = max(max_delta, *(abs(u - v) for u, v in zip(a, b)))
                    if not (0 <= x < 130 and 12 <= y < 34):
                        outside += 1
        records.append({
            "file": desired_path.name,
            "mismatched_pixels": mismatched,
            "outside_title_mismatched_pixels": outside,
            "max_channel_delta": max_delta,
        })
        left = desired.resize((512, 384), Image.Resampling.NEAREST)
        right = actual.resize((512, 384), Image.Resampling.NEAREST)
        row = Image.new("RGB", (1048, 408), "white")
        row.paste(left, (0, 24))
        row.paste(right, (536, 24))
        ImageDraw.Draw(row).text((4, 5), desired_path.name, fill="black", font=font)
        rows.append(row)

    sheet = Image.new("RGB", (1048, 408 * len(rows)), "white")
    for index, row in enumerate(rows):
        sheet.paste(row, (0, 408 * index))
    args.sheet.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.sheet)
    payload = {
        "all_exact": all(r["mismatched_pixels"] == 0 for r in records),
        "all_exact_outside_title": all(r["outside_title_mismatched_pixels"] == 0 for r in records),
        "records": records,
    }
    args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
