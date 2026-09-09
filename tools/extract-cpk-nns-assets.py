"""Pull Nintendo NNS graphics out of decompressed CPK entries.

Some CPK payloads are archives that carry NCGR/NCLR/NCER/NANR files rather than
the game's own CLUT container, so the CLUT-only scan in
tools/survey-cpk-visuals.py walks past them. The title-screen logo lives in
one of these. Each graphic is written out beside a PNG: the composed cells when
the cell layout can be split back into tiles, otherwise the plain tile sheet.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import struct
from pathlib import Path

NNS_MAGICS = {
    b"RGCN": "NCGR",
    b"RLCN": "NCLR",
    b"RECN": "NCER",
    b"RNAN": "NANR",
    b"RCSN": "NSCR",
}


def load_module(name: str):
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(name[:-3].replace("-", "_"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONVERTER = load_module("convert-visual-assets.py")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decompressed-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--entry", action="append", type=int, default=[], help="limit to these CPK entry ids")
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def find_nns_files(payload: bytes) -> list[tuple[int, str, int]]:
    """Locate every embedded NNS file by magic, byte-order mark, and length."""
    found = []
    for magic, kind in NNS_MAGICS.items():
        offset = payload.find(magic)
        while offset >= 0:
            if payload[offset + 4 : offset + 6] in (b"\xff\xfe", b"\xfe\xff"):
                size = struct.unpack_from("<I", payload, offset + 8)[0]
                if 16 < size <= len(payload) - offset:
                    found.append((offset, kind, size))
            offset = payload.find(magic, offset + 4)
    found.sort()
    return found


def main() -> None:
    args = parse_args()
    manifest = json.loads((args.decompressed_root / "manifest.json").read_text(encoding="utf-8"))
    wanted = set(args.entry)
    records = []
    for entry in manifest["records"]:
        entry_id = int(entry["id"])
        if wanted and entry_id not in wanted:
            continue
        payload = (args.decompressed_root / entry["path"]).read_bytes()
        files = find_nns_files(payload)
        if not files:
            continue
        directory = args.output_root / f"{entry_id:04d}"
        directory.mkdir(parents=True, exist_ok=True)
        written: dict[str, list[Path]] = {}
        for index, (offset, kind, size) in enumerate(files):
            path = directory / f"{index:02d}-{offset:06x}.{kind}"
            path.write_bytes(payload[offset : offset + size])
            written.setdefault(kind, []).append(path)

        palettes = written.get("NCLR", [])
        cells = written.get("NCER", [])
        for index, graphic in enumerate(written.get("NCGR", [])):
            palette = palettes[index] if index < len(palettes) else (palettes[0] if palettes else None)
            if palette is None:
                continue
            cell = cells[index] if index < len(cells) else (cells[0] if cells else None)
            rendered: dict = {}
            separable = False
            if cell is not None:
                # Always compose the cells: that is the only view where the
                # artwork is readable. Whether an edit can be split back into
                # tiles is a separate question, answered below.
                layout = CONVERTER.parse_ncer(cell)
                separable = CONVERTER.cells_are_separable(CONVERTER.parse_ncgr(graphic), layout)
                images, metadata, info = CONVERTER.render_ncer_cells(graphic, palette, cell)
                cell_directory = graphic.with_suffix(".cells")
                cell_directory.mkdir(parents=True, exist_ok=True)
                for image, meta in zip(images, metadata):
                    image.save(cell_directory / f"cell-{meta['index']:03d}.png")
                CONVERTER.contact_sheet(images, metadata).save(graphic.with_suffix(".cells.png"))
                rendered = {"kind": "cells", "cell_count": len(images), **info}
            if cell is None or not separable:
                image, info = CONVERTER.render_ncgr(graphic, palette)
                image.save(graphic.with_suffix(".tiles.png"))
                if not rendered:
                    rendered = {"kind": "tiles", **info}
            # Cells are the edit path everywhere. Where objects overlap or a
            # tile is reused mirrored, part of the picture is hidden and an edit
            # there cannot be read back, so the tile sheet ships alongside.
            rendered["edit_through"] = "cells"
            rendered["has_hidden_pixels"] = not separable
            records.append(
                {
                    "cpk_entry_id": entry_id,
                    "graphic": graphic.relative_to(args.output_root).as_posix(),
                    "palette": palette.relative_to(args.output_root).as_posix(),
                    "cell_bank": cell.relative_to(args.output_root).as_posix() if cell else None,
                    **rendered,
                }
            )

    document = {"schema_version": 1, "record_count": len(records), "records": records}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"record_count": len(records)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
