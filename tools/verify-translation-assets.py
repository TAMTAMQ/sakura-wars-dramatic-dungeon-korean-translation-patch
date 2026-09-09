#!/usr/bin/env python3
"""Validate versioned translation inputs against the immutable source ROM."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path


CONTROL_TOKEN = re.compile(r"\{CTRL:01:([0-9A-F]{2})\}")
REVIEW_STATES = {
    "untranslated",
    "in_progress",
    "cross_review_required",
    "human_judgment_required",
    "complete",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    return parser.parse_args()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode_source_text(text: str) -> bytes:
    result = bytearray()
    position = 0
    for match in CONTROL_TOKEN.finditer(text):
        result.extend(text[position : match.start()].encode("shift_jis"))
        result.extend((1, int(match.group(1), 16)))
        position = match.end()
    result.extend(text[position:].encode("shift_jis"))
    return bytes(result)


def translation_encoded_length(text: str) -> int:
    """Return the byte length used by the build's Shift-JIS/custom-glyph encoding."""
    def span_length(span: str) -> int:
        length = 0
        for character in span:
            try:
                length += len(character.encode("shift_jis"))
            except UnicodeEncodeError:
                # Custom glyphs are allocated from the game's two-byte font code space.
                length += 2
        return length

    length = 0
    position = 0
    for match in CONTROL_TOKEN.finditer(text):
        length += span_length(text[position : match.start()]) + 2
        position = match.end()
    return length + span_length(text[position:])


def table_entry_locations(data: bytes, label: str) -> list[tuple[int, int]]:
    first_pointer = struct.unpack_from("<I", data, 0)[0]
    if first_pointer < 4 or first_pointer % 4:
        raise ValueError(f"{label}: invalid first pointer")
    pointers = list(struct.unpack_from(f"<{first_pointer // 4}I", data, 0))
    if pointers != sorted(pointers) or len(set(pointers)) != len(pointers):
        raise ValueError(f"{label}: pointers must be unique and monotonic")
    result = []
    for entry_id, pointer in enumerate(pointers):
        terminator = data.find(b"\0", pointer)
        if terminator < 0:
            raise ValueError(f"{label}: unterminated entry {entry_id}")
        result.append((pointer, terminator))
    return result


