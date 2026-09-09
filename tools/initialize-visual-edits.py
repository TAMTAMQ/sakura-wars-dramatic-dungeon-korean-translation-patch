#!/usr/bin/env python3
"""Seed Korean visual edit files from immutable Japanese PNG exports."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def copy_missing(source: Path, destination: Path) -> bool:
    if destination.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--visual-manifest", type=Path, required=True)
    parser.add_argument("--visual-source-root", type=Path, required=True)
    parser.add_argument("--visual-edit-root", type=Path, required=True)
    parser.add_argument("--cpk-manifest", type=Path, required=True)
    parser.add_argument("--cpk-source-root", type=Path, required=True)
    parser.add_argument("--cpk-edit-root", type=Path, required=True)
    parser.add_argument("--overwrite-prefix", default="")
    args = parser.parse_args()

    visual_manifest = json.loads(args.visual_manifest.read_text(encoding="utf-8"))
    created = []
    preserved = []
    for record in visual_manifest["records"]:
        paths = record.get("cell_png_paths") or [record["png_path"]]
        for relative in paths:
            source = args.visual_source_root / relative
            destination = args.visual_edit_root / relative
            if args.overwrite_prefix and relative.startswith(args.overwrite_prefix) and destination.exists():
                shutil.copy2(source, destination)
                bucket = created
            else:
                bucket = created if copy_missing(source, destination) else preserved
            bucket.append(str(destination))

    cpk_manifest = json.loads(args.cpk_manifest.read_text(encoding="utf-8"))
    for record in cpk_manifest["records"]:
        if record["asset_kind"] != "clut_cmap_char" or not record["changed_byte_count"]:
            continue
        relative = Path(f"{int(record['cpk_entry_id']):04d}") / f"custom-{int(record['asset_index']):02d}.png"
        source = args.cpk_source_root / relative
        destination = args.cpk_edit_root / relative
        bucket = created if copy_missing(source, destination) else preserved
        bucket.append(str(destination))

    print(json.dumps({
        "created_count": len(created),
        "preserved_existing_count": len(preserved),
        "visual_edit_root": str(args.visual_edit_root.resolve()),
        "cpk_edit_root": str(args.cpk_edit_root.resolve()),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
