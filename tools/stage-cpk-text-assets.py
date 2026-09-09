"""Copy the CPK graphics that carry Japanese text into the versioned asset tree.

The extraction under `work/` is regenerable, so translation work needs its own
copy. This stages the editable PNGs and records where each one came from, which
is what putting the edit back needs: the CPK entry, the offset of the graphic
inside the decompressed payload, and the hashes both ends have to match.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

# Confirmed text-bearing sprite banks. `entries` lists every CPK entry that
# carries the same graphic, because a copy left untranslated still shows up in
# the game. `name` becomes the PNG stem the translator works on.
TEXT_ASSETS = [
    {
        "name": "prompt-touch-pen",
        "entries": [1179],
        "graphic": "01-000180",
        "note": "タッチペンの準備をしてね!",
    },
    {
        "name": "episode-2-matenrou-no-majin-sprite",
        "entries": [1540],
        "graphic": "05-04f060",
        "note": "第二話 摩天楼の魔人",
    },
    {
        "name": "title-logo",
        "entries": [1546, 1547, 1548],
        "graphic": {1546: "01-000280", 1547: "01-0002c0", 1548: "01-0002e0"},
        "note": "메인 화면 로고 + PRESS START BUTTON (세 항목이 같은 그림)",
    },
    {"name": "subtitle-1559", "entries": [1559], "graphic": None, "note": "컷신 자막"},
    {"name": "subtitle-1560", "entries": [1560], "graphic": None, "note": "컷신 자막"},
    {"name": "subtitle-1561", "entries": [1561], "graphic": None, "note": "컷신 자막"},
    {"name": "subtitle-1562", "entries": [1562], "graphic": None, "note": "컷신 자막"},
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--extraction-root", type=Path, required=True)
    parser.add_argument("--extraction-report", type=Path, required=True)
    parser.add_argument("--decompressed-root", type=Path, required=True)
    parser.add_argument("--cpk-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--force",
        action="store_true",
        help="restage over PNGs that already exist (they may be translated)",
    )
    return parser.parse_args()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    args = parse_args()
    report = json.loads(args.extraction_report.read_text(encoding="utf-8"))
    cpk = json.loads(args.cpk_manifest.read_text(encoding="utf-8"))
    slots = {int(record["id"]): int(record["file_size"]) for record in cpk["records"]}
    by_entry: dict[int, list[dict]] = {}
    for record in report["records"]:
        by_entry.setdefault(int(record["cpk_entry_id"]), []).append(record)

    args.output_root.mkdir(parents=True, exist_ok=True)
    staged = []
    for wanted in TEXT_ASSETS:
        lead_entry = int(wanted["entries"][0])
        graphics = by_entry.get(lead_entry, [])
        wanted_graphic = wanted["graphic"]
        if isinstance(wanted_graphic, dict):
            graphics = [g for g in graphics if Path(g["graphic"]).stem == wanted_graphic[lead_entry]]
        elif wanted_graphic is not None:
            graphics = [g for g in graphics if Path(g["graphic"]).stem == wanted_graphic]
        if not graphics:
            raise ValueError(f"no extracted graphic for CPK entry {lead_entry}")

        for graphic_index, graphic in enumerate(graphics):
            source_path = args.extraction_root / graphic["graphic"]
            cells_source = source_path.with_suffix(".cells")
            cells = sorted(cells_source.glob("cell-*.png"))
            files = []
            for cell in cells:
                cell_index = int(cell.stem.split("-")[1])
                if len(graphics) > 1 and len(cells) > 1:
                    suffix = f"-{graphic_index:02d}-{cell_index:02d}"
                elif len(graphics) > 1:
                    suffix = f"-{graphic_index:02d}"
                elif len(cells) > 1:
                    suffix = f"-{cell_index:02d}"
                else:
                    suffix = ""
                name = f"{wanted['name']}{suffix}.png"
                staged = args.output_root / name
                # Staging copies the untranslated export, so overwriting an
                # existing PNG would undo a translated one.
                if not staged.exists() or args.force:
                    shutil.copy2(cell, staged)
                files.append({"png": name, "cell": cell_index})
            sheet = source_path.with_suffix(".tiles.png")
            sheet_name = None
            if sheet.exists():
                sheet_name = f"{wanted['name']}-tiles.png"
                staged_sheet = args.output_root / sheet_name
                if not staged_sheet.exists() or args.force:
                    shutil.copy2(sheet, staged_sheet)

            targets = []
            for entry in wanted["entries"]:
                stem = wanted_graphic[entry] if isinstance(wanted_graphic, dict) else source_path.stem
                target_path = args.extraction_root / f"{entry:04d}" / f"{stem}.NCGR"
                payload = (args.decompressed_root / f"{entry:04d}.bin").read_bytes()
                targets.append(
                    {
                        "cpk_entry_id": entry,
                        "cpk_slot_size": slots[entry],
                        "payload_sha256": sha256(payload),
                        "graphic_offset": int(stem.split("-")[1], 16),
                        "graphic_sha256": sha256(target_path.read_bytes()),
                    }
                )

            staged.append(
                {
                    # One record per graphic, named after the PNG the translator
                    # edits, so a manifest entry is never ambiguous.
                    "name": Path(files[0]["png"]).stem if len(files) == 1 else wanted["name"],
                    "group": wanted["name"],
                    "note": wanted["note"],
                    "files": files,
                    "tile_sheet": sheet_name,
                    "palette": Path(graphic["palette"]).name,
                    "cell_bank": Path(graphic["cell_bank"]).name if graphic["cell_bank"] else None,
                    "has_hidden_pixels": bool(graphic.get("has_hidden_pixels")),
                    "targets": targets,
                    "translation_status": "untranslated",
                    "review_status": "untranslated",
                }
            )

    document = {
        "schema_version": 1,
        "source_profile_id": "ys9j-rev0-e6cafe64",
        "note": "Edit the cell PNGs. tiles.png appears only where objects overlap and part of the picture cannot be read back.",
        "record_count": len(staged),
        "records": staged,
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "graphics": len(staged),
                "png_count": sum(len(record["files"]) for record in staged),
                "cpk_entries": sorted({t["cpk_entry_id"] for r in staged for t in r["targets"]}),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
