#!/usr/bin/env python3
"""Verify LIPS slot tables, answer strings, and final NCGR replacements agree."""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--translation-index", type=Path, required=True)
    parser.add_argument("--visual-index", type=Path)
    parser.add_argument("--rom", type=Path)
    parser.add_argument("--build-report", type=Path)
    return parser.parse_args()


def load_records(index_path: Path) -> dict[str, tuple[str, dict]]:
    index = json.loads(index_path.read_text(encoding="utf-8"))
    root = index_path.parent
    result: dict[str, tuple[str, dict]] = {}
    for entry in index["segments"]:
        segment = json.loads((root / entry["path"]).read_text(encoding="utf-8"))
        region = segment["region"]
        for record in segment["records"]:
            result[record["id"]] = (region, record)
    return result


def phrase_from_slots(source_slots: str, target_slots: str, source_phrase: str) -> str:
    if len(source_slots) != len(target_slots):
        raise ValueError("LIPS source/target slot population differs")
    used: set[int] = set()
    output = []
    for character in source_phrase:
        candidates = [
            index
            for index, slot_character in enumerate(source_slots)
            if slot_character == character and index not in used
        ]
        if not candidates:
            raise ValueError(f"phrase character {character!r} is not available in LIPS slots")
        index = candidates[0]
        used.add(index)
        output.append(target_slots[index])
    return "".join(output)


def encode(text: str, mapping: dict[str, str]) -> bytes:
    result = bytearray()
    for character in text:
        if character in mapping:
            result.extend(bytes.fromhex(mapping[character]))
        else:
            result.extend(character.encode("shift_jis"))
    return bytes(result)


def rom_layout(rom: bytes) -> dict[str, tuple[int, int]]:
    arm9_offset, _entry, _ram, arm9_size = struct.unpack_from("<4I", rom, 0x20)
    result = {"arm9": (arm9_offset, arm9_offset + arm9_size)}
    overlay_offset, overlay_size = struct.unpack_from("<II", rom, 0x50)
    fat_offset, _fat_size = struct.unpack_from("<II", rom, 0x48)
    for position in range(overlay_offset, overlay_offset + overlay_size, 32):
        overlay_id, _ram, _size, _bss, _init, _fini, file_id, _flags = struct.unpack_from(
            "<8I", rom, position
        )
        start, end = struct.unpack_from("<II", rom, fat_offset + file_id * 8)
        result[f"arm9-overlay:{overlay_id}"] = (start, end)
    return result


def main() -> None:
    args = parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    records = load_records(args.translation_index)
    checked_answers = 0
    expected_by_id: dict[str, str] = {}

    for lips in spec["records"]:
        slot_id = lips["slot_record_id"]
        slot_region, slot_record = records[slot_id]
        if slot_region != "arm9":
            raise ValueError(f"LIPS slot table is not in ARM9: {slot_id}")
        source_slots = slot_record["source_text"]
        target_slots = slot_record["korean_text"]
        if slot_record.get("fixed_width_bytes") != 16:
            raise ValueError(f"LIPS slot table is not fixed at 16 bytes: {slot_id}")
        if len(source_slots) != 8 or len(target_slots) != 8:
            raise ValueError(f"LIPS slot table is not 8 characters: {slot_id}")
        expected_by_id[slot_id] = target_slots

        source_phrases = lips["source_phrases"]
        korean_phrases = lips["korean_phrases"]
        answer_record_ids = lips["answer_record_ids"]
        if not (len(source_phrases) == len(korean_phrases) == len(answer_record_ids)):
            raise ValueError(f"LIPS phrase metadata population mismatch: {lips['path']}")

        for source_phrase, korean_phrase, answer_ids in zip(
            source_phrases, korean_phrases, answer_record_ids, strict=True
        ):
            expected = phrase_from_slots(source_slots, target_slots, source_phrase)
            if expected != korean_phrase:
                raise ValueError(
                    f"LIPS spec disagrees with slot composition: {lips['path']} "
                    f"{source_phrase!r} -> {korean_phrase!r}, expected {expected!r}"
                )
            for answer_id in answer_ids:
                _region, answer_record = records[answer_id]
                if answer_record["source_text"] != source_phrase:
                    raise ValueError(f"LIPS answer source mismatch: {answer_id}")
                if answer_record["korean_text"] != expected:
                    raise ValueError(
                        f"LIPS answer translation mismatch: {answer_id} "
                        f"{answer_record['korean_text']!r} != {expected!r}"
                    )
                expected_by_id[answer_id] = expected
                checked_answers += 1

    final_checks = 0
    visual_checks = 0
    if args.rom is not None:
        if args.build_report is None or args.visual_index is None:
            raise ValueError("--rom requires --build-report and --visual-index")
        rom = args.rom.read_bytes()
        mapping = json.loads(args.build_report.read_text(encoding="utf-8"))["custom_mapping"]
        layout = rom_layout(rom)
        for record_id, expected_text in expected_by_id.items():
            region, record = records[record_id]
            start = layout[region][0] + int(record["locator"]["offset"])
            encoded = encode(expected_text, mapping)
            if rom[start : start + len(encoded)] != encoded:
                raise ValueError(f"final ROM LIPS text mismatch: {record_id}")
            final_checks += 1

        visual_index = json.loads(args.visual_index.read_text(encoding="utf-8"))
        visuals = {record["path"]: record for record in visual_index["records"]}
        visual_root = args.visual_index.parent
        fat_offset, _fat_size = struct.unpack_from("<II", rom, 0x48)
        for lips in spec["records"]:
            if lips.get("policy") == "source_retained":
                continue
            visual = visuals[lips["path"]]
            replacement_path = visual.get("replacement_path")
            if not replacement_path:
                raise ValueError(f"translated LIPS image has no replacement: {lips['path']}")
            replacement = (visual_root / replacement_path).read_bytes()
            file_id = int(visual["file_id"])
            start, end = struct.unpack_from("<II", rom, fat_offset + file_id * 8)
            if rom[start:end] != replacement:
                raise ValueError(f"final ROM LIPS image mismatch: {lips['path']}")
            visual_checks += 1

    print(
        json.dumps(
            {
                "valid": True,
                "lips_set_count": len(spec["records"]),
                "answer_record_count": checked_answers,
                "final_text_check_count": final_checks,
                "final_image_check_count": visual_checks,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
