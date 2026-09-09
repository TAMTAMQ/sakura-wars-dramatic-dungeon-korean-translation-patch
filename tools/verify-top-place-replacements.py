#!/usr/bin/env python3
"""Verify rebuilt top_place binaries render exactly as their palette-mapped PNGs."""

from __future__ import annotations

import argparse
import importlib.util
import json
import struct
from pathlib import Path

from PIL import Image, ImageChops


def load_module(filename: str, name: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--png-root", type=Path, required=True)
    parser.add_argument("--replacement-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    converter = load_module("convert-visual-assets.py", "convert_visual_assets")
    rebuilder = load_module("rebuild-visual-assets.py", "rebuild_visual_assets")
    records = json.loads(args.spec.read_text(encoding="utf-8"))["records"]
    failures = []
    checked = []
    for record in records:
        stem = Path(record["file"]).stem
        replacement = args.replacement_root / "cinema" / f"{stem}.bin"
        desired_path = args.png_root / record["file"]
        data = replacement.read_bytes()
        palette_raw = converter.chunks(data)[b"CLUT"][1]
        palette = [
            converter.rgb555(value)
            for value in struct.unpack(f"<{len(palette_raw) // 2}H", palette_raw)
        ]
        desired = Image.open(desired_path)
        expected_indexes = rebuilder.indexed_pixels(desired, palette)
        expected = Image.new("RGB", desired.size)
        expected.putdata([palette[index] for index in expected_indexes])
        actual, _ = converter.render_custom(replacement)
        checked.append(record["file"])
        if ImageChops.difference(expected, actual.convert("RGB")).getbbox() is not None:
            failures.append(record["file"])

    result = {
        "checked_count": len(checked),
        "failure_count": len(failures),
        "failures": failures,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
