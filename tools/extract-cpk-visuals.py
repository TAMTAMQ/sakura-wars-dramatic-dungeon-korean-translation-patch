#!/usr/bin/env python3
"""Render visual sub-assets from CPK entries changed by the prior patch."""

from __future__ import annotations

import argparse
import html
import importlib.util
import json
import struct
from pathlib import Path


CONVERTER_PATH = Path(__file__).with_name("convert-visual-assets.py")
SPEC = importlib.util.spec_from_file_location("convert_visual_assets", CONVERTER_PATH)
assert SPEC is not None and SPEC.loader is not None
CONVERTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONVERTER)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpk-manifest", type=Path, required=True)
    parser.add_argument("--extracted-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


def custom_streams(data: bytes) -> list[tuple[int, bytes]]:
    result = []
    search = 0
    while True:
        start = data.find(b"CLUT", search)
        if start < 0:
            return result
        position = start
        tags = []
        try:
            while position + 8 <= len(data):
                tag = data[position : position + 4]
                size = struct.unpack_from("<I", data, position + 4)[0] * 4
                if size < 8 or position + size > len(data):
                    raise ValueError
                tags.append(tag)
                position += size
                if tag == b"END ":
                    break
            if tags[:3] == [b"CLUT", b"CMAP", b"CHAR"] and tags[-1] == b"END ":
                result.append((start, data[start:position]))
                search = position
            else:
                search = start + 4
        except (ValueError, struct.error):
            search = start + 4


def nns_file(data: bytes, magic: bytes) -> tuple[int, bytes] | None:
    start = data.find(magic)
    if start < 0 or start + 12 > len(data):
        return None
    size = struct.unpack_from("<I", data, start + 8)[0]
    if size < 16 or start + size > len(data):
        return None
    return start, data[start : start + size]


def changed_bytes(left: bytes, right: bytes) -> int:
    return sum(a != b for a, b in zip(left, right, strict=True))


def main() -> None:
    args = parse_args()
    manifest = json.loads(args.cpk_manifest.read_text(encoding="utf-8"))
    extracted_root = args.extracted_root.resolve()
    output_root = args.output_root.resolve()
    records = []
    cards = []
    for cpk_record in manifest["records"]:
        if not cpk_record.get("changed_by_prior_patch"):
            continue
        entry_id = int(cpk_record["id"])
        source = (extracted_root / cpk_record["path"]).read_bytes()
        prior = (extracted_root / cpk_record["prior_path"]).read_bytes()
        source_streams = custom_streams(source)
        prior_streams = custom_streams(prior)
        if [(offset, len(data)) for offset, data in source_streams] != [
            (offset, len(data)) for offset, data in prior_streams
        ]:
            raise ValueError(f"custom stream layout differs in CPK entry {entry_id}")
        for index, ((offset, source_asset), (_, prior_asset)) in enumerate(zip(source_streams, prior_streams, strict=True)):
            pair_paths = []
            for label, asset in (("source", source_asset), ("prior-patch", prior_asset)):
                asset_dir = output_root / label / f"{entry_id:04d}"
                asset_dir.mkdir(parents=True, exist_ok=True)
                binary_path = asset_dir / f"custom-{index:02d}.bin"
                binary_path.write_bytes(asset)
                image, metadata = CONVERTER.render_custom(binary_path)
                png_path = binary_path.with_suffix(".png")
                image.save(png_path, format="PNG", optimize=False)
                pair_paths.append(png_path.relative_to(output_root).as_posix())
            diff_count = changed_bytes(source_asset, prior_asset)
            record = {
                "cpk_entry_id": entry_id,
                "asset_kind": "clut_cmap_char",
                "asset_index": index,
                "offset": offset,
                "size": len(source_asset),
                "changed_byte_count": diff_count,
                "source_png": pair_paths[0],
                "prior_patch_png": pair_paths[1],
                **metadata,
            }
            records.append(record)
            cards.append((record, pair_paths))
        source_ncgr = nns_file(source, b"RGCN")
        source_nclr = nns_file(source, b"RLCN")
        prior_ncgr = nns_file(prior, b"RGCN")
        prior_nclr = nns_file(prior, b"RLCN")
        if all(item is not None for item in (source_ncgr, source_nclr, prior_ncgr, prior_nclr)):
            assert source_ncgr and source_nclr and prior_ncgr and prior_nclr
            pair_paths = []
            for label, ncgr_item, nclr_item in (
                ("source", source_ncgr, source_nclr),
                ("prior-patch", prior_ncgr, prior_nclr),
            ):
                asset_dir = output_root / label / f"{entry_id:04d}"
                asset_dir.mkdir(parents=True, exist_ok=True)
                ncgr_path = asset_dir / "sprite.NCGR"
                nclr_path = asset_dir / "sprite.NCLR"
                ncgr_path.write_bytes(ncgr_item[1])
                nclr_path.write_bytes(nclr_item[1])
                image, metadata = CONVERTER.render_ncgr(ncgr_path, nclr_path)
                png_path = asset_dir / "sprite.tiles.png"
                image.save(png_path, format="PNG", optimize=False)
                pair_paths.append(png_path.relative_to(output_root).as_posix())
            record = {
                "cpk_entry_id": entry_id,
                "asset_kind": "ncgr_tile_sheet",
                "asset_index": 0,
                "offset": source_ncgr[0],
                "size": len(source_ncgr[1]),
                "changed_byte_count": changed_bytes(source_ncgr[1], prior_ncgr[1]),
                "source_png": pair_paths[0],
                "prior_patch_png": pair_paths[1],
                **metadata,
            }
            records.append(record)
            cards.append((record, pair_paths))
    manifest_output = {
        "schema_version": 1,
        "record_count": len(records),
        "records": records,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "manifest.json").write_text(
        json.dumps(manifest_output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    html_cards = []
    for record, paths in cards:
        title = f"CPK {record['cpk_entry_id']} / {record['asset_kind']} {record['asset_index']}"
        html_cards.append(
            f"<section><h2>{html.escape(title)}</h2><div class='pair'>"
            f"<figure><img src='{html.escape(paths[0])}'><figcaption>일본어 원본</figcaption></figure>"
            f"<figure><img src='{html.escape(paths[1])}'><figcaption>중국어 패치</figcaption></figure>"
            f"</div><p>변경 바이트: {record['changed_byte_count']}</p></section>"
        )
    gallery = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>CPK 변경 이미지 비교</title><style>
body{font-family:Malgun Gothic,sans-serif;background:#202124;color:#eee;margin:24px}
section{background:#303134;margin:16px 0;padding:16px;border-radius:8px}.pair{display:flex;gap:20px;flex-wrap:wrap}
figure{margin:0}img{image-rendering:pixelated;min-width:256px;max-width:720px;height:auto;background:#111}
figcaption{text-align:center;margin-top:6px}</style></head><body><h1>CPK 변경 이미지 비교</h1>
""" + "\n".join(html_cards) + "</body></html>\n"
    (output_root / "index.html").write_text(gallery, encoding="utf-8")
    print(json.dumps({"record_count": len(records), "output_root": str(output_root)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
