"""Scan every uncompressed CPK entry for embedded images.

tools/extract-cpk-visuals.py only looks inside entries the prior Chinese patch
changed, so images the patch left alone were never rendered. This walks all
entries the extractor can read and writes a PNG for each asset it finds.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


def load_module(name: str):
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(name.replace("-", "_").removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXTRACTOR = load_module("extract-cpk-visuals.py")
CONVERTER = EXTRACTOR.CONVERTER


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpk-manifest", type=Path, required=True)
    parser.add_argument("--extracted-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = json.loads(args.cpk_manifest.read_text(encoding="utf-8"))
    extracted_root = args.extracted_root.resolve()
    output_root = args.output_root.resolve()
    records = []
    unreadable = []

    for cpk_record in manifest["records"]:
        if cpk_record.get("compressed"):
            continue
        if cpk_record["kind"] == "adx":
            continue
        entry_id = int(cpk_record["id"])
        payload = (extracted_root / cpk_record["path"]).read_bytes()
        asset_dir = output_root / f"{entry_id:04d}"
        found = 0

        for index, (offset, asset) in enumerate(EXTRACTOR.custom_streams(payload)):
            asset_dir.mkdir(parents=True, exist_ok=True)
            binary_path = asset_dir / f"custom-{index:02d}.bin"
            binary_path.write_bytes(asset)
            try:
                image, metadata = CONVERTER.render_custom(binary_path)
            except ValueError as error:
                unreadable.append({"cpk_entry_id": entry_id, "offset": offset, "reason": str(error)})
                continue
            png_path = binary_path.with_suffix(".png")
            image.save(png_path, format="PNG", optimize=False)
            found += 1
            records.append(
                {
                    "cpk_entry_id": entry_id,
                    "asset_kind": "clut_cmap_char",
                    "asset_index": index,
                    "offset": offset,
                    "size": len(asset),
                    "png": png_path.relative_to(output_root).as_posix(),
                    **metadata,
                }
            )

        ncgr = EXTRACTOR.nns_file(payload, b"RGCN")
        nclr = EXTRACTOR.nns_file(payload, b"RLCN")
        if ncgr is not None and nclr is not None:
            asset_dir.mkdir(parents=True, exist_ok=True)
            ncgr_path = asset_dir / "sprite.NCGR"
            nclr_path = asset_dir / "sprite.NCLR"
            ncgr_path.write_bytes(ncgr[1])
            nclr_path.write_bytes(nclr[1])
            try:
                image, metadata = CONVERTER.render_ncgr(ncgr_path, nclr_path)
            except ValueError as error:
                unreadable.append({"cpk_entry_id": entry_id, "offset": ncgr[0], "reason": str(error)})
            else:
                png_path = asset_dir / "sprite.tiles.png"
                image.save(png_path, format="PNG", optimize=False)
                found += 1
                records.append(
                    {
                        "cpk_entry_id": entry_id,
                        "asset_kind": "ncgr_tile_sheet",
                        "asset_index": 0,
                        "offset": ncgr[0],
                        "size": len(ncgr[1]),
                        "png": png_path.relative_to(output_root).as_posix(),
                        **metadata,
                    }
                )

        if not found:
            unreadable.append({"cpk_entry_id": entry_id, "reason": "no readable image stream"})

    output_root.mkdir(parents=True, exist_ok=True)
    document = {
        "schema_version": 1,
        "scanned_entry_count": sum(
            1 for record in manifest["records"] if not record.get("compressed") and record["kind"] != "adx"
        ),
        "image_count": len(records),
        "entries_with_images": sorted({record["cpk_entry_id"] for record in records}),
        "unreadable": unreadable,
        "records": records,
    }
    (output_root / "manifest.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in document.items() if k not in ("records", "unreadable")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
