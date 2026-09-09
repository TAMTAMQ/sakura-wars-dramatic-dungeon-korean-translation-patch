#!/usr/bin/env python3
"""Build the integrated text/font development ROM on the proven repacked layout."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import struct
from pathlib import Path

from PIL import ImageFont


CONTROL_TOKEN = re.compile(r"\{CTRL:01:([0-9A-F]{2})\}")
MODULE_PATH = Path(__file__).with_name("build-dev-poc.py")
SPEC = importlib.util.spec_from_file_location("build_dev_poc", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
FONT_BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FONT_BUILD)


BANNER_VERSION_SIZE = {1: 0x840, 2: 0x940, 3: 0xA40, 0x103: 0x240 + 8 * 0x100}
BANNER_TITLE_SLOTS = 6
BANNER_TITLE_SIZE = 0x100


def rebuild_banner(banner: bytes, title: str) -> bytes:
    """Put `title` in every language slot and refresh the checksum.

    The Chinese patch this build starts from carries its own banner, so leaving
    the base untouched would ship its title and its credit line.
    """
    encoded = title.encode("utf-16-le")
    if len(encoded) > BANNER_TITLE_SIZE - 2:
        raise ValueError("banner title does not fit a 128 character slot")
    padded = encoded + bytes(BANNER_TITLE_SIZE - len(encoded))
    result = bytearray(banner)
    for slot in range(BANNER_TITLE_SLOTS):
        start = 0x240 + slot * BANNER_TITLE_SIZE
        result[start : start + BANNER_TITLE_SIZE] = padded
    struct.pack_into("<H", result, 2, crc16_modbus(bytes(result[0x20:0x840])))
    return bytes(result)


def crc16_modbus(data: bytes) -> int:
    table = []
    for index in range(256):
        value = index
        for _ in range(8):
            value = (value >> 1) ^ (0xA001 if value & 1 else 0)
        table.append(value)
    crc = 0xFFFF
    for byte in data:
        crc = (crc >> 8) ^ table[(crc ^ byte) & 0xFF]
    return crc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--source-inventory", type=Path, required=True)
    parser.add_argument("--base-inventory", type=Path, required=True)
    parser.add_argument("--translation-index", type=Path, required=True)
    parser.add_argument("--visual-index", type=Path, required=True)
    parser.add_argument("--font-config", type=Path, required=True)
    parser.add_argument("--banner-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inventory_by_id(inventory: dict) -> dict[int, dict]:
    result = {int(item["file_id"]): item for item in inventory["files"]}
    for item in inventory["arm9_overlays"]:
        result[int(item["file_id"])] = item
    return result


def item_size(item: dict) -> int:
    """Return a FAT file extent for both named files and overlay records."""
    if "size" in item:
        return int(item["size"])
    return int(item["stored_size"])


def load_translation_records(index_path: Path) -> tuple[dict, list[dict]]:
    index = json.loads(index_path.read_text(encoding="utf-8"))
    root = index_path.resolve().parent
    records = []
    seen = set()
    for entry in index["segments"]:
        path = (root / entry["path"]).resolve()
        payload = path.read_bytes()
        if sha256(payload) != entry["sha256"]:
            raise ValueError(f"translation segment hash mismatch: {entry['path']}")
        segment = json.loads(payload.decode("utf-8"))
        if segment["region"] != entry["region"]:
            raise ValueError(f"translation region mismatch: {entry['path']}")
        for record in segment["records"]:
            if record["id"] in seen:
                raise ValueError(f"duplicate translation ID: {record['id']}")
            seen.add(record["id"])
            records.append({**record, "region": segment["region"]})
    if len(records) != int(index["scope_record_count"]):
        raise ValueError("translation scope count mismatch")
    return index, records


def parse_table(data: bytes) -> tuple[list[bytes], int]:
    first = struct.unpack_from("<I", data, 0)[0]
    pointers = struct.unpack_from(f"<{first // 4}I", data, 0)
    entries = []
    last_end = first
    for pointer in pointers:
        if pointer != last_end:
            raise ValueError("source text table is not tightly packed")
        end = data.find(b"\0", pointer)
        if end < 0:
            raise ValueError("unterminated source text table entry")
        entries.append(data[pointer:end])
        last_end = end + 1
    return entries, len(data) - last_end


def rebuild_table(entries: list[bytes], size: int) -> bytes:
    table_size = len(entries) * 4
    body = bytearray()
    pointers = []
    for entry in entries:
        pointers.append(table_size + len(body))
        body.extend(entry)
        body.append(0)
    result = struct.pack(f"<{len(pointers)}I", *pointers) + bytes(body)
    if len(result) > size:
        raise ValueError(f"rebuilt text table exceeds fixed file size by {len(result) - size} bytes")
    return result + b"\0" * (size - len(result))


def control_arguments(raw: bytes) -> list[int]:
    result = []
    index = 0
    while index < len(raw):
        if raw[index] == 1 and index + 1 < len(raw):
            result.append(raw[index + 1])
            index += 2
        else:
            index += 1
    return result


def allocate_codes(characters: list[str], used: set[bytes]) -> dict[str, bytes]:
    pool = FONT_BUILD.valid_custom_codes(bytes.fromhex("889f"), used)
    return {character: next(pool) for character in characters}


def encode_translation(text: str, mapping: dict[str, bytes]) -> bytes:
    def encode_character(character: str) -> bytes:
        if character in mapping:
            return mapping[character]
        return character.encode("shift_jis")

    result = bytearray()
    position = 0
    for match in CONTROL_TOKEN.finditer(text):
        for character in text[position : match.start()]:
            result.extend(encode_character(character))
        result.extend((1, int(match.group(1), 16)))
        position = match.end()
    for character in text[position:]:
        result.extend(encode_character(character))
    return bytes(result)


def main() -> None:
    args = parse_args()
    for path in (
        args.output,
        args.output.with_suffix(args.output.suffix + ".tmp"),
        args.report,
        args.report.with_suffix(args.report.suffix + ".tmp"),
    ):
        path.unlink(missing_ok=True)
    source = args.source.read_bytes()
    base = args.base.read_bytes()
    source_inventory = json.loads(args.source_inventory.read_text(encoding="utf-8"))
    base_inventory = json.loads(args.base_inventory.read_text(encoding="utf-8"))
    font_cfg = json.loads(args.font_config.read_text(encoding="utf-8"))
    index, records = load_translation_records(args.translation_index)
    visual_index = json.loads(args.visual_index.read_text(encoding="utf-8"))
    if sha256(source) != index["source_sha256"]:
        raise ValueError("translation index does not match source ROM")
    if sha256(base) != "14233650933bebfc577ec3e3e87e3c8698930c0e54e8ac44d3106fde02cd9ff4":
        raise ValueError("unexpected repacked base ROM")

    source_items = inventory_by_id(source_inventory)
    base_items = inventory_by_id(base_inventory)
    if set(source_items) != set(base_items):
        raise ValueError("source/base FAT populations differ")
    files_by_path = {item["path"]: item for item in source_inventory["files"]}
    base_files_by_path = {item["path"]: item for item in base_inventory["files"]}

    # The files under assets/translation are the authoritative translation state.
    # A non-empty korean_text must never silently fall back to Japanese merely
    # because an old apply_translation flag was left false by an earlier import.
    metadata_applied = [record for record in records if record["apply_translation"]]
    applied = [record for record in records if record.get("korean_text")]
    stale_disabled = [
        record["id"]
        for record in records
        if record.get("korean_text") and not record["apply_translation"]
    ]
    # Every translated record, not just the ones written into their own slot: a
    # translation that outgrew its slot is placed elsewhere afterwards and still
    # needs its glyphs in the font.
    custom_characters = sorted(
        {
            character
            for record in records
            for character in (record["korean_text"] or "")
            if not character.isspace()
            and character not in "{}:0123456789ABCDEFCTRL"
            and _not_shift_jis(character)
        }
    )

    source_font_item = files_by_path["font/LD937714.dat"]
    base_font_item = base_files_by_path["font/LD937714.dat"]
    source_font = source[int(source_font_item["start"]) : int(source_font_item["end"])]
    font_records = {
        source_font[offset : offset + 2]: source_font[offset + 2 : offset + 20]
        for offset in range(0, len(source_font), 20)
    }
    mapping = allocate_codes(custom_characters, set(font_records))
    font_source = (args.font_config.resolve().parent.parent / font_cfg["hangul_source_candidate"]["path"]).resolve()
    if sha256(font_source.read_bytes()) != font_cfg["hangul_source_candidate"]["sha256"]:
        raise ValueError("selected Hangul font hash mismatch")
    raster = font_cfg["hangul_rasterization"]
    pil_font = ImageFont.truetype(str(font_source), int(raster["pixel_size"]))
    raster_cfg = {
        "draw_x": raster["draw_x"],
        "draw_y": raster["draw_y"],
        "threshold": raster["threshold"],
    }
    for character, code in mapping.items():
        font_records[code] = FONT_BUILD.rasterize(character, pil_font, raster_cfg)
    if len(font_records) > 3300:
        raise ValueError("font record population exceeds proven 3300-record capacity")
    rebuilt_font = b"".join(code + font_records[code] for code in sorted(font_records))
    rebuilt_font += b"\0" * ((3300 - len(font_records)) * 20)
    if len(rebuilt_font) != int(base_font_item["size"]):
        raise ValueError("rebuilt font does not match proven base extent")

    replacements: dict[int, bytearray] = {}
    for file_id, source_item in source_items.items():
        if file_id == int(source_font_item["file_id"]):
            replacements[file_id] = bytearray(rebuilt_font)
            continue
        if item_size(source_item) != item_size(base_items[file_id]):
            raise ValueError(f"non-font file size changed for ID {file_id}")
        replacements[file_id] = bytearray(
            source[int(source_item["start"]) : int(source_item["end"])]
        )

    visual_root = args.visual_index.resolve().parent
    applied_visuals = []
    for record in visual_index["records"]:
        source_item = files_by_path[record["path"]]
        file_id = int(source_item["file_id"])
        source_asset = bytes(replacements[file_id])
        if sha256(source_asset) != record["source_sha256"]:
            raise ValueError(f"visual source precondition mismatch: {record['id']}")
        ready = (
            record["translation_status"] == "translated"
            and record["review_status"] == "complete"
            and record["replacement_path"] is not None
        )
        approval_fields = (
            record["translation_status"] == "translated",
            record["review_status"] == "complete",
            record["replacement_path"] is not None,
        )
        if any(approval_fields) and not all(approval_fields):
            raise ValueError(f"incomplete visual approval tuple: {record['id']}")
        if not ready:
            continue
        replacement_path = (visual_root / record["replacement_path"]).resolve()
        if not replacement_path.is_relative_to(visual_root):
            raise ValueError(f"visual replacement escapes asset root: {record['id']}")
        replacement = replacement_path.read_bytes()
        if len(replacement) != item_size(source_item):
            raise ValueError(f"visual replacement size mismatch: {record['id']}")
        if record.get("replacement_sha256") and sha256(replacement) != record["replacement_sha256"]:
            raise ValueError(f"visual replacement hash mismatch: {record['id']}")
        replacements[file_id] = bytearray(replacement)
        applied_visuals.append(record["id"])

    source_arm9_offset = struct.unpack_from("<I", source, 32)[0]
    source_arm9_size = struct.unpack_from("<I", source, 44)[0]
    base_arm9_offset = struct.unpack_from("<I", base, 32)[0]
    arm9 = bytearray(source[source_arm9_offset : source_arm9_offset + source_arm9_size])

    by_region: dict[str, list[dict]] = {}
    for record in applied:
        by_region.setdefault(record["region"], []).append(record)

    for path in ("text/CAMPDATA.DAT", "text/CITR.DAT"):
        item = files_by_path[path]
        file_id = int(item["file_id"])
        entries, _ = parse_table(bytes(replacements[file_id]))
        for record in by_region.get(path, []):
            entry_id = int(record["locator"]["entry_id"])
            encoded = encode_translation(record["korean_text"], mapping)
            if path == "text/CAMPDATA.DAT" and control_arguments(encoded) != control_arguments(entries[entry_id]):
                raise ValueError(f"CAMP control topology changed: {record['id']}")
            entries[entry_id] = encoded
        replacements[file_id] = bytearray(rebuild_table(entries, int(item["size"])))

    deferred_slot_overflow = []
    for region, region_records in by_region.items():
        if region in {"text/CAMPDATA.DAT", "text/CITR.DAT"}:
            continue
        if region == "arm9":
            buffer = arm9
        else:
            match = re.fullmatch(r"arm9-overlay:(\d+)", region)
            if not match:
                raise ValueError(f"unsupported applied region: {region}")
            overlay = next(
                item for item in source_inventory["arm9_overlays"] if int(item["overlay_id"]) == int(match.group(1))
            )
            buffer = replacements[int(overlay["file_id"])]
        for record in region_records:
            start = int(record["locator"]["offset"])
            source_raw = bytes.fromhex(record["source_hex"])
            fixed_width_bytes = record.get("fixed_width_bytes")
            if fixed_width_bytes is not None:
                if not isinstance(fixed_width_bytes, int) or fixed_width_bytes <= 0:
                    raise ValueError(f"invalid fixed-width declaration: {record['id']}")
                if len(source_raw) != fixed_width_bytes:
                    raise ValueError(f"fixed-width source size mismatch: {record['id']}")
            if bytes(buffer[start : start + len(source_raw)]) != source_raw:
                raise ValueError(f"embedded source precondition mismatch: {record['id']}")
            encoded = encode_translation(record["korean_text"], mapping)
            if fixed_width_bytes is not None and len(encoded) != fixed_width_bytes:
                raise ValueError(
                    f"fixed-width translation size mismatch: {record['id']} "
                    f"({len(encoded)} != {fixed_width_bytes})"
                )
            terminator = start + len(source_raw)
            zero_end = terminator
            while zero_end < len(buffer) and buffer[zero_end] == 0:
                zero_end += 1
            if zero_end == terminator:
                raise ValueError(f"source record is not NUL-terminated: {record['id']}")
            capacity = zero_end - start - 1
            if len(encoded) > capacity:
                # DEC-TEXTSLOT-003: ordinary embedded strings that outgrow their
                # source slot are deliberately left untouched in this first-stage
                # ROM. relocate-overflow-strings.py places them in reclaimed ARM9
                # slot tails and rewrites every live pointer afterwards. Explicit
                # fixed-width records (notably LIPS character tables) must never
                # take this path; they were checked for exact width above.
                if fixed_width_bytes is not None:
                    raise ValueError(f"fixed-width translation exceeds source slot: {record['id']}")
                deferred_slot_overflow.append(
                    {
                        "id": record["id"],
                        "region": region,
                        "capacity": capacity,
                        "used": len(encoded),
                    }
                )
                continue
            # Terminate where the translation ends and zero the rest. Padding a
            # short translation with spaces up to the original terminator makes
            # the game measure a longer string than it draws, which pushes
            # centred text left; the original terminator position stays a zero
            # byte either way, which is what DEC-TEXTSLOT-002 relies on.
            buffer[start:zero_end] = encoded + bytes(zero_end - start - len(encoded))

    planned = []
    rebuilt = bytearray(base)
    arm9_replacement = bytes(arm9)
    if rebuilt[base_arm9_offset : base_arm9_offset + len(arm9_replacement)] != arm9_replacement:
        planned.append(("arm9-source-restore-and-translation", base_arm9_offset, arm9_replacement))
    for file_id in sorted(replacements):
        item = base_items[file_id]
        replacement = bytes(replacements[file_id])
        start = int(item["start"])
        if base[start : start + len(replacement)] != replacement:
            planned.append((f"fat-file:{file_id}", start, replacement))
    banner_config = json.loads(args.banner_config.read_text(encoding="utf-8"))
    banner_start = struct.unpack_from("<I", base, 0x68)[0]
    banner_version = struct.unpack_from("<H", base, banner_start)[0]
    if banner_version != int(banner_config["expected_banner_version"]):
        raise ValueError(f"unexpected banner version: {banner_version}")
    banner_size = BANNER_VERSION_SIZE[banner_version]
    banner = base[banner_start : banner_start + banner_size]
    if crc16_modbus(banner[0x20:0x840]) != struct.unpack_from("<H", banner, 2)[0]:
        raise ValueError("base banner checksum precondition mismatch")
    new_banner = rebuild_banner(banner, banner_config["title"])
    if new_banner != banner:
        planned.append(("banner", banner_start, new_banner))

    planned.sort(key=lambda item: item[1])
    for left, right in zip(planned, planned[1:]):
        if left[1] + len(left[2]) > right[1]:
            raise ValueError(f"planned writes overlap: {left[0]} / {right[0]}")
    for _, start, replacement in planned:
        rebuilt[start : start + len(replacement)] = replacement
    rebuilt_bytes = bytes(rebuilt)

    intervals = [(start, start + len(replacement)) for _, start, replacement in planned]
    interval_index = 0
    for offset, (before, after) in enumerate(zip(base, rebuilt_bytes, strict=True)):
        if before == after:
            continue
        while interval_index < len(intervals) and offset >= intervals[interval_index][1]:
            interval_index += 1
        if interval_index >= len(intervals) or offset < intervals[interval_index][0]:
            raise ValueError(f"unregistered final diff at {offset:#x}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    output_tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    output_tmp.write_bytes(rebuilt_bytes)
    output_tmp.replace(args.output)
    report = {
        "schema_version": 1,
        "status": "development_translation_pipeline",
        "source_sha256": sha256(source),
        "base_sha256": sha256(base),
        "output_sha256": sha256(rebuilt_bytes),
        "output_size": len(rebuilt_bytes),
        "translation_records": len(records),
        "applied_translation_count": len(applied),
        "metadata_applied_translation_count": len(metadata_applied),
        "asset_text_authoritative": True,
        "stale_disabled_translation_count": len(stale_disabled),
        "stale_disabled_translation_ids": stale_disabled,
        "custom_glyph_count": len(mapping),
        "custom_mapping": {char: code.hex() for char, code in mapping.items()},
        "font_record_count": len(font_records),
        "font_extent_record_capacity": 3300,
        "planned_write_count": len(planned),
        "deferred_slot_overflow_count": len(deferred_slot_overflow),
        "deferred_slot_overflow": deferred_slot_overflow,
        "all_final_diffs_registered": True,
        "banner_title": banner_config["title"],
        "visual_scope_record_count": int(visual_index["record_count"]),
        "applied_visual_count": len(applied_visuals),
        "applied_visual_ids": applied_visuals,
        "untranslated_visuals_are_original_japanese": True,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report_tmp = args.report.with_suffix(args.report.suffix + ".tmp")
    report_tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_tmp.replace(args.report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def _not_shift_jis(character: str) -> bool:
    try:
        character.encode("shift_jis")
        return False
    except UnicodeEncodeError:
        return True


if __name__ == "__main__":
    main()
