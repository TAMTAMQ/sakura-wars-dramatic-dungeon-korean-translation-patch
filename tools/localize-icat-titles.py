from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter


TITLES = {
    "eyecatch01_00.png": ("후카가와 저택 지하", (92, 179, 126), 145),
    "eyecatch01_01.png": ("우에노 공원", (226, 142, 180), 145),
    "eyecatch01_02.png": ("후카가와 저택 지하", (92, 179, 126), 145),
    "eyecatch01_03.png": ("긴자 지하도", (205, 139, 191), 145),
    "eyecatch01_04.png": ("시부야 대공동", (226, 176, 106), 145),
    "eyecatch02_01.png": ("빌리지 지하도", (143, 202, 185), 145),
    "eyecatch02_02.png": ("센트럴 파크", (113, 192, 168), 145),
    "eyecatch02_03.png": ("마천루 타워", (147, 165, 210), 145),
    "eyecatch02_04.png": ("뉴욕", (192, 205, 218), 145),
    "eyecatch03_01.png": ("샹젤리제 지하수도", (222, 174, 145), 145),
    "eyecatch03_02.png": ("루브르 미술관", (214, 167, 190), 145),
    "eyecatch03_03.png": ("노트르담 사원", (192, 104, 91), 145),
    "eyecatch04_01.png": ("빙옥", (132, 174, 213), 145),
    "eyecatch04_02.png": ("남극", (132, 174, 213), 145),
}


def reconstruct_header(rgb: Image.Image, right: int) -> Image.Image:
    """Rebuild the title strip from adjacent untouched artwork."""
    out = rgb.copy()
    # The untouched scene directly under the strip is the best local source for
    # removing the Japanese glyphs without inventing unrelated scenery.
    donor = rgb.crop((0, 32, right, 51)).filter(ImageFilter.GaussianBlur(0.6))
    veil = Image.new("RGB", donor.size, (92, 94, 103))
    donor = Image.blend(donor, veil, 0.22)
    out.paste(donor, (0, 12))
    return out


def fit_font(font_path: Path, text: str, max_width: int) -> ImageFont.FreeTypeFont:
    for size in range(13, 9, -1):
        font = ImageFont.truetype(str(font_path), size=size)
        box = font.getbbox(text, stroke_width=0)
        if box[2] - box[0] <= max_width:
            return font
    return ImageFont.truetype(str(font_path), size=10)


def draw_title(rgb: Image.Image, text: str, fill: tuple[int, int, int], right: int, font_path: Path) -> Image.Image:
    out = reconstruct_header(rgb, right)
    font = fit_font(font_path, text, right - 8)
    # Mulmaru remains legible at NDS resolution. A dark offset shadow plus a pale
    # keyline reproduces the original eyecatch-title treatment.
    mask = Image.new("L", out.size, 0)
    md = ImageDraw.Draw(mask)
    md.text((4, 12), text, font=font, fill=255, anchor="lt")
    outer = mask.filter(ImageFilter.MaxFilter(5))
    shadow = Image.new("L", out.size, 0)
    shadow.paste(outer.filter(ImageFilter.MaxFilter(3)), (1, 1))
    out.paste((22, 26, 42), mask=shadow)
    out.paste((239, 235, 221), mask=outer)
    out.paste(fill, mask=mask)
    # A one-pixel warm highlight on the upper-left edge keeps the embossed look.
    highlight = Image.new("L", out.size, 0)
    highlight.paste(mask, (0, -1))
    highlight = Image.eval(highlight, lambda p: p // 3)
    out.paste((255, 249, 235), mask=highlight)
    out.paste(fill, mask=mask)
    return out


def make_comparison(before_dir: Path, edit_dir: Path, output: Path) -> None:
    scale = 3
    crop_box = (0, 6, 145, 38)
    rows = []
    label_font = ImageFont.load_default()
    for name in TITLES:
        before = Image.open(before_dir / name).convert("RGB").crop(crop_box).resize((435, 96), Image.Resampling.NEAREST)
        after = Image.open(edit_dir / name).convert("RGB").crop(crop_box).resize((435, 96), Image.Resampling.NEAREST)
        row = Image.new("RGB", (900, 118), "white")
        row.paste(before, (0, 20))
        row.paste(after, (465, 20))
        ImageDraw.Draw(row).text((4, 3), name, font=label_font, fill="black")
        rows.append(row)
    sheet = Image.new("RGB", (900, 118 * len(rows)), "white")
    for i, row in enumerate(rows):
        sheet.paste(row, (0, i * 118))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--edit-dir", type=Path, required=True)
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--seed-backup",
        action="store_true",
        help="treat the current edit files as pre-edit and snapshot them",
    )
    args = parser.parse_args()

    args.backup_dir.mkdir(parents=True, exist_ok=True)
    report = {"changed": [], "unchanged_outside_header": True}
    for name, (text, fill, right) in TITLES.items():
        path = args.edit_dir / name
        backup = args.backup_dir / name
        if not backup.exists():
            # The backup is what every run draws from. Snapshotting an edit file
            # that already carries a title would make this render on top of it,
            # so a missing backup has to be a deliberate choice.
            if not args.seed_backup:
                raise ValueError(
                    f"no pre-edit backup for {name}; point --backup-dir at the "
                    "untouched exports or pass --seed-backup"
                )
            shutil.copy2(path, backup)

        # Always render from the saved pre-edit file so repeated runs are stable.
        original = Image.open(backup)
        original.load()
        before_indices = original.copy()
        rgb = original.convert("RGB")
        edited_rgb = draw_title(rgb, text, fill, right, args.font)

        # Map only the edited header crop back to the image's existing palette.
        # All pixels outside this crop retain their original palette indices.
        crop_box = (0, 11, right, 32)
        crop = edited_rgb.crop(crop_box).quantize(palette=original, dither=Image.Dither.NONE)
        original.paste(crop, crop_box)
        original.save(path)

        after_indices = Image.open(path)
        before = before_indices.load()
        after = after_indices.load()
        outside_changes = 0
        changed = 0
        for y in range(original.height):
            for x in range(original.width):
                if before[x, y] != after[x, y]:
                    changed += 1
                    if not (0 <= x < right and 11 <= y < 32):
                        outside_changes += 1
        report["changed"].append({
            "file": name,
            "translation": text,
            "edit_box": [0, 11, right, 32],
            "changed_pixels": changed,
            "outside_changed_pixels": outside_changes,
        })
        report["unchanged_outside_header"] &= outside_changes == 0

    make_comparison(args.backup_dir, args.edit_dir, args.comparison)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
