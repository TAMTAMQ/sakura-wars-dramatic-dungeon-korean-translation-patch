"""Redraw the Japanese line on a sprite cell in Korean.

The cells are paletted artwork, so the text is drawn without anti-aliasing:
every pixel is either the fill colour, the outline colour, or transparent. That
keeps the picture inside the palette the game already has and avoids the fringe
colours a smoothed glyph would introduce.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def fit_font(font_path: Path, text: str, width: int, height: int, stroke: int) -> ImageFont.FreeTypeFont:
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    for size in range(height + 8, 5, -1):
        font = ImageFont.truetype(str(font_path), size)
        box = probe.textbbox((0, 0), text, font=font, stroke_width=stroke)
        if box[2] - box[0] <= width and box[3] - box[1] <= height:
            return font
    return ImageFont.truetype(str(font_path), 6)


def hard_mask(size: tuple[int, int], text: str, font: ImageFont.FreeTypeFont, position: tuple[int, int], stroke: int) -> Image.Image:
    """Render one text layer and cut it to a hard edge."""
    layer = Image.new("L", size, 0)
    ImageDraw.Draw(layer).text(position, text, font=font, fill=255, stroke_width=stroke, stroke_fill=255)
    return layer.point(lambda value: 255 if value >= 128 else 0)


def main() -> None:
    args = parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    args.output_root.mkdir(parents=True, exist_ok=True)
    report = []
    for card in plan["cards"]:
        image = Image.open(args.source_root / card["png"]).convert("RGBA")
        pixels = np.array(image)
        top, bottom = card["band"]
        pixels[top:bottom, :, 3] = 0
        canvas = Image.fromarray(pixels)

        stroke = int(card.get("stroke", 2))
        height = bottom - top
        # A cell is the bounding box of its OAM objects, and that box can be
        # wider than the columns any object actually covers. Text drawn outside
        # the covered columns has no sprite to live in and is dropped on
        # rebuild, so a card may narrow the band it is laid out in.
        left = int(card.get("text_left", 0))
        right = int(card.get("text_right", image.width))
        width = int(card.get("max_width", right - left - 4))
        font = fit_font(args.font, card["korean"], width, height, stroke)
        probe = ImageDraw.Draw(canvas)
        box = probe.textbbox((0, 0), card["korean"], font=font, stroke_width=stroke)
        x = left + (right - left - (box[2] - box[0])) // 2 - box[0]
        y = top + (height - (box[3] - box[1])) // 2 - box[1]

        outline = hard_mask(image.size, card["korean"], font, (x, y), stroke)
        body = hard_mask(image.size, card["korean"], font, (x, y), 0)
        canvas.paste(Image.new("RGBA", image.size, tuple(card["outline"]) + (255,)), mask=outline)
        canvas.paste(Image.new("RGBA", image.size, tuple(card["fill"]) + (255,)), mask=body)

        destination = args.output_root / card["png"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(destination, format="PNG")
        report.append(
            {
                "png": card["png"],
                "source": card["source"],
                "korean": card["korean"],
                "band": [top, bottom],
                "text_left": left,
                "text_right": right,
                "font_size": font.size,
                "stroke": stroke,
                "fill": card["fill"],
                "outline": card["outline"],
            }
        )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({"cards": report}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"card_count": len(report)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
