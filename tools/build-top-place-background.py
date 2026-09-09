#!/usr/bin/env python3
"""Build a clean common top_place background from the immutable source PNGs.

The Japanese labels differ between images while the plaque artwork is shared.
For every pixel, this script selects the most frequent RGBA value across all
top_place images.  This preserves the original pixel art without resampling.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw


EXPECTED_SIZE = (112, 24)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--preview", type=Path)
    parser.add_argument("--qa-preview", type=Path)
    parser.add_argument("--contact-sheet", type=Path)
    parser.add_argument("--contact-scale", type=int, default=2)
    parser.add_argument("--contact-columns", type=int, default=4)
    parser.add_argument("--zoom-root", type=Path)
    parser.add_argument("--tile-mode-preview", type=Path)
    parser.add_argument("--row-tile-mode-preview", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sources = sorted(args.source_root.glob("top_place_*.png"))
    if not sources:
        raise SystemExit(f"No top_place PNG files found in {args.source_root}")

    images: list[Image.Image] = []
    for path in sources:
        image = Image.open(path).convert("RGBA")
        if image.size != EXPECTED_SIZE:
            raise SystemExit(
                f"Unexpected size for {path.name}: {image.size}; expected {EXPECTED_SIZE}"
            )
        images.append(image)

    pixel_sets = [list(image.getdata()) for image in images]
    output_pixels = []
    winning_counts = []
    for pixel_index in range(EXPECTED_SIZE[0] * EXPECTED_SIZE[1]):
        counts = Counter(pixels[pixel_index] for pixels in pixel_sets)
        # RGBA tuple is a stable secondary key in the unlikely event of a tie.
        pixel, count = max(counts.items(), key=lambda item: (item[1], item[0]))
        output_pixels.append(pixel)
        winning_counts.append(count)

    background = Image.new("RGBA", EXPECTED_SIZE)
    background.putdata(output_pixels)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    background.save(args.output)

    if args.tile_mode_preview or args.row_tile_mode_preview:
        tiles_by_image = []
        for image in images:
            tiles = []
            for tile_y in range(3):
                for tile_x in range(14):
                    tiles.append(
                        tuple(
                            image.crop(
                                (tile_x * 8, tile_y * 8, tile_x * 8 + 8, tile_y * 8 + 8)
                            ).getdata()
                        )
                    )
            tiles_by_image.append(tiles)

        if args.tile_mode_preview:
            tile_mode = Image.new("RGBA", EXPECTED_SIZE)
            for tile_index in range(42):
                counts = Counter(tiles[tile_index] for tiles in tiles_by_image)
                tile = max(counts.items(), key=lambda item: (item[1], item[0]))[0]
                patch = Image.new("RGBA", (8, 8))
                patch.putdata(tile)
                tile_mode.paste(patch, ((tile_index % 14) * 8, (tile_index // 14) * 8))
            args.tile_mode_preview.parent.mkdir(parents=True, exist_ok=True)
            tile_mode.resize((448, 96), Image.Resampling.NEAREST).save(
                args.tile_mode_preview
            )

        if args.row_tile_mode_preview:
            row_tile_mode = Image.new("RGBA", EXPECTED_SIZE)
            for tile_y in range(3):
                row_tiles = [
                    tiles[tile_y * 14 + tile_x]
                    for tiles in tiles_by_image
                    for tile_x in range(1, 13)
                ]
                tile = Counter(row_tiles).most_common(1)[0][0]
                patch = Image.new("RGBA", (8, 8))
                patch.putdata(tile)
                for tile_x in range(1, 13):
                    row_tile_mode.paste(patch, (tile_x * 8, tile_y * 8))
                row_tile_mode.paste(images[0].crop((0, tile_y * 8, 8, tile_y * 8 + 8)), (0, tile_y * 8))
                row_tile_mode.paste(images[0].crop((104, tile_y * 8, 112, tile_y * 8 + 8)), (104, tile_y * 8))
            args.row_tile_mode_preview.parent.mkdir(parents=True, exist_ok=True)
            row_tile_mode.resize((448, 96), Image.Resampling.NEAREST).save(
                args.row_tile_mode_preview
            )

    if args.preview:
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        background.resize(
            (EXPECTED_SIZE[0] * 4, EXPECTED_SIZE[1] * 4),
            Image.Resampling.NEAREST,
        ).save(args.preview)

    if args.qa_preview:
        args.qa_preview.parent.mkdir(parents=True, exist_ok=True)
        scale = 4
        qa = Image.new(
            "RGBA",
            (EXPECTED_SIZE[0] * scale, EXPECTED_SIZE[1] * scale * 3),
            (0, 0, 0, 0),
        )
        for row, image in enumerate((images[0], background, images[-1])):
            qa.paste(
                image.resize(
                    (EXPECTED_SIZE[0] * scale, EXPECTED_SIZE[1] * scale),
                    Image.Resampling.NEAREST,
                ),
                (0, row * EXPECTED_SIZE[1] * scale),
            )
        qa.save(args.qa_preview)

    if args.contact_sheet:
        args.contact_sheet.parent.mkdir(parents=True, exist_ok=True)
        scale = args.contact_scale
        columns = args.contact_columns
        label_height = 14
        cell_width = EXPECTED_SIZE[0] * scale
        cell_height = label_height + EXPECTED_SIZE[1] * scale
        rows = (len(images) + columns - 1) // columns
        sheet = Image.new(
            "RGBA", (cell_width * columns, cell_height * rows), (32, 32, 32, 255)
        )
        draw = ImageDraw.Draw(sheet)
        for index, (path, image) in enumerate(zip(sources, images)):
            x = (index % columns) * cell_width
            y = (index // columns) * cell_height
            draw.text((x + 2, y + 1), path.stem, fill=(255, 255, 255, 255))
            sheet.paste(
                image.resize(
                    (EXPECTED_SIZE[0] * scale, EXPECTED_SIZE[1] * scale),
                    Image.Resampling.NEAREST,
                ),
                (x, y + label_height),
            )
        sheet.save(args.contact_sheet)

    if args.zoom_root:
        args.zoom_root.mkdir(parents=True, exist_ok=True)
        for path, image in zip(sources, images):
            image.resize(
                (EXPECTED_SIZE[0] * 8, EXPECTED_SIZE[1] * 8),
                Image.Resampling.NEAREST,
            ).save(args.zoom_root / path.name)

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "method": "per-pixel RGBA mode",
            "source_root": str(args.source_root.resolve()),
            "source_count": len(sources),
            "source_files": [path.name for path in sources],
            "size": list(EXPECTED_SIZE),
            "output": str(args.output.resolve()),
            "minimum_winning_count": min(winning_counts),
            "average_winning_ratio": round(
                sum(winning_counts) / (len(winning_counts) * len(sources)), 6
            ),
            "unanimous_pixels": sum(count == len(sources) for count in winning_counts),
            "total_pixels": len(winning_counts),
        }
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    print(f"Built {args.output} from {len(sources)} images")


if __name__ == "__main__":
    main()
