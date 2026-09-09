#!/usr/bin/env python3
"""Extract stable Japanese translation candidates using a prior patch as a map.

The prior localized ROM is evidence only. Japanese text and control topology always
come from the verified source ROM; target bytes merely decide whether a record was
touched by the prior patch.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import struct
import unicodedata
from pathlib import Path


TEXT_PATHS = ("text/CAMPDATA.DAT", "text/CITR.DAT")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--source-inventory", type=Path, required=True)
    parser.add_argument("--target-inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tsv-output", type=Path)
    return parser.parse_args()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_inventory(path: Path) -> tuple[dict[str, dict], dict[int, dict]]:
    inventory = json.loads(path.read_text(encoding="utf-8"))
    files = {item["path"]: item for item in inventory["files"]}
    overlays = {int(item["overlay_id"]): item for item in inventory["arm9_overlays"]}
    return files, overlays


def extent(image: bytes, item: dict) -> bytes:
    start = int(item["start"])
    end = int(item["end"])
    return image[start:end]


def text_entries(data: bytes) -> list[tuple[int, bytes]]:
    first_pointer = struct.unpack_from("<I", data, 0)[0]
    if first_pointer < 4 or first_pointer % 4:
        raise ValueError(f"invalid first text pointer: {first_pointer:#x}")
    pointers = struct.unpack_from(f"<{first_pointer // 4}I", data, 0)
    result = []
    for pointer in pointers:
        terminator = data.find(b"\0", pointer)
        if terminator < 0:
            raise ValueError(f"unterminated text at {pointer:#x}")
        result.append((pointer, data[pointer:terminator]))
    return result


def decode_source(raw: bytes) -> str:
    """Render source bytes without losing CAMP control tokens."""
    parts: list[str] = []
    plain = bytearray()

    def flush() -> None:
        if plain:
            parts.append(bytes(plain).decode("shift_jis"))
            plain.clear()

    index = 0
    while index < len(raw):
        if raw[index] == 1 and index + 1 < len(raw):
            flush()
            parts.append(f"{{CTRL:01:{raw[index + 1]:02X}}}")
            index += 2
        else:
            plain.append(raw[index])
            index += 1
    flush()
    return "".join(parts)


def has_japanese(text: str) -> bool:
    return any(
        "\u3040" <= char <= "\u30ff" or "\u3400" <= char <= "\u9fff"
        for char in text
    )


def nul_fields(source: bytes, target: bytes, region: str) -> list[dict]:
    """Find changed, strict Shift-JIS, NUL-terminated Japanese source fields."""
    common = min(len(source), len(target))
    result: list[dict] = []
    start = 0
    while start < common:
        end = source.find(b"\0", start, common)
        if end < 0:
            break
        raw = source[start:end]
        if raw and source[start : end + 1] != target[start : end + 1]:
            try:
                text = raw.decode("shift_jis")
            except UnicodeDecodeError:
                text = ""
            if text and has_japanese(text):
                result.append(
                    {
                        "id": f"{region}@{start:08X}",
                        "region": region,
                        "offset": start,
                        "source_byte_length": len(raw),
                        "source_hex": raw.hex(),
                        "source_text": text,
                        "prior_target_same_span_hex": target[start:end].hex(),
                    }
                )
        start = end + 1
    return result


def plausible_pointer_text(text: str) -> bool:
    """Reject binary false positives while retaining pointer-addressed UI text."""
    visible = [char for char in text if not char.isspace()]
    if len(visible) < 2:
        return False
    if any(not (char.isprintable() or char.isspace()) for char in text):
        return False
    japanese = sum(
        "\u3040" <= char <= "\u30ff" or "\u3400" <= char <= "\u9fff"
        for char in visible
    )
    assigned = sum(unicodedata.category(char) != "Cn" for char in visible)
    return assigned == len(visible) and japanese >= 2 and japanese / len(visible) >= 0.35


def pointer_fields(
    source: bytes,
    target: bytes,
    region: str,
    ram_address: int,
    existing: list[dict],
) -> list[dict]:
    """Find changed strings referenced by pointers but missed by NUL-boundary scans.

    Some tables place their first string immediately after pointer bytes, so that
    string does not begin after a NUL and cannot be found by ``nul_fields``.
    """
    common = min(len(source), len(target))
    existing_spans = [
        (int(item["offset"]), int(item["offset"]) + int(item["source_byte_length"]) + 1)
        for item in existing
    ]
    pointed_offsets: set[int] = set()
    ram_end = ram_address + common
    for pointer_at in range(0, common - 3):
        value = struct.unpack_from("<I", source, pointer_at)[0]
        if ram_address <= value < ram_end:
            pointed_offsets.add(value - ram_address)

    recovered: list[dict] = []
    occupied = list(existing_spans)
    for start in sorted(pointed_offsets):
        if any(span_start <= start < span_end for span_start, span_end in occupied):
            continue
        end = source.find(b"\0", start, min(common, start + 2048))
        if end <= start or source[start : end + 1] == target[start : end + 1]:
            continue
        raw = source[start:end]
        try:
            decoded = raw.decode("shift_jis")
        except UnicodeDecodeError:
            continue
        if not plausible_pointer_text(decoded):
            continue
        if any(start < span_end and end + 1 > span_start for span_start, span_end in occupied):
            continue
        recovered.append(
            {
                "id": f"{region}@{start:08X}",
                "region": region,
                "offset": start,
                "source_byte_length": len(raw),
                "source_hex": raw.hex(),
                "source_text": decoded,
                "prior_target_same_span_hex": target[start:end].hex(),
            }
        )
        occupied.append((start, end + 1))
    return recovered


def main() -> None:
    args = parse_args()
    source = args.source.read_bytes()
    target = args.target.read_bytes()
    source_files, source_overlays = load_inventory(args.source_inventory)
    target_files, target_overlays = load_inventory(args.target_inventory)

    candidates: list[dict] = []
    populations: dict[str, int] = {}

    for path in TEXT_PATHS:
        source_data = extent(source, source_files[path])
        target_data = extent(target, target_files[path])
        source_entries = text_entries(source_data)
        target_entries = text_entries(target_data)
        if len(source_entries) != len(target_entries):
            raise ValueError(f"entry population changed: {path}")
        region_count = 0
        for entry_id, ((pointer, raw), (_, prior_raw)) in enumerate(
            zip(source_entries, target_entries, strict=True)
        ):
            if raw == prior_raw:
                continue
            candidates.append(
                {
                    "id": f"{path}#{entry_id:04d}",
                    "region": path,
                    "entry_id": entry_id,
                    "source_offset": pointer,
                    "source_byte_length": len(raw),
                    "source_hex": raw.hex(),
                    "source_text": decode_source(raw),
                    "prior_target_hex": prior_raw.hex(),
                }
            )
            region_count += 1
        populations[path] = region_count

    def arm9(image: bytes) -> bytes:
        offset = struct.unpack_from("<I", image, 32)[0]
        size = struct.unpack_from("<I", image, 44)[0]
        return image[offset : offset + size]

    source_arm9 = arm9(source)
    target_arm9 = arm9(target)
    arm9_candidates = nul_fields(source_arm9, target_arm9, "arm9")
    arm9_ram_address = struct.unpack_from("<I", source, 40)[0]
    arm9_candidates.extend(
        pointer_fields(
            source_arm9,
            target_arm9,
            "arm9",
            arm9_ram_address,
            arm9_candidates,
        )
    )
    arm9_candidates.sort(key=lambda item: int(item["offset"]))
    candidates.extend(arm9_candidates)
    populations["arm9"] = len(arm9_candidates)

    for overlay_id in sorted(source_overlays):
        source_data = extent(source, source_overlays[overlay_id])
        target_data = extent(target, target_overlays[overlay_id])
        region = f"arm9-overlay:{overlay_id}"
        overlay_candidates = nul_fields(source_data, target_data, region)
        overlay_candidates.extend(
            pointer_fields(
                source_data,
                target_data,
                region,
                int(source_overlays[overlay_id]["ram_address"]),
                overlay_candidates,
            )
        )
        overlay_candidates.sort(key=lambda item: int(item["offset"]))
        candidates.extend(overlay_candidates)
        populations[region] = len(overlay_candidates)

    report = {
        "schema_version": 1,
        "authority": {
            "source_sha256": sha256(source),
            "prior_patch_target_sha256": sha256(target),
            "rule": "source text is authoritative; prior target only selects candidates",
        },
        "population": {
            "total": len(candidates),
            "by_region": populations,
        },
        "candidates": candidates,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.tsv_output:
        args.tsv_output.parent.mkdir(parents=True, exist_ok=True)
        with args.tsv_output.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=(
                    "id",
                    "region",
                    "source_text",
                    "source_hex",
                    "korean_text",
                    "note",
                ),
                delimiter="\t",
                extrasaction="ignore",
            )
            writer.writeheader()
            for candidate in candidates:
                tsv_text = (
                    candidate["source_text"]
                    .replace("\r", "{CR}")
                    .replace("\n", "{LF}")
                    .replace("\t", "{TAB}")
                )
                writer.writerow(
                    {
                        **candidate,
                        "source_text": tsv_text,
                        "korean_text": "",
                        "note": "",
                    }
                )
    print(json.dumps(report["population"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
