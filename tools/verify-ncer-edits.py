#!/usr/bin/env python3
"""Verify rebuilt NCGR files render exactly like their edited NCER cell PNGs."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path, PurePosixPath

from PIL import Image, ImageChops


def load_converter():
    path = Path(__file__).with_name("convert-visual-assets.py")
    spec = importlib.util.spec_from_file_location("convert_visual_assets", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--edit-root", type=Path, required=True)
    parser.add_argument("--replacement-root", type=Path, required=True)
    parser.add_argument("--path-prefix", default="")
    args = parser.parse_args()
    converter = load_converter()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    checked = 0
    failures = []
    for record in manifest["records"]:
        if not record["source_path"].startswith(args.path_prefix) or "cell_png_paths" not in record:
            continue
        relative = PurePosixPath(record["source_path"])
        replacement = args.replacement_root / relative
        source = args.source_root / relative
        images, _, _ = converter.render_ncer_cells(
            replacement, source.with_suffix(".NCLR"), source.with_suffix(".NCER")
        )
        for image, cell_path in zip(images, record["cell_png_paths"], strict=True):
            expected = Image.open(args.edit_root / PurePosixPath(cell_path)).convert("RGBA")
            checked += 1
            if ImageChops.difference(image, expected).getbbox() is not None:
                failures.append(cell_path)
    result = {"checked_cell_count": checked, "failure_count": len(failures), "failures": failures}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
