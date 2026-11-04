#!/usr/bin/env python3
"""Turn the name-entry kana keyboard into a fixed Hangul syllable keyboard.

Every key draws and inserts the 4-byte ARM9 string its layout cell points to,
so translating those strings replaces both what a key shows and what it types.
The ゛/゜ cells are conversion keys (type 0x0E/0x0F); they become ordinary
insert keys (type 0x10) so they type their own syllable instead of converting
the previous one through the dakuten table.

The kana strings are referenced only by the keyboard and its conversion
tables; the digits are shared with the friend-code pad and stay as they are.
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

RAM = 0x02000000

# Hiragana page → "한글1": open syllables in the kana grid's a/i/u/e/o order.
PAGE1 = dict(zip(
    "あいうえおはひふへほかきくけこまみむめもさしすせそやゆよたちつてとらりるれろなにぬねのわをん゛゜ぁぃぅぇぉゃゅょっ",
    "아이우에오하히후해호가기구게고마미무메모사시수세소야유요다디두데도라리루레로나니누네노와위의바파자지주제조어여서애",
))
# Katakana page → "한글2": aspirated syllables, then syllables with a final
# consonant that names need. ゛/゜ point at one string each, so both pages
# show page 1's syllables there.
PAGE2 = dict(zip(
    "アイウエオハヒフヘホカキクケコマミムメモサシスセソヤユヨタチツテトラリルレロナニヌネノワヲンァィゥェォャュョッ",
    "카키쿠케코타토츠치차김박최정강민준영은현성진연상원훈석철동경혜선용환명한윤범형인순숙남송신권장임안홍전문양손일",
))
LABELS = {"かな ": "한글1 ", "カナ ": "한글2 "}

KEY_STRINGS = (0x155A08, 0x155DA8)
LAYOUT_TABLES = (0x155E08, 0x157900)
CONVERSION_TYPES = {0x0E, 0x0F}
INSERT_TYPE = 0x10


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--segment", type=Path, required=True)
    parser.add_argument("--patches", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rom = args.source.read_bytes()
    arm9_offset, _entry, _ram, arm9_size = struct.unpack_from("<4I", rom, 0x20)
    arm9 = rom[arm9_offset : arm9_offset + arm9_size]

    slots: dict[str, int] = {}
    for offset in range(*KEY_STRINGS, 4):
        raw = arm9[offset : arm9.index(b"\0", offset)]
        if raw:
            slots[raw.decode("shift_jis")] = offset

    syllables = {**PAGE1, **PAGE2}
    if len(set(syllables.values())) != len(syllables):
        raise ValueError("a syllable appears on more than one key")
    for kana in syllables:
        offset = slots[kana]
        for position in range(0, len(arm9) - 3, 4):
            if struct.unpack_from("<I", arm9, position)[0] == RAM + offset:
                if not LAYOUT_TABLES[0] <= position < LAYOUT_TABLES[1]:
                    raise ValueError(f"key string is used outside the keyboard: {kana} at {position:#x}")

    document = json.loads(args.segment.read_text(encoding="utf-8"))
    records = document["records"]
    by_id = {record["id"]: record for record in records}
    added = updated = 0
    for kana, syllable in syllables.items():
        offset = slots[kana]
        record_id = f"arm9@{offset:08X}"
        source_raw = kana.encode("shift_jis")
        record = {
            "id": record_id,
            "locator": {"offset": offset},
            "source_text": kana,
            "source_hex": source_raw.hex(),
            "korean_text": syllable,
            "apply_translation": True,
            "review_status": "cross_review_required",
            "note": "이름 입력 자판의 키 글자 (add-hangul-keyboard.py)",
        }
        if record_id in by_id:
            if by_id[record_id]["source_hex"] != record["source_hex"]:
                raise ValueError(f"existing record differs: {record_id}")
            if by_id[record_id]["korean_text"] != syllable:
                by_id[record_id]["korean_text"] = syllable
                updated += 1
            continue
        records.append(record)
        by_id[record_id] = record
        added += 1
    for record in records:
        if record["source_text"] in LABELS and record["korean_text"] != LABELS[record["source_text"]]:
            record["korean_text"] = LABELS[record["source_text"]]
            updated += 1
    records.sort(key=lambda record: int(record["locator"]["offset"]))
    # Segments are hashed as LF bytes (.gitattributes); write_text would emit
    # CRLF on Windows.
    args.segment.write_bytes((json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    patches = []
    for position in range(LAYOUT_TABLES[0], LAYOUT_TABLES[1], 4):
        pointer, key_type = struct.unpack_from("<II", arm9, position)
        if key_type in CONVERSION_TYPES and pointer in {RAM + slots["゛"], RAM + slots["゜"]}:
            patches.append(
                {
                    "region": "arm9",
                    "offset": position + 4,
                    "expected_hex": struct.pack("<I", key_type).hex(),
                    "replacement_hex": struct.pack("<I", INSERT_TYPE).hex(),
                    "note": "이름 입력 자판: 탁점/반탁점 변환 키를 글자 입력 키로",
                }
            )
    args.patches.write_bytes(
        (json.dumps({"schema_version": 1, "patches": patches}, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    print(f"added={added} updated={updated} patches={len(patches)}")


if __name__ == "__main__":
    main()
