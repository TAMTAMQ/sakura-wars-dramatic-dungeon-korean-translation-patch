#!/usr/bin/env python3
"""Build a tightly scoped one-string Hangul development PoC ROM."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def valid_custom_codes(start: bytes, used: set[bytes]):
    start_value = int.from_bytes(start, "big")
    for lead in range(0x81, 0xA0):
        for trail in (*range(0x40, 0x7F), *range(0x80, 0xFD)):
            code = bytes((lead, trail))
            if int.from_bytes(code, "big") >= start_value and code not in used:
                yield code


def rasterize(character: str, font: ImageFont.FreeTypeFont, cfg: dict) -> bytes:
    canvas = Image.new("L", (12, 12), 0)
    ImageDraw.Draw(canvas).text(
        (int(cfg["draw_x"]), int(cfg["draw_y"])),
        character,
        font=font,
        fill=255,
    )
    pixels = canvas.load()
    payload = bytearray(18)
    ink = 0
    for y in range(12):
        for x in range(12):
            if pixels[x, y] >= int(cfg["threshold"]):
                bit_index = y * 12 + x
                payload[bit_index // 8] |= 1 << (bit_index % 8)
                ink += 1
    if not ink:
        raise ValueError(f"rasterized glyph is empty: {character}")
    return bytes(payload)


def changed_count(left: bytes, right: bytes) -> int:
    return sum(a != b for a, b in zip(left, right, strict=True))


def main() -> None:
    args = parse_args()
    output_tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    report_tmp = args.report.with_suffix(args.report.suffix + ".tmp")
    for path in (args.output, output_tmp, args.report, report_tmp):
        path.unlink(missing_ok=True)

    source = args.source.read_bytes()
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    if sha256(source) != cfg["source_sha256"]:
        raise ValueError("unsupported source ROM")
    if cfg["status"] != "development_only_not_release_approved":
        raise ValueError("PoC config must remain development-only")

    files = {item["path"]: item for item in inventory["files"]}
    translation_cfg = cfg["translation"]
    font_cfg = cfg["font"]
    text_item = files[translation_cfg["path"]]
    font_item = files[font_cfg["path"]]

    text_data = source[int(text_item["start"]) : int(text_item["end"])]
    first_pointer = struct.unpack_from("<I", text_data, 0)[0]
    pointers = struct.unpack_from(f"<{first_pointer // 4}I", text_data, 0)
    entry_id = int(translation_cfg["entry_id"])
    entry_start = int(pointers[entry_id])
    entry_end = text_data.find(b"\0", entry_start)
    expected_text = bytes.fromhex(translation_cfg["source_hex"])
    if text_data[entry_start:entry_end] != expected_text:
        raise ValueError("PoC protected source text mismatch")

    font_path = (args.config.resolve().parent.parent / font_cfg["source_path"]).resolve()
    if sha256(font_path.read_bytes()) != font_cfg["source_sha256"]:
        raise ValueError("Hangul source font hash mismatch")
    pil_font = ImageFont.truetype(str(font_path), int(font_cfg["pixel_size"]))

    source_font = source[int(font_item["start"]) : int(font_item["end"])]
    if len(source_font) % 20:
        raise ValueError("source main font record size mismatch")
    records = {
        source_font[offset : offset + 2]: source_font[offset + 2 : offset + 20]
        for offset in range(0, len(source_font), 20)
    }
    if len(records) * 20 != len(source_font):
        raise ValueError("source main font contains duplicate codes")

    korean_characters = sorted(
        {char for char in translation_cfg["korean_text"] if not char.isspace()}
    )
    pool = valid_custom_codes(bytes.fromhex(font_cfg["custom_code_start_hex"]), set(records))
    mapping = {character: next(pool) for character in korean_characters}
    for character, code in mapping.items():
        records[code] = rasterize(character, pil_font, font_cfg)

    encoded_text = bytearray()
    for character in translation_cfg["korean_text"]:
        if character in mapping:
            encoded_text.extend(mapping[character])
        else:
            encoded_text.extend(character.encode("shift_jis"))
    if len(encoded_text) != len(expected_text):
        raise ValueError(
            f"PoC text must preserve its slot length: {len(encoded_text)} != {len(expected_text)}"
        )

    rebuilt_font = b"".join(code + records[code] for code in sorted(records))
    all_extents = [*inventory["files"], *inventory["arm9_overlays"]]
    next_start = min(
        int(item["start"])
        for item in all_extents
        if int(item["start"]) >= int(font_item["end"])
        and int(item["start"]) != int(font_item["start"])
    )
    new_font_end = int(font_item["start"]) + len(rebuilt_font)
    if new_font_end > next_start:
        raise ValueError("expanded font exceeds verified following-file boundary")

    fat_offset = struct.unpack_from("<I", source, 72)[0]
    fat_end_offset = fat_offset + int(font_item["file_id"]) * 8 + 4
    if struct.unpack_from("<I", source, fat_end_offset)[0] != int(font_item["end"]):
        raise ValueError("font FAT end precondition mismatch")

    text_absolute = int(text_item["start"]) + entry_start
    writes = [
        {
            "owner": translation_cfg["id"],
            "kind": "data",
            "start": text_absolute,
            "original": expected_text,
            "replacement": bytes(encoded_text),
        },
        {
            "owner": "main-font",
            "kind": "data",
            "start": int(font_item["start"]),
            "original": source[int(font_item["start"]) : new_font_end],
            "replacement": rebuilt_font,
        },
        {
            "owner": "main-font-fat-end",
            "kind": "filesystem-metadata",
            "start": fat_end_offset,
            "original": struct.pack("<I", int(font_item["end"])),
            "replacement": struct.pack("<I", new_font_end),
        },
    ]
    writes.sort(key=lambda item: item["start"])
    for left, right in zip(writes, writes[1:]):
        if left["start"] + len(left["original"]) > right["start"]:
            raise ValueError("Expected Writes overlap")
    rebuilt = bytearray(source)
    for write in writes:
        start = write["start"]
        end = start + len(write["original"])
        if len(write["original"]) != len(write["replacement"]):
            raise ValueError("in-place PoC write changed declared range length")
        if source[start:end] != write["original"]:
            raise ValueError(f"Expected Write precondition mismatch: {write['owner']}")
        rebuilt[start:end] = write["replacement"]

    rebuilt_bytes = bytes(rebuilt)
    allowed = [range(item["start"], item["start"] + len(item["original"])) for item in writes]
    for offset, (before, after) in enumerate(zip(source, rebuilt_bytes, strict=True)):
        if before != after and not any(offset in region for region in allowed):
            raise ValueError(f"untracked final diff at {offset:#x}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    output_tmp.write_bytes(rebuilt_bytes)
    output_tmp.replace(args.output)
    report = {
        "schema_version": 1,
        "status": cfg["status"],
        "source_sha256": sha256(source),
        "output_sha256": sha256(rebuilt_bytes),
        "output_size": len(rebuilt_bytes),
        "translation": {
            "id": translation_cfg["id"],
            "source_text": "操作方法のヒント",
            "korean_text": translation_cfg["korean_text"],
            "encoded_hex": bytes(encoded_text).hex(),
        },
        "font": {
            "source_records": len(source_font) // 20,
            "output_records": len(records),
            "added_glyphs": {char: code.hex() for char, code in mapping.items()},
            "source_size": len(source_font),
            "output_size": len(rebuilt_font),
            "available_gap": next_start - int(font_item["end"]),
            "remaining_gap": next_start - new_font_end,
        },
        "expected_writes": [
            {
                "owner": item["owner"],
                "kind": item["kind"],
                "start": item["start"],
                "length": len(item["original"]),
                "original_sha256": sha256(item["original"]),
                "replacement_sha256": sha256(item["replacement"]),
                "changed_bytes": changed_count(item["original"], item["replacement"]),
            }
            for item in writes
        ],
        "all_final_diffs_registered": True,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report_tmp.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report_tmp.replace(args.report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