def main() -> None:
    args = parse_args()
    source = args.source.read_bytes()
    source_hash = sha256(source)
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    index = json.loads(args.index.read_text(encoding="utf-8"))
    index_root = args.index.resolve().parent

    if index["source_sha256"] != source_hash:
        raise ValueError("translation index does not match the source ROM")
    if index["scope_decision"] != "DEC-PATCHMAP-001":
        raise ValueError("translation index has an unknown scope decision")

    files = {item["path"]: item for item in inventory["files"]}
    overlays = {
        int(item["overlay_id"]): item for item in inventory["arm9_overlays"]
    }
    arm9_offset = struct.unpack_from("<I", source, 32)[0]
    arm9_size = struct.unpack_from("<I", source, 44)[0]

    table_locations = {}
    for path in ("text/CAMPDATA.DAT", "text/CITR.DAT"):
        item = files[path]
        data = source[int(item["start"]) : int(item["end"])]
        table_locations[path] = (
            int(item["start"]),
            table_entry_locations(data, path),
        )

    listed_paths: set[Path] = set()
    regions: set[str] = set()
    ids: set[str] = set()
    record_count = 0
    metadata_applied_count = 0
    applied_count = 0
    complete_count = 0
    asset_text_authoritative = bool(
        index.get("translation_policy", {}).get("nonempty_korean_text_is_authoritative")
    )

    for segment_entry in index["segments"]:
        relative_path = Path(segment_entry["path"])
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"unsafe segment path: {relative_path}")
        segment_path = (index_root / relative_path).resolve()
        if not segment_path.is_relative_to(index_root):
            raise ValueError(f"segment escapes index root: {relative_path}")
        if segment_path in listed_paths:
            raise ValueError(f"duplicate segment path: {relative_path}")
        listed_paths.add(segment_path)

        payload = segment_path.read_bytes()
        if sha256(payload) != segment_entry["sha256"]:
            raise ValueError(f"segment hash mismatch: {relative_path}")
        segment = json.loads(payload.decode("utf-8"))
        region = segment["region"]
        if region != segment_entry["region"]:
            raise ValueError(f"segment region mismatch: {relative_path}")
        if region in regions:
            raise ValueError(f"duplicate segment region: {region}")
        regions.add(region)
        records = segment["records"]
        if len(records) != int(segment_entry["record_count"]):
            raise ValueError(f"segment record count mismatch: {relative_path}")

        for record in records:
            record_id = record["id"]
            if record_id in ids:
                raise ValueError(f"duplicate translation ID: {record_id}")
            ids.add(record_id)
            expected = bytes.fromhex(record["source_hex"])
            fixed_width_bytes = record.get("fixed_width_bytes")
            if fixed_width_bytes is not None:
                if not isinstance(fixed_width_bytes, int) or fixed_width_bytes <= 0:
                    raise ValueError(f"invalid fixed-width declaration: {record_id}")
                if len(expected) != fixed_width_bytes:
                    raise ValueError(f"fixed-width source size mismatch: {record_id}")
            if encode_source_text(record["source_text"]) != expected:
                raise ValueError(f"source text re-encode mismatch: {record_id}")

            if region in table_locations:
                file_start, locations = table_locations[region]
                entry_id = int(record["locator"]["entry_id"])
                if not 0 <= entry_id < len(locations):
                    raise ValueError(f"table entry outside population: {record_id}")
                relative_start, relative_end = locations[entry_id]
                absolute_start = file_start + relative_start
                absolute_end = file_start + relative_end
            elif region == "arm9":
                relative_start = int(record["locator"]["offset"])
                absolute_start = arm9_offset + relative_start
                absolute_end = absolute_start + len(expected)
                if absolute_end >= arm9_offset + arm9_size:
                    raise ValueError(f"ARM9 record outside boundary: {record_id}")
            else:
                match = re.fullmatch(r"arm9-overlay:(\d+)", region)
                if not match or int(match.group(1)) not in overlays:
                    raise ValueError(f"unknown translation region: {region}")
                item = overlays[int(match.group(1))]
                relative_start = int(record["locator"]["offset"])
                absolute_start = int(item["start"]) + relative_start
                absolute_end = absolute_start + len(expected)
                if absolute_end >= int(item["end"]):
                    raise ValueError(f"overlay record outside boundary: {record_id}")

            if absolute_end - absolute_start != len(expected):
                raise ValueError(f"source length changed: {record_id}")
            if source[absolute_start:absolute_end] != expected:
                raise ValueError(f"protected source bytes mismatch: {record_id}")
            if source[absolute_end] != 0:
                raise ValueError(f"source record is not NUL-terminated: {record_id}")

            state = record["review_status"]
            if state not in REVIEW_STATES:
                raise ValueError(f"invalid review state: {record_id}")
            apply_translation = record["apply_translation"]
            if not isinstance(apply_translation, bool):
                raise ValueError(f"apply_translation must be boolean: {record_id}")
            korean_text = record["korean_text"]
            if korean_text is not None and not isinstance(korean_text, str):
                raise ValueError(f"korean_text must be text or null: {record_id}")
            if apply_translation and not korean_text:
                raise ValueError(f"enabled translation is empty: {record_id}")
            effective_apply = bool(korean_text) if asset_text_authoritative else apply_translation
            if (
                effective_apply
                and fixed_width_bytes is not None
                and translation_encoded_length(korean_text) != fixed_width_bytes
            ):
                raise ValueError(f"fixed-width translation size mismatch: {record_id}")
            if state == "complete" and not effective_apply:
                raise ValueError(f"completed translation is not enabled: {record_id}")
            metadata_applied_count += apply_translation
            applied_count += effective_apply
            complete_count += state == "complete"
            record_count += 1

    if record_count != int(index["scope_record_count"]):
        raise ValueError("index scope population does not match segment records")

    print(
        json.dumps(
            {
                "valid": True,
                "source_sha256": source_hash,
                "segment_count": len(listed_paths),
                "record_count": record_count,
                "applied_translation_count": applied_count,
                "metadata_applied_translation_count": metadata_applied_count,
                "asset_text_authoritative": asset_text_authoritative,
                "completed_translation_count": complete_count,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
