"""Check that a relocation actually landed and touched nothing else.

The two mistakes worth catching are silent ones. A string can be written at an
offset derived from the wrong ROM layout, which lands in code rather than in the
slot it meant to reuse; and two relocations can be handed the same hole, so one
overwrites the other. Both leave a ROM that builds and boots.

Everything here is measured against the ROM's own overlay table, never against
offsets recorded for a different ROM.
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

NUL = bytes(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, required=True, help="ROM the relocation started from")
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True, help="index the build used")
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--relocation-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def overlay_layout(rom: bytes) -> dict[str, tuple[int, int, int]]:
    arm9_rom, _entry, arm9_ram, arm9_size = struct.unpack_from("<4I", rom, 0x20)
    layout = {"arm9": (arm9_rom, arm9_rom + arm9_size, arm9_ram)}
    overlay_offset, overlay_size = struct.unpack_from("<II", rom, 0x50)
    fat_offset, _fat_size = struct.unpack_from("<II", rom, 0x48)
    for position in range(overlay_offset, overlay_offset + overlay_size, 32):
        overlay_id, ram_address, _rs, _bss, _i, _f, file_id, _r = struct.unpack_from("<8I", rom, position)
        start, end = struct.unpack_from("<II", rom, fat_offset + file_id * 8)
        layout[f"arm9-overlay:{overlay_id}"] = (start, end, ram_address)
    return layout


def main() -> None:
    args = parse_args()
    before = args.before.read_bytes()
    after = args.after.read_bytes()
    relocation = json.loads(args.relocation_report.read_text(encoding="utf-8"))
    mapping = json.loads(args.build_report.read_text(encoding="utf-8"))["custom_mapping"]
    index = json.loads(args.index.read_text(encoding="utf-8"))
    layout = overlay_layout(after)

    records: dict[str, dict] = {}
    for entry in index["segments"]:
        segment = json.loads((args.index.parent / entry["path"]).read_text(encoding="utf-8"))
        for record in segment["records"]:
            records[record["id"]] = {**record, "region": segment["region"]}

    def encode(text: str) -> bytes:
        out = bytearray()
        position = 0
        import re

        for match in re.finditer(r"\{CTRL:01:([0-9A-F]{2})\}", text):
            out += encode(text[position : match.start()])
            out += bytes((0x01, int(match.group(1), 16)))
            position = match.end()
        for character in text[position:]:
            if character in mapping:
                out += bytes.fromhex(mapping[character])
            else:
                out += character.encode("shift_jis")
        return bytes(out)

    findings = []
    spans: list[tuple[int, int, str]] = []

    def check(record_id: str, address: int, text: str) -> None:
        want = encode(text) + NUL
        region = records[record_id]["region"]
        for name in (region, "arm9"):
            if name not in layout:
                continue
            start, _end, ram = layout[name]
            offset = start + (address - ram)
            if 0 <= offset <= len(after) - len(want) and after[offset : offset + len(want)] == want:
                spans.append((offset, offset + len(want), record_id))
                return
        findings.append({"id": record_id, "problem": "relocated text is not at the address its pointer holds"})

    for moved in relocation["moved"]:
        check(moved["id"], moved["new_address"], moved["text"])
        for pointer in moved["pointer_rom_offsets"]:
            if struct.unpack_from("<I", after, pointer)[0] != moved["new_address"]:
                findings.append({"id": moved["id"], "problem": f"pointer at {pointer:#x} was not updated"})
    for evicted in relocation["evicted"]:
        check(evicted["id"], evicted["new_address"], records[evicted["id"]]["korean_text"])

    spans.sort()
    for first, second in zip(spans, spans[1:]):
        if first[1] > second[0]:
            findings.append({"id": first[2], "problem": f"overlaps {second[2]}"})

    # Nothing outside a relocated slot may change, and a slot that only lost its
    # trailing spaces is fine - that is how the donors give their room back.
    relocated = {m["id"] for m in relocation["moved"]} | {e["id"] for e in relocation["evicted"]}
    touched = 0
    for record in records.values():
        if record["region"].startswith("text/") or not record.get("korean_text"):
            continue
        if record["id"] in relocated:
            continue
        start = layout[record["region"]][0] + int(record["locator"]["offset"])
        old = before[start : before.index(NUL, start)].rstrip(b" ")
        new = after[start : after.index(NUL, start)].rstrip(b" ")
        if old != new:
            touched += 1
            if len(findings) < 40:
                findings.append({"id": record["id"], "problem": "untouched record changed"})

    document = {
        "schema_version": 1,
        "moved": len(relocation["moved"]),
        "evicted": len(relocation["evicted"]),
        "skipped": relocation["skipped_count"],
        "unrelated_records_changed": touched,
        "finding_count": len(findings),
        "findings": findings,
    }
    args.report.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in document.items() if k != "findings"}, ensure_ascii=False, indent=2))
    for finding in findings[:10]:
        print("  ", finding)


if __name__ == "__main__":
    main()
