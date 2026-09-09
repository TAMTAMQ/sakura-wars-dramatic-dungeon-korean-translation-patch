#!/usr/bin/env python3
"""Build a diagnostic Hangul PoC on proven Chinese-patch font slots."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import struct
from pathlib import Path

from PIL import ImageFont


CHINESE_BASE_SHA256 = "14233650933bebfc577ec3e3e87e3c8698930c0e54e8ac44d3106fde02cd9ff4"
MODULE_PATH = Path(__file__).with_name("build-dev-poc.py")
SPEC = importlib.util.spec_from_file_location("build_dev_poc", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    args = parse_args()
    output_tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    report_tmp = args.report.with_suffix(args.report.suffix + ".tmp")
    for path in (args.output, output_tmp, args.report, report_tmp):
        path.unlink(missing_ok=True)

    base = args.base.read_bytes()
    if sha256(base) != CHINESE_BASE_SHA256:
        raise ValueError("unexpected Chinese-patch base")
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    files = {item["path"]: item for item in inventory["files"]}
    font_item = files[cfg["font"]["path"]]

    font_path = (args.config.resolve().parent.parent / cfg["font"]["source_path"]).resolve()
    if sha256(font_path.read_bytes()) != cfg["font"]["source_sha256"]:
        raise ValueError("Hangul source font hash mismatch")
    pil_font = ImageFont.truetype(str(font_path), int(cfg["font"]["pixel_size"]))

    characters = sorted(
        {
            character
            for translation in cfg["translations"]
            for character in translation["korean_text"]
            if not character.isspace()
        }
    )
    slot_codes = [bytes((0x88, trail)) for trail in range(0x9F, 0x9F + len(characters))]
    mapping = dict(zip(characters, slot_codes, strict=True))

    font_data = base[int(font_item["start"]) : int(font_item["end"])]
    records = {
        font_data[offset : offset + 2]: offset for offset in range(0, len(font_data), 20)
    }
    if any(code not in records for code in slot_codes):
        raise ValueError("Chinese base is missing a selected proven font slot")

    writes = []
    for character, code in mapping.items():
        start = int(font_item["start"]) + records[code] + 2
        replacement = BUILD.rasterize(character, pil_font, cfg["font"])
        writes.append(
            {
                "owner": f"glyph:{character}:{code.hex()}",
                "start": start,
                "original": base[start : start + 18],
                "replacement": replacement,
            }
        )
    arm9_start = struct.unpack_from("<I", base, 32)[0]
    translation_reports = []
    for translation in cfg["translations"]:
        original = bytes.fromhex(translation["base_hex"])
        absolute = arm9_start + int(translation["arm9_offset"])
        if base[absolute : absolute + len(original)] != original:
            raise ValueError(f"title-menu source precondition mismatch: {translation['id']}")
        encoded = bytearray()
        for character in translation["korean_text"]:
            encoded.extend(
                mapping[character] if character in mapping else character.encode("shift_jis")
            )
        if len(encoded) > len(original):
            raise ValueError(f"title-menu text exceeds fixed slot: {translation['id']}")
        encoded.extend(b" " * (len(original) - len(encoded)))
        writes.append(
            {
                "owner": translation["id"],
                "start": absolute,
                "original": original,
                "replacement": bytes(encoded),
            }
        )
        translation_reports.append(
            {
                "id": translation["id"],
                "source_text": translation["source_text"],
                "korean_text": translation["korean_text"],
                "encoded_hex": bytes(encoded).hex(),
                "slot_length": len(original),
            }
        )
    writes.sort(key=lambda item: item["start"])
    rebuilt = bytearray(base)
    for left, right in zip(writes, writes[1:]):
        if left["start"] + len(left["original"]) > right["start"]:
            raise ValueError("diagnostic Expected Writes overlap")
    for write in writes:
        start = write["start"]
        end = start + len(write["original"])
        if base[start:end] != write["original"]:
            raise ValueError(f"write precondition mismatch: {write['owner']}")
        rebuilt[start:end] = write["replacement"]
    rebuilt_bytes = bytes(rebuilt)

    allowed = [range(w["start"], w["start"] + len(w["original"])) for w in writes]
    for offset, (before, after) in enumerate(zip(base, rebuilt_bytes, strict=True)):
        if before != after and not any(offset in region for region in allowed):
            raise ValueError(f"untracked final diff: {offset:#x}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    output_tmp.write_bytes(rebuilt_bytes)
    output_tmp.replace(args.output)
    report = {
        "schema_version": 1,
        "status": "diagnostic_chinese_base_not_for_distribution",
        "base_sha256": sha256(base),
        "output_sha256": sha256(rebuilt_bytes),
        "output_size": len(rebuilt_bytes),
        "translations": translation_reports,
        "mapping": {character: code.hex() for character, code in mapping.items()},
        "expected_write_count": len(writes),
        "all_final_diffs_registered": True,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report_tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_tmp.replace(args.report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
