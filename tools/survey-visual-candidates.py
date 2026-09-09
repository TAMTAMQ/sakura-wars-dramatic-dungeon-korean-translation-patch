"""List decodable image assets in the source NitroFS and flag ones outside the visual index.

The adopted scope came from the prior Chinese patch's diff, so images that patch
never touched are absent from `assets/visual/index.json` even when they carry
Japanese text. This survey reports every file the existing converters can render.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path, PurePosixPath

CUSTOM_CHUNKS = {b"CLUT", b"CMAP", b"CHAR"}
TILE_SHEET_CHUNKS = {b"CLUT", b"CHAR"}
NCGR_MAGIC = b"RGCN"
NCLR_MAGIC = b"RLCN"
CUSTOM_DIMENSIONS = {
    768: (32, 24),
    1024: (32, 32),
    2048: (64, 32),
    3072: (64, 48),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def custom_chunks(payload: bytes) -> dict[bytes, bytes] | None:
    """Read the game's own CLUT/CMAP/CHAR container, or return None.

    Chunk headers store the size in 32-bit words and cover the header itself,
    matching tools/convert-visual-assets.py.
    """
    found: dict[bytes, bytes] = {}
    position = 0
    while position + 8 <= len(payload):
        tag = payload[position : position + 4]
        size = struct.unpack_from("<I", payload, position + 4)[0] * 4
        if size < 8 or position + size > len(payload):
            return None
        found[tag] = payload[position + 8 : position + size]
        position += size
    if position != len(payload):
        return None
    if CUSTOM_CHUNKS <= found.keys() or TILE_SHEET_CHUNKS <= found.keys():
        return found
    return None


def sibling(files: dict[str, dict], path: str, suffix: str) -> str | None:
    """Find a companion file, tolerating the mixed extension case in the ROM."""
    wanted = str(PurePosixPath(path).with_suffix(suffix)).lower()
    for candidate in files:
        if candidate.lower() == wanted:
            return candidate
    return None


def main() -> None:
    args = parse_args()
    source = args.source.read_bytes()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    index = json.loads(args.index.read_text(encoding="utf-8"))
    indexed = {record["path"] for record in index["records"]}
    files = {item["path"]: item for item in inventory["files"]}

    candidates = []
    for path, item in files.items():
        payload = source[int(item["start"]) : int(item["end"])]
        if not payload:
            continue
        kind = None
        detail: dict = {}
        chunks = custom_chunks(payload)
        if chunks is not None and b"CMAP" in chunks:
            cells = len(chunks[b"CMAP"]) // 2
            kind = "game_specific_binary_image"
            detail = {
                "cell_count": cells,
                "tile_grid": CUSTOM_DIMENSIONS.get(cells),
                "palette_color_count": len(chunks[b"CLUT"]) // 2,
                "tile_bank_count": len(chunks[b"CHAR"]) // 64,
            }
        elif chunks is not None:
            kind = "game_specific_tile_sheet"
            detail = {
                "palette_color_count": len(chunks[b"CLUT"]) // 2,
                "tile_bank_count": len(chunks[b"CHAR"]) // 64,
            }
        elif payload[:4] == NCGR_MAGIC:
            palette_path = sibling(files, path, ".NCLR")
            if palette_path is None:
                continue
            palette_item = files[palette_path]
            if source[int(palette_item["start"]) : int(palette_item["start"]) + 4] != NCLR_MAGIC:
                continue
            kind = "nintendo_ds_ncgr_image"
            detail = {
                "palette_path": palette_path,
                "has_ncer": sibling(files, path, ".NCER") is not None,
            }
        if kind is None:
            continue
        candidates.append(
            {
                "id": f"nitrofs:{path}",
                "path": path,
                "file_id": int(item["file_id"]),
                "category": path.split("/", 1)[0],
                "asset_kind": kind,
                "source_size": len(payload),
                "source_sha256": sha256(payload),
                "in_index": path in indexed,
                **detail,
            }
        )

    candidates.sort(key=lambda entry: entry["path"])
    missing = [entry for entry in candidates if not entry["in_index"]]
    by_category: dict[str, dict[str, int]] = {}
    for entry in candidates:
        bucket = by_category.setdefault(entry["category"], {"total": 0, "missing": 0})
        bucket["total"] += 1
        bucket["missing"] += 0 if entry["in_index"] else 1

    document = {
        "schema_version": 1,
        "source_sha256": sha256(source),
        "decodable_image_count": len(candidates),
        "missing_from_index_count": len(missing),
        "by_category": by_category,
        "candidates": candidates,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in document.items() if k != "candidates"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
