#!/usr/bin/env python3
"""Verify that exported editable PNGs rebuild to byte-identical source assets."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path, PurePosixPath


def load_rebuilder():
    path = Path(__file__).with_name("rebuild-visual-assets.py")
    spec = importlib.util.spec_from_file_location("rebuild_visual_assets", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--png-root", type=Path, required=True)
    parser.add_argument("--path-prefix", default="")
    args = parser.parse_args()
    rebuilder = load_rebuilder()
    converter = rebuilder.CONVERTER
    index = json.loads(args.index.read_text(encoding="utf-8"))
    checked = []
    failures = []
    for record in index["records"]:
        if not record["path"].startswith(args.path_prefix):
            continue
        relative = PurePosixPath(record["path"])
        source = (args.source_root / relative).resolve()
        if record["asset_kind"] == "game_specific_binary_image":
            rebuilt = rebuilder.rebuild_custom(
                source, (args.png_root / relative.with_suffix(".png")).resolve()
            )
        elif record["asset_kind"] == "game_specific_tile_sheet":
            rebuilt = rebuilder.rebuild_tile_sheet(
                source, (args.png_root / relative.with_suffix(".tiles.png")).resolve()
            )
        elif record["asset_kind"] == "nintendo_ds_ncgr_image":
            ncer = source.with_suffix(".NCER")
            if ncer.exists() and (args.png_root / relative.with_suffix(".cells")).is_dir():
                rebuilt = rebuilder.rebuild_ncer_ncgr(
                    source,
                    (args.png_root / relative.with_suffix(".cells")).resolve(),
                    source.with_suffix(".NCLR"),
                    ncer,
                )
            else:
                rebuilt = rebuilder.rebuild_ncgr(
                    source,
                    (args.png_root / relative.with_suffix(".tiles.png")).resolve(),
                    source.with_suffix(".NCLR"),
                )
        else:
            continue
        checked.append(record["path"])
        if rebuilt != source.read_bytes():
            failures.append(record["path"])
    result = {"checked_count": len(checked), "failure_count": len(failures), "failures": failures}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
