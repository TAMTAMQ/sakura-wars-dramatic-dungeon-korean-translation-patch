from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


THEMES = {
    "red": ((239, 65, 49), (255, 222, 156), (74, 16, 8)),
    "gold": ((222, 164, 65), (255, 246, 205), (82, 41, 16)),
    "blue": ((98, 164, 222), (238, 246, 255), (24, 41, 74)),
    "pink": ((213, 106, 156), (255, 230, 246), (82, 24, 49)),
    "purple": ((172, 90, 205), (246, 222, 255), (57, 24, 74)),
    "cyan": ((57, 189, 205), (222, 255, 255), (16, 74, 82)),
    "gray": ((197, 189, 180), (255, 246, 238), (65, 49, 41)),
    "green": ((107, 189, 65), (238, 255, 222), (32, 74, 24)),
}


def text_mask(size, text, font, center):
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).text(center, text, font=font, fill=255, anchor="mm")
    return mask


def fit_font(font_path: Path, text: str, max_width: int, maximum: int):
    for size in range(maximum, 6, -1):
        font = ImageFont.truetype(str(font_path), size)
        box = font.getbbox(text)
        if box[2] - box[0] <= max_width:
            return font
    return ImageFont.truetype(str(font_path), 7)


def draw_styled(image, text, font, center, fill, highlight, outline):
    mask = text_mask(image.size, text, font, center)
    outer = mask.filter(ImageFilter.MaxFilter(3))
    shadow = Image.new("L", image.size, 0)
    shadow.paste(outer, (1, 1))
    image.paste((24, 16, 16), mask=shadow)
    image.paste(outline, mask=outer)
    gradient = Image.new("RGB", image.size)
    gp = gradient.load()
    top = max(0, center[1] - 8)
    bottom = min(image.height - 1, center[1] + 7)
    for y in range(image.height):
        t = min(1.0, max(0.0, (y - top) / max(1, bottom - top)))
        color = tuple(round(highlight[i] * (1 - t) + fill[i] * t) for i in range(3))
        for x in range(image.width):
            gp[x, y] = color
    image.paste(gradient, mask=mask)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--background", type=Path, required=True)
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    args = parser.parse_args()
    args.backup_dir.mkdir(parents=True, exist_ok=True)
    records = json.loads(args.manifest.read_text(encoding="utf-8"))["records"]
    base = Image.open(args.background).convert("RGB")
    report = []
    for record in records:
        path = args.input_dir / record["file"]
        backup = args.backup_dir / record["file"]
        if not backup.exists():
            shutil.copy2(path, backup)
        if record["file"].startswith("top_place_"):
            image = base.copy()
            fill, highlight, outline = THEMES[record["theme"]]
            font = fit_font(args.font, record["korean"], 106, 11)
            draw_styled(image, record["korean"], font, (56, 12), fill, highlight, outline)
        else:
            source = Image.open(backup).convert("RGB")
            image = source.copy()
            blurred = source.filter(ImageFilter.GaussianBlur(5.0))
            image.paste(blurred.crop((0, 2, 96, 14)), (0, 2))
            theme = record["theme"].replace("screen_", "")
            screen_colors = {
                "blue": ((65, 57, 57), (255, 246, 238), (49, 41, 41)),
                "green": ((74, 65, 41), (255, 246, 213), (49, 49, 24)),
                "red": ((90, 49, 41), (255, 238, 222), (57, 24, 24)),
                "orange": ((98, 57, 41), (255, 238, 213), (65, 32, 24)),
            }
            fill, highlight, outline = screen_colors[theme]
            font = fit_font(args.font, record["korean"], 88, 9)
            draw_styled(image, record["korean"], font, (48, 8), fill, highlight, outline)
        image.save(path)
        report.append({"file": record["file"], "source": record["source"], "korean": record["korean"]})
    (args.backup_dir / "render-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"rendered": len(report)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
