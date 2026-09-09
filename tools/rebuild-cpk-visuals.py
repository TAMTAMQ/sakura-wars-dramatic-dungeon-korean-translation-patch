#!/usr/bin/env python3
"""Reinsert edited fixed-size PNG sub-assets into faCpkData.cpk."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path


REBUILDER_PATH = Path(__file__).with_name("rebuild-visual-assets.py")
SPEC = importlib.util.spec_from_file_location("rebuild_visual_assets", REBUILDER_PATH)
assert SPEC is not None and SPEC.loader is not None
REBUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REBUILDER)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpk", type=Path, required=True)
    parser.add_argument("--cpk-manifest", type=Path, required=True)
    parser.add_argument("--visual-manifest", type=Path, required=True)
    parser.add_argument("--visual-root", type=Path, required=True)
    parser.add_argument("--edit-root", type=Path)
    parser.add_argument("--visual-index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--update-index", action="store_true")
    return parser.parse_args()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    args = parse_args()
    cpk = args.cpk.read_bytes()
    rebuilt = bytearray(cpk)
    cpk_manifest = json.loads(args.cpk_manifest.read_text(encoding="utf-8"))
    visual_manifest = json.loads(args.visual_manifest.read_text(encoding="utf-8"))
    cpk_entries = {int(record["id"]): record for record in cpk_manifest["records"]}
    visual_root = args.visual_root.resolve()
    edit_root = args.edit_root.resolve() if args.edit_root else visual_root / "source"
    applied = []
    for record in visual_manifest["records"]:
        if record["asset_kind"] != "clut_cmap_char" or not record["changed_byte_count"]:
            continue
        entry_id = int(record["cpk_entry_id"])
        asset_index = int(record["asset_index"])
        asset_dir = visual_root / "source" / f"{entry_id:04d}"
        binary_path = asset_dir / f"custom-{asset_index:02d}.bin"
        png_path = edit_root / f"{entry_id:04d}" / f"custom-{asset_index:02d}.png"
        source_asset = binary_path.read_bytes()
        replacement = REBUILDER.rebuild_custom(binary_path, png_path)
        if len(replacement) != len(source_asset):
            raise ValueError(f"CPK nested asset size changed: {entry_id}/{asset_index}")
        if replacement == source_asset:
            continue
        entry = cpk_entries[entry_id]
        absolute_offset = int(entry["offset"]) + int(record["offset"])
        if bytes(rebuilt[absolute_offset : absolute_offset + len(source_asset)]) != source_asset:
            raise ValueError(f"CPK nested source precondition mismatch: {entry_id}/{asset_index}")
        rebuilt[absolute_offset : absolute_offset + len(source_asset)] = replacement
        applied.append({
            "cpk_entry_id": entry_id,
            "asset_index": asset_index,
            "absolute_offset": absolute_offset,
            "size": len(replacement),
            "png_path": str(png_path),
        })
    rebuilt_bytes = bytes(rebuilt)
    if applied:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(rebuilt_bytes)
        if args.update_index:
            index = json.loads(args.visual_index.read_text(encoding="utf-8"))
            asset_root = args.visual_index.resolve().parent
            output = args.output.resolve()
            if not output.is_relative_to(asset_root):
                raise ValueError("--update-index requires output inside the visual asset directory")
            record = next(item for item in index["records"] if item["path"] == "cpk/faCpkData.cpk")
            replacement_path = output.relative_to(asset_root).as_posix()
            digest = sha256(rebuilt_bytes)
            # Same rule as the NitroFS rebuilder: identical bytes keep whatever
            # approval the record already carries.
            unchanged_replacement = (
                record.get("replacement_sha256") == digest
                and record.get("replacement_path") == replacement_path
            )
            record["translation_status"] = "translated"
            if not unchanged_replacement:
                record["review_status"] = "in_progress"
            record["replacement_path"] = replacement_path
            record["replacement_sha256"] = digest
            args.visual_index.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "schema_version": 1,
        "source_cpk_sha256": sha256(cpk),
        "output_cpk_sha256": sha256(rebuilt_bytes),
        "applied_nested_asset_count": len(applied),
        "output_written": bool(applied),
        "index_updated": bool(applied and args.update_index),
        "applied": applied,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
