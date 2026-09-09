#!/usr/bin/env python3
"""Verify lossless source-text extraction and no-change reconstruction boundaries.

This remains a survey verifier: it consumes the prior-patch candidate manifest under
work/ and must not be used as a product build input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from collections import defaultdict
from pathlib import Path


TEXT_PATHS = ("text/CAMPDATA.DAT", "text/CITR.DAT")
CONTROL_TOKEN = re.compile(r"\{CTRL:01:([0-9A-F]{2})\}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extent(image: bytes, item: dict) -> bytes:
    return image[int(item["start"]) : int(item["end"])]


def encode_source_text(text: str) -> bytes:
    """Encode Shift-JIS plus the explicit CAMP control-token notation."""
    result = bytearray()
    position = 0
    for match in CONTROL_TOKEN.finditer(text):
        result.extend(text[position : match.start()].encode("shift_jis"))
        result.extend((1, int(match.group(1), 16)))
        position = match.end()
    result.extend(text[position:].encode("shift_jis"))
    return bytes(result)


def parse_text_table(data: bytes, label: str) -> tuple[list[bytes], bytes]:
    if len(data) < 4:
        raise ValueError(f"{label}: table is too short")
    first_pointer = struct.unpack_from("<I", data, 0)[0]
    if first_pointer < 4 or first_pointer % 4:
        raise ValueError(f"{label}: invalid first pointer {first_pointer:#x}")
    pointer_count = first_pointer // 4
    pointers = list(struct.unpack_from(f"<{pointer_count}I", data, 0))
    if pointers != sorted(pointers) or len(set(pointers)) != len(pointers):
        raise ValueError(f"{label}: pointers must be unique and monotonic")

    entries: list[bytes] = []
    last_end = first_pointer
    for entry_id, pointer in enumerate(pointers):
        if pointer != last_end:
            raise ValueError(
                f"{label}: entry {entry_id} is not tightly packed "
                f"({pointer:#x} != {last_end:#x})"
            )
        terminator = data.find(b"\0", pointer)
        if terminator < 0:
            raise ValueError(f"{label}: entry {entry_id} is unterminated")
        entries.append(data[pointer:terminator])
        last_end = terminator + 1
    return entries, data[last_end:]


def rebuild_text_table(entries: list[bytes], trailing: bytes) -> bytes:
    pointer_table_size = len(entries) * 4
    body = bytearray()
    pointers = []
    for entry in entries:
        pointers.append(pointer_table_size + len(body))
        body.extend(entry)
        body.append(0)
    return struct.pack(f"<{len(pointers)}I", *pointers) + bytes(body) + trailing


def main() -> None:
    args = parse_args()
    temporary_output = args.output.with_suffix(args.output.suffix + ".tmp")
    args.output.unlink(missing_ok=True)
    temporary_output.unlink(missing_ok=True)
    source = args.source.read_bytes()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    manifest = json.loads(args.candidates.read_text(encoding="utf-8"))

    source_hash = digest(source)
    authority_hash = manifest["authority"]["source_sha256"]
    if source_hash != authority_hash:
        raise ValueError(
            f"candidate authority mismatch: {source_hash} != {authority_hash}"
        )
    if int(manifest["population"]["total"]) != len(manifest["candidates"]):
        raise ValueError("candidate manifest population does not match its records")

    files = {item["path"]: item for item in inventory["files"]}
    overlays = {
        int(item["overlay_id"]): item for item in inventory["arm9_overlays"]
    }
    arm9_offset = struct.unpack_from("<I", source, 32)[0]
    arm9_size = struct.unpack_from("<I", source, 44)[0]
    boundaries: dict[str, tuple[int, int]] = {
        "arm9": (arm9_offset, arm9_offset + arm9_size),
        **{
            f"arm9-overlay:{overlay_id}": (int(item["start"]), int(item["end"]))
            for overlay_id, item in overlays.items()
        },
        **{
            path: (int(files[path]["start"]), int(files[path]["end"]))
            for path in TEXT_PATHS
        },
    }

    writes_by_region: dict[str, list[tuple[int, int, bytes, str]]] = defaultdict(list)
    reencoded_count = 0
    for candidate in manifest["candidates"]:
        region = candidate["region"]
        if region not in boundaries:
            raise ValueError(f"unknown candidate region: {region}")
        encoded = encode_source_text(candidate["source_text"])
        expected = bytes.fromhex(candidate["source_hex"])
        if encoded != expected:
            raise ValueError(f"source text re-encode mismatch: {candidate['id']}")

        relative = int(candidate.get("source_offset", candidate.get("offset")))
        boundary_start, boundary_end = boundaries[region]
        absolute_start = boundary_start + relative
        absolute_end = absolute_start + len(expected)
        if not (boundary_start <= absolute_start <= absolute_end < boundary_end):
            raise ValueError(f"candidate outside boundary: {candidate['id']}")
        if source[absolute_start:absolute_end] != expected:
            raise ValueError(f"protected source bytes mismatch: {candidate['id']}")
        if source[absolute_end] != 0:
            raise ValueError(f"candidate is not NUL-terminated: {candidate['id']}")
        writes_by_region[region].append(
            (absolute_start, absolute_end, encoded, candidate["id"])
        )
        reencoded_count += 1

    for region, writes in writes_by_region.items():
        writes.sort()
        for left, right in zip(writes, writes[1:]):
            if left[1] > right[0]:
                raise ValueError(f"overlapping candidate writes: {left[3]} / {right[3]}")

    table_reports = {}
    for path in TEXT_PATHS:
        original = extent(source, files[path])
        entries, trailing = parse_text_table(original, path)
        rebuilt = rebuild_text_table(entries, trailing)
        if rebuilt != original:
            raise ValueError(f"full table round-trip mismatch: {path}")
        table_reports[path] = {
            "entry_count": len(entries),
            "trailing_byte_count": len(trailing),
            "boundary_size": len(original),
            "sha256": digest(original),
            "identical": True,
        }

    boundary_reports = {}
    for region, (start, end) in boundaries.items():
        original = source[start:end]
        rebuilt = bytearray(original)
        for absolute_start, absolute_end, replacement, _ in writes_by_region.get(
            region, []
        ):
            local_start = absolute_start - start
            local_end = absolute_end - start
            rebuilt[local_start:local_end] = replacement
        rebuilt_bytes = bytes(rebuilt)
        if rebuilt_bytes != original:
            raise ValueError(f"declared boundary round-trip mismatch: {region}")
        boundary_reports[region] = {
            "candidate_count": len(writes_by_region.get(region, [])),
            "boundary_size": len(original),
            "sha256": digest(original),
            "identical": True,
        }

    report = {
        "schema_version": 1,
        "source_sha256": source_hash,
        "candidate_count": len(manifest["candidates"]),
        "reencoded_candidate_count": reencoded_count,
        "candidate_ids_unique": len({item["id"] for item in manifest["candidates"]})
        == len(manifest["candidates"]),
        "all_boundaries_identical": True,
        "text_tables": table_reports,
        "boundaries": boundary_reports,
    }
    if not report["candidate_ids_unique"]:
        raise ValueError("duplicate candidate IDs")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary_output.replace(args.output)
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "candidate_count": report["candidate_count"],
                "all_boundaries_identical": report["all_boundaries_identical"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
