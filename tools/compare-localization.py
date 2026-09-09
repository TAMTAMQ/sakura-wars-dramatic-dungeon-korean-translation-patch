#!/usr/bin/env python3
"""Compare a prior localized NDS image with the immutable survey source.

This is an analysis tool. Its output is evidence under work/, not a product-build
input and not proof that a prior patch's implementation is safe to reuse.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from collections import Counter
from pathlib import Path
from typing import BinaryIO


TEXT_PATHS = ("text/CAMPDATA.DAT", "text/CITR.DAT")
MAIN_FONT_PATH = "font/LD937714.dat"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--source-inventory", type=Path, required=True)
    parser.add_argument("--target-inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def inventory_map(inventory: dict) -> dict[int, dict]:
    result = {int(item["file_id"]): item for item in inventory["files"]}
    for overlay in inventory["arm9_overlays"]:
        file_id = int(overlay["file_id"])
        if file_id in result:
            raise ValueError(f"overlay file ID is also named in FNT: {file_id}")
        result[file_id] = {
            "file_id": file_id,
            "path": f"<arm9-overlay:{overlay['overlay_id']}>",
            "start": int(overlay["start"]),
            "end": int(overlay["end"]),
            "size": int(overlay["stored_size"]),
        }
    return result


def read_exact(stream: BinaryIO, start: int, size: int) -> bytes:
    stream.seek(start)
    data = stream.read(size)
    if len(data) != size:
        raise ValueError(f"short read at {start:#x}: wanted {size}, got {len(data)}")
    return data


def hash_range(stream: BinaryIO, start: int, size: int) -> str:
    stream.seek(start)
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        chunk = stream.read(min(remaining, 1024 * 1024))
        if not chunk:
            raise ValueError(f"short read while hashing at {start:#x}, size {size}")
        digest.update(chunk)
        remaining -= len(chunk)
    return digest.hexdigest()


def byte_diff_stats(source: bytes, target: bytes) -> dict:
    changed_count = 0
    changed_runs = 0
    first_changed = None
    last_changed = None
    in_run = False
    for offset in range(max(len(source), len(target))):
        changed = (
            offset >= len(source)
            or offset >= len(target)
            or source[offset] != target[offset]
        )
        if changed:
            changed_count += 1
            if first_changed is None:
                first_changed = offset
            last_changed = offset
            if not in_run:
                changed_runs += 1
        in_run = changed
    return {
        "changed_byte_count": changed_count,
        "changed_run_count": changed_runs,
        "first_changed_offset": first_changed,
        "last_changed_offset": last_changed,
    }


def header(data: bytes) -> dict:
    if len(data) < 512:
        raise ValueError("NDS header is truncated")
    u32 = lambda offset: struct.unpack_from("<I", data, offset)[0]
    return {
        "title": data[0:12].rstrip(b"\0").decode("ascii"),
        "game_code": data[12:16].decode("ascii"),
        "revision": data[30],
        "arm9_offset": u32(32),
        "arm9_ram_address": u32(40),
        "arm9_size": u32(44),
        "arm7_offset": u32(48),
        "arm7_ram_address": u32(56),
        "arm7_size": u32(60),
        "fnt_offset": u32(64),
        "fnt_size": u32(68),
        "fat_offset": u32(72),
        "fat_size": u32(76),
        "arm9_overlay_offset": u32(80),
        "arm9_overlay_size": u32(84),
        "banner_offset": u32(104),
        "used_rom_size": u32(128),
        "header_size": u32(132),
    }


def get_file(stream: BinaryIO, items: dict[int, dict], path: str) -> bytes:
    matches = [item for item in items.values() if item["path"] == path]
    if len(matches) != 1:
        raise ValueError(f"expected one inventory item for {path}, got {len(matches)}")
    item = matches[0]
    return read_exact(stream, int(item["start"]), int(item["size"]))


def parse_text_table(data: bytes, label: str) -> dict:
    if len(data) < 4:
        raise ValueError(f"{label}: text table is too short")
    first_pointer = struct.unpack_from("<I", data, 0)[0]
    if first_pointer < 4 or first_pointer % 4:
        raise ValueError(f"{label}: invalid first pointer {first_pointer:#x}")
    pointer_count = first_pointer // 4
    pointers = list(struct.unpack_from(f"<{pointer_count}I", data, 0))
    if any(pointer < first_pointer or pointer >= len(data) for pointer in pointers):
        raise ValueError(f"{label}: pointer outside string area")
    if pointers != sorted(pointers):
        raise ValueError(f"{label}: pointer table is not monotonic")
    if len(set(pointers)) != len(pointers):
        raise ValueError(f"{label}: duplicate string target")

    entries: list[bytes] = []
    packing_mismatches: list[dict] = []
    for index, pointer in enumerate(pointers):
        terminator = data.find(b"\0", pointer)
        if terminator < 0:
            raise ValueError(f"{label}: unterminated entry {index}")
        entries.append(data[pointer:terminator])
        expected_next = pointers[index + 1] if index + 1 < len(pointers) else len(data)
        if terminator + 1 != expected_next:
            packing_mismatches.append(
                {
                    "entry": index,
                    "terminated_end": terminator + 1,
                    "next": expected_next,
                    "delta": expected_next - (terminator + 1),
                }
            )

    trailing = data[pointers[-1] + len(entries[-1]) + 1 :]
    return {
        "first_pointer": first_pointer,
        "pointer_count": pointer_count,
        "unique_targets": len(set(pointers)),
        "empty_entries": sum(not entry for entry in entries),
        "total_string_bytes": sum(map(len, entries)),
        "maximum_string_bytes": max(map(len, entries), default=0),
        "packing_mismatches": packing_mismatches,
        "trailing_bytes": len(trailing),
        "trailing_values": sorted(set(trailing)),
        "pointers": pointers,
        "entries": entries,
    }


def control_sequence(entry: bytes) -> tuple[list[int], list[int]]:
    controls: list[int] = []
    orphan_controls: list[int] = []
    index = 0
    while index < len(entry):
        value = entry[index]
        if value == 1:
            if index + 1 >= len(entry):
                orphan_controls.append(value)
                break
            controls.append(entry[index + 1])
            index += 2
        elif value < 0x20:
            orphan_controls.append(value)
            index += 1
        elif value >= 0x80:
            index += 2
        else:
            index += 1
    return controls, orphan_controls


def font_map(data: bytes, record_size: int = 20) -> tuple[dict[bytes, list[bytes]], int]:
    if len(data) % record_size:
        raise ValueError(f"font size {len(data)} is not divisible by {record_size}")
    result: dict[bytes, list[bytes]] = {}
    for offset in range(0, len(data), record_size):
        code = data[offset : offset + 2]
        result.setdefault(code, []).append(data[offset + 2 : offset + record_size])
    return result, len(data) // record_size


def corpus_font_demand(tables: list[dict], codes: set[bytes]) -> dict:
    demand: set[bytes] = set()
    missing = Counter()
    control_arguments = Counter()
    for table in tables:
        for entry in table["entries"]:
            index = 0
            while index < len(entry):
                value = entry[index]
                if value == 1 and index + 1 < len(entry):
                    control_arguments[entry[index + 1]] += 1
                    index += 2
                    continue
                pair = entry[index : index + 2]
                if len(pair) == 2 and pair in codes:
                    demand.add(pair)
                    index += 2
                    continue
                if 0x20 <= value <= 0x7E or 0xA1 <= value <= 0xDF:
                    index += 1
                    continue
                missing[f"{value:02x}"] += 1
                index += 1
    return {
        "font_code_demand": len(demand),
        "unmapped_byte_values": sorted(missing),
        "unmapped_byte_occurrences": sum(missing.values()),
        "control_arguments": {f"{key:02x}": value for key, value in sorted(control_arguments.items())},
    }


def comparable_table(table: dict) -> dict:
    return {key: value for key, value in table.items() if key not in {"pointers", "entries"}}


def main() -> None:
    args = parse_args()
    source_inventory = load_json(args.source_inventory)
    target_inventory = load_json(args.target_inventory)
    source_items = inventory_map(source_inventory)
    target_items = inventory_map(target_inventory)
    if set(source_items) != set(target_items):
        raise ValueError("source and target FAT file ID populations differ")

    with args.source.open("rb") as source, args.target.open("rb") as target:
        source_header = header(read_exact(source, 0, 512))
        target_header = header(read_exact(target, 0, 512))

        changed_files = []
        relocated_unchanged = 0
        same_place_unchanged = 0
        for file_id in sorted(source_items):
            source_item = source_items[file_id]
            target_item = target_items[file_id]
            if source_item["path"] != target_item["path"]:
                raise ValueError(f"file path changed for ID {file_id}")
            source_hash = hash_range(source, int(source_item["start"]), int(source_item["size"]))
            target_hash = hash_range(target, int(target_item["start"]), int(target_item["size"]))
            if source_hash != target_hash:
                source_bytes = read_exact(source, int(source_item["start"]), int(source_item["size"]))
                target_bytes = read_exact(target, int(target_item["start"]), int(target_item["size"]))
                changed_files.append(
                    {
                        "file_id": file_id,
                        "path": source_item["path"],
                        "source_start": int(source_item["start"]),
                        "target_start": int(target_item["start"]),
                        "source_size": int(source_item["size"]),
                        "target_size": int(target_item["size"]),
                        "size_delta": int(target_item["size"]) - int(source_item["size"]),
                        "source_sha256": source_hash,
                        "target_sha256": target_hash,
                        **byte_diff_stats(source_bytes, target_bytes),
                    }
                )
            elif int(source_item["start"]) != int(target_item["start"]):
                relocated_unchanged += 1
            else:
                same_place_unchanged += 1

        source_tables = {
            path: parse_text_table(get_file(source, source_items, path), f"source:{path}")
            for path in TEXT_PATHS
        }
        target_tables = {
            path: parse_text_table(get_file(target, target_items, path), f"target:{path}")
            for path in TEXT_PATHS
        }

        table_pairs = {}
        for path in TEXT_PATHS:
            source_table = source_tables[path]
            target_table = target_tables[path]
            if source_table["pointer_count"] != target_table["pointer_count"]:
                raise ValueError(f"entry count changed for {path}")
            unchanged_entries = 0
            control_topology_mismatches = 0
            control_topology_mismatch_entries = []
            source_orphans = 0
            target_orphans = 0
            for entry_id, (source_entry, target_entry) in enumerate(
                zip(source_table["entries"], target_table["entries"], strict=True)
            ):
                unchanged_entries += source_entry == target_entry
                source_controls, source_bad = control_sequence(source_entry)
                target_controls, target_bad = control_sequence(target_entry)
                if source_controls != target_controls:
                    control_topology_mismatches += 1
                    control_topology_mismatch_entries.append(
                        {
                            "entry_id": entry_id,
                            "source_arguments": [f"{value:02x}" for value in source_controls],
                            "target_arguments": [f"{value:02x}" for value in target_controls],
                        }
                    )
                source_orphans += len(source_bad)
                target_orphans += len(target_bad)
            table_pairs[path] = {
                "entry_count": source_table["pointer_count"],
                "unchanged_entries": unchanged_entries,
                "changed_entries": source_table["pointer_count"] - unchanged_entries,
                "control_topology_mismatches": control_topology_mismatches,
                "control_topology_mismatch_entries": control_topology_mismatch_entries,
                "source_orphan_controls": source_orphans,
                "target_orphan_controls": target_orphans,
                "source": comparable_table(source_table),
                "target": comparable_table(target_table),
            }

        source_font, source_font_records = font_map(get_file(source, source_items, MAIN_FONT_PATH))
        target_font, target_font_records = font_map(get_file(target, target_items, MAIN_FONT_PATH))
        source_codes = set(source_font)
        target_codes = set(target_font)
        common_codes = source_codes & target_codes
        common_same_bitmap = sum(source_font[code][0] == target_font[code][0] for code in common_codes)

        system_regions = {}
        for name, offset_key, size_key in (
            ("arm9", "arm9_offset", "arm9_size"),
            ("arm7", "arm7_offset", "arm7_size"),
            ("fnt", "fnt_offset", "fnt_size"),
            ("fat", "fat_offset", "fat_size"),
            ("arm9_overlay_table", "arm9_overlay_offset", "arm9_overlay_size"),
        ):
            source_hash = hash_range(source, source_header[offset_key], source_header[size_key])
            target_hash = hash_range(target, target_header[offset_key], target_header[size_key])
            source_region = read_exact(
                source, source_header[offset_key], source_header[size_key]
            )
            target_region = read_exact(
                target, target_header[offset_key], target_header[size_key]
            )
            system_regions[name] = {
                "source_offset": source_header[offset_key],
                "target_offset": target_header[offset_key],
                "source_size": source_header[size_key],
                "target_size": target_header[size_key],
                "content_changed": source_hash != target_hash,
                "source_sha256": source_hash,
                "target_sha256": target_hash,
                **byte_diff_stats(source_region, target_region),
            }

    report = {
        "schema_version": 1,
        "source": {
            "path": args.source.name,
            "size": args.source.stat().st_size,
            "sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
            "header": source_header,
        },
        "target": {
            "path": args.target.name,
            "size": args.target.stat().st_size,
            "sha256": hashlib.sha256(args.target.read_bytes()).hexdigest(),
            "header": target_header,
        },
        "system_regions": system_regions,
        "nitrofs_comparison": {
            "file_count": len(source_items),
            "changed_file_count": len(changed_files),
            "relocated_unchanged_count": relocated_unchanged,
            "same_place_unchanged_count": same_place_unchanged,
            "changed_files": changed_files,
        },
        "text_tables": table_pairs,
        "main_font": {
            "record_size": 20,
            "source_records": source_font_records,
            "target_records": target_font_records,
            "source_unique_codes": len(source_codes),
            "target_unique_codes": len(target_codes),
            "source_duplicate_records": source_font_records - len(source_codes),
            "target_duplicate_records": target_font_records - len(target_codes),
            "added_codes": len(target_codes - source_codes),
            "removed_codes": len(source_codes - target_codes),
            "common_codes": len(common_codes),
            "common_codes_with_same_first_bitmap": common_same_bitmap,
            "source_corpus_coverage": corpus_font_demand(list(source_tables.values()), source_codes),
            "target_corpus_coverage": corpus_font_demand(list(target_tables.values()), target_codes),
        },
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")

    summary = {
        "output": str(args.output.resolve()),
        "changed_file_count": report["nitrofs_comparison"]["changed_file_count"],
        "relocated_unchanged_count": relocated_unchanged,
        "text_tables": {
            path: {
                "entries": item["entry_count"],
                "changed_entries": item["changed_entries"],
                "control_topology_mismatches": item["control_topology_mismatches"],
            }
            for path, item in table_pairs.items()
        },
        "main_font": report["main_font"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
