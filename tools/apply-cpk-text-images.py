"""Write the translated CPK artwork back into faCpkData.cpk.

Two kinds of asset live inside the container: the game's own CLUT images and
NNS sprite banks edited as cells. Both are rebuilt at their original size,
spliced into the entry payload, and - for the entries the game stores with
CRILAYLA - compressed again. A CPK entry sits at a fixed offset, so the packed
result has to fit the slot it came from; the leftover bytes are zeroed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path


def load_module(name: str):
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(name[:-3].replace("-", "_"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REBUILDER = load_module("rebuild-visual-assets.py")
DECOMPRESSOR = load_module("decompress-cpk-entries.py")
COMPRESSOR = load_module("compress-cpk-entries.py")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpk", type=Path, required=True, help="original faCpkData.cpk")
    parser.add_argument("--cpk-manifest", type=Path, required=True)
    parser.add_argument("--decompressed-root", type=Path, required=True)
    parser.add_argument("--extracted-root", type=Path, required=True)
    parser.add_argument("--nns-root", type=Path, required=True)
    parser.add_argument("--edits-root", type=Path, required=True)
    parser.add_argument("--sprite-targets", type=Path, required=True)
    parser.add_argument("--clut-targets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument(
        "--skip-compressed",
        action="store_true",
        help="leave every CRILAYLA entry byte-identical, the way the prior patch did",
    )
    parser.add_argument(
        "--skip-entries",
        default="",
        help="comma separated CPK entry ids to leave untouched",
    )
    return parser.parse_args()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve_graphic(directory: Path, offset: int) -> tuple[Path, Path, Path]:
    """Find the graphic at `offset` and the palette and cell bank that go with it.

    Copies of the same artwork sit at different offsets in different entries, so
    the file names cannot be carried over from one entry to another. The trio is
    paired by ordinal position, the same way the extraction wrote them out.
    """
    graphics = sorted(directory.glob("*.NCGR"))
    palettes = sorted(directory.glob("*.NCLR"))
    cells = sorted(directory.glob("*.NCER"))
    for index, graphic in enumerate(graphics):
        if int(graphic.stem.split("-")[1], 16) != offset:
            continue
        palette = palettes[index] if index < len(palettes) else palettes[0]
        cell = cells[index] if index < len(cells) else cells[0]
        return graphic, palette, cell
    raise ValueError(f"no NCGR at {offset:#x} under {directory}")


def main() -> None:
    args = parse_args()
    cpk = bytearray(args.cpk.read_bytes())
    manifest = json.loads(args.cpk_manifest.read_text(encoding="utf-8"))
    slots = {int(r["id"]): r for r in manifest["records"]}
    sprites = json.loads(args.sprite_targets.read_text(encoding="utf-8"))
    cluts = json.loads(args.clut_targets.read_text(encoding="utf-8"))

    # Collect every edit per CPK entry so an entry is packed only once.
    per_entry: dict[int, list[dict]] = {}
    for record in cluts["records"]:
        png = args.edits_root / record["png"]
        if not png.exists():
            continue
        for target in record["targets"]:
            per_entry.setdefault(int(target["cpk_entry_id"]), []).append(
                {"kind": "clut", "png": png, "offset": int(target["offset"]), "size": int(target["size"])}
            )
    for record in sprites["records"]:
        files = [(args.edits_root / f["png"], int(f["cell"])) for f in record["files"]]
        present = [png for png, _ in files if png.exists()]
        if not present:
            continue
        if len(present) != len(files):
            # A cell bank is rebuilt as a whole, so a half-populated record would
            # drop the entry without saying so. Name the missing files instead.
            missing = ", ".join(png.name for png, _ in files if not png.exists())
            raise ValueError(f"{record['name']} is missing cell images: {missing}")
        for target in record["targets"]:
            per_entry.setdefault(int(target["cpk_entry_id"]), []).append(
                {
                    "kind": "cells",
                    "files": files,
                    "offset": int(target["graphic_offset"]),
                    "palette": record["palette"],
                    "cell_bank": record["cell_bank"],
                    "name": record["name"],
                }
            )

    skipped_ids = {int(item) for item in args.skip_entries.split(",") if item.strip()}

    applied = []
    skipped = []
    for entry_id in sorted(per_entry):
        slot = slots[entry_id]
        compressed = bool(slot.get("compressed"))
        if entry_id in skipped_ids or (compressed and args.skip_compressed):
            skipped.append(entry_id)
            continue
        payload_path = (
            args.decompressed_root / f"{entry_id:04d}.bin" if compressed else args.extracted_root / slot["path"]
        )
        payload = bytearray(payload_path.read_bytes())
        changes = []

        for edit in per_entry[entry_id]:
            if edit["kind"] == "clut":
                stream = args.work_root / f"{entry_id:04d}-{edit['offset']:06x}.bin"
                stream.parent.mkdir(parents=True, exist_ok=True)
                stream.write_bytes(bytes(payload[edit["offset"] : edit["offset"] + edit["size"]]))
                rebuilt = REBUILDER.rebuild_custom(stream, edit["png"], merge_tiles=True)
                label = edit["png"].name
            else:
                directory = args.nns_root / f"{entry_id:04d}"
                graphic, palette_path, cell_path = resolve_graphic(directory, edit["offset"])
                cells = args.work_root / "cells" / f"{entry_id:04d}-{edit['offset']:06x}"
                if cells.exists():
                    shutil.rmtree(cells)
                cells.mkdir(parents=True)
                source_cells = graphic.with_suffix(".cells")
                for cell in sorted(source_cells.glob("cell-*.png")):
                    shutil.copy2(cell, cells / cell.name)
                for png, index in edit["files"]:
                    shutil.copy2(png, cells / f"cell-{index:03d}.png")
                rebuilt = REBUILDER.rebuild_ncer_ncgr(graphic, cells, palette_path, cell_path)
                label = edit["name"]

            start = edit["offset"]
            if len(rebuilt) != (edit["size"] if edit["kind"] == "clut" else graphic.stat().st_size):
                raise ValueError(f"rebuilt asset changed size: {label}")
            payload[start : start + len(rebuilt)] = rebuilt
            changes.append(label)

        plain = bytes(payload)
        capacity = int(slot["file_size"])
        effort = None
        if compressed:
            packed, effort = COMPRESSOR.compress_to_fit(plain, capacity)
            if DECOMPRESSOR.decompress(packed) != plain:
                raise ValueError(f"CRILAYLA round trip failed for CPK entry {entry_id}")
        else:
            packed = plain
        if len(packed) > capacity:
            raise ValueError(f"CPK entry {entry_id} needs {len(packed)} bytes but its slot holds {capacity}")
        start = int(slot["offset"])
        cpk[start : start + capacity] = packed + bytes(capacity - len(packed))
        applied.append(
            {
                "cpk_entry_id": entry_id,
                "compressed": compressed,
                "assets": changes,
                "packed_size": len(packed),
                "slot_size": capacity,
                "slack": capacity - len(packed),
                "search_effort": effort,
            }
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(bytes(cpk))
    document = {
        "schema_version": 1,
        "source_cpk_sha256": sha256(args.cpk.read_bytes()),
        "output_cpk_sha256": sha256(bytes(cpk)),
        "output_size": len(cpk),
        "entry_count": len(applied),
        "asset_count": sum(len(item["assets"]) for item in applied),
        "skipped_entries": skipped,
        "entries": applied,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in document.items() if k != "entries"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
