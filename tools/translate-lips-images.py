#!/usr/bin/env python3
"""Render Korean Mulmaru glyphs into the fixed LIPS NCER cell images."""

from __future__ import annotations

import argparse
import html
import importlib.util
import json
from pathlib import Path, PurePosixPath

from PIL import Image, ImageDraw, ImageFont


def load_converter():
    path = Path(__file__).with_name("convert-visual-assets.py")
    spec = importlib.util.spec_from_file_location("convert_visual_assets", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def luminance(color: tuple[int, int, int, int]) -> int:
    return color[0] * 299 + color[1] * 587 + color[2] * 114


def style_colors(template: Image.Image) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    colors = {color for color in template.convert("RGBA").get_flattened_data() if color[3] >= 128}
    if not colors:
        return (0, 0, 0, 255), (238, 238, 238, 255)
    return min(colors, key=luminance), max(colors, key=luminance)


def fitting_font(path: Path, text: str, maximum: tuple[int, int]) -> tuple[ImageFont.FreeTypeFont, tuple[int, int, int, int]]:
    probe = Image.new("L", maximum)
    draw = ImageDraw.Draw(probe)
    for size in range(12, 5, -1):
        font = ImageFont.truetype(str(path), size)
        box = draw.textbbox((0, 0), text, font=font, stroke_width=1)
        if box[2] - box[0] <= maximum[0] - 1 and box[3] - box[1] <= maximum[1] - 1:
            return font, box
    raise ValueError(f"text does not fit LIPS cell: {text!r}")


def render_cell(template: Image.Image, text: str, font_path: Path) -> Image.Image:
    template = template.convert("RGBA")
    if not text:
        return Image.new("RGBA", template.size, (0, 0, 0, 0))
    outline, fill = style_colors(template)
    font, box = fitting_font(font_path, text, template.size)
    width, height = box[2] - box[0], box[3] - box[1]
    x = (template.width - width) // 2 - box[0]
    y = (template.height - height) // 2 - box[1]
    output = Image.new("RGBA", template.size, (0, 0, 0, 0))
    ImageDraw.Draw(output).text(
        (x, y), text, font=font, fill=fill, stroke_width=1, stroke_fill=outline
    )
    pixels = []
    for red, green, blue, alpha in output.get_flattened_data():
        pixels.append((red, green, blue, 255) if alpha >= 128 else (0, 0, 0, 0))
    output.putdata(pixels)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--edit-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--preview-root", type=Path, required=True)
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite edit files that already exist (they may be hand drawn)",
    )
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    records = {record["source_path"]: record for record in manifest["records"]}
    converter = load_converter()
    edited = []
    skipped = []
    cards = []
    for translation in spec["records"]:
        record = records[translation["path"]]
        cells = {int(index): text for index, text in translation["cells"].items()}
        for relative, metadata in zip(record["cell_png_paths"], record["cells"], strict=True):
            cell_index = int(metadata["index"])
            base_index = cell_index - 16 if cell_index >= 16 else cell_index
            base_index -= base_index % 2
            if base_index not in cells:
                continue
            path = args.edit_root / PurePosixPath(relative)
            template = args.source_root / PurePosixPath(relative)
            # An edit file that is already there may have been drawn by hand,
            # and rendering over it would throw that work away.
            if path.exists() and not args.force:
                skipped.append(str(path))
                continue
            image = render_cell(Image.open(template), cells[base_index], args.font)
            image.save(path, format="PNG", optimize=False)
            edited.append(str(path))
        images = [Image.open(args.edit_root / PurePosixPath(path)).convert("RGBA")
                  for path in record["cell_png_paths"]]
        preview = converter.contact_sheet(images, record["cells"])
        preview_relative = PurePosixPath(translation["path"]).with_suffix(".cells.png")
        preview_path = args.preview_root / preview_relative
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview.save(preview_path, format="PNG", optimize=False)
        source_phrases = " / ".join(translation["source_phrases"])
        korean_phrases = " / ".join(translation["korean_phrases"])
        relative_href = preview_path.relative_to(args.preview_root).as_posix()
        cards.append(
            f"<section><h2>{html.escape(translation['path'])}</h2>"
            f"<p>{html.escape(source_phrases)} → {html.escape(korean_phrases)}</p>"
            f"<a href='{html.escape(relative_href)}'><img src='{html.escape(relative_href)}'></a></section>"
        )
    args.preview_root.mkdir(parents=True, exist_ok=True)
    (args.preview_root / "index.html").write_text(
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>LIPS 한국어 미리보기</title>"
        "<style>body{font-family:Malgun Gothic,sans-serif;background:#202124;color:#eee;margin:24px}"
        "section{background:#303134;margin:16px 0;padding:16px;border-radius:8px}"
        "img{image-rendering:pixelated;max-width:100%;height:auto;background:#111}</style></head>"
        "<body><h1>LIPS 한국어 미리보기</h1>" + "".join(cards) + "</body></html>\n",
        encoding="utf-8",
    )
    print(json.dumps({"translated_set_count": len(spec["records"]), "edited_cell_count": len(edited),
                      "skipped_existing_count": len(skipped),
                      "preview_root": str(args.preview_root.resolve())}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
