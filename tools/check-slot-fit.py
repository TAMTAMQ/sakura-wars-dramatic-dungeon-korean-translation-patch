"""Report translation records that do not fit their fixed source slot.

Capacity is the source byte length plus the zero padding that follows the NUL
terminator, matching the layout the prior Chinese patch used. Pass --sync-index
to refresh assets/translation/index.json segment hashes after manual edits.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

CONTROL_TOKEN = re.compile(r"\{CTRL:01:([0-9A-F]{2})\}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--sync-index", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def character_length(character: str) -> int:
    """Custom Hangul glyphs occupy two bytes, like the game's own kanji codes."""
    try:
        return len(character.encode("shift_jis"))
    except UnicodeEncodeError:
        return 2


def encoded_length(text: str) -> int:
    total = 0
    position = 0
    for match in CONTROL_TOKEN.finditer(text):
        total += sum(character_length(character) for character in text[position : match.start()]) + 2
        position = match.end()
    return total + sum(character_length(character) for character in text[position:])


def main() -> None:
    args = parse_args()
    index = json.loads(args.index.read_text(encoding="utf-8"))
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    source = args.source.read_bytes()
    arm9_offset = int.from_bytes(source[32:36], "little")
    overlays = {int(item["overlay_id"]): int(item["start"]) for item in inventory["arm9_overlays"]}

    findings = []
    applied = 0
    for entry in index["segments"]:
        path = args.index.parent / entry["path"]
        payload = path.read_bytes()
        if args.sync_index:
            entry["sha256"] = hashlib.sha256(payload).hexdigest()
            entry["record_count"] = len(json.loads(payload.decode("utf-8"))["records"])
        segment = json.loads(payload.decode("utf-8"))
        region = segment["region"]
        if region.startswith("text/"):
            continue
        base = arm9_offset if region == "arm9" else overlays[int(region.split(":")[1])]
        for record in segment["records"]:
            if not record["apply_translation"]:
                continue
            applied += 1
            start = base + int(record["locator"]["offset"])
            end = start + len(bytes.fromhex(record["source_hex"]))
            zero_end = end
            while zero_end < len(source) and source[zero_end] == 0:
                zero_end += 1
            capacity = zero_end - start - 1
            used = encoded_length(record["korean_text"])
            if used > capacity:
                findings.append(
                    {
                        "id": record["id"],
                        "capacity": capacity,
                        "used": used,
                        "over": used - capacity,
                        "source_text": record["source_text"],
                        "korean_text": record["korean_text"],
                    }
                )

    if args.sync_index:
        index["scope_record_count"] = sum(
            int(entry["record_count"]) for entry in index["segments"]
        )
        args.index.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = {
        "applied_translation_count": applied,
        "overflow_count": len(findings),
        "overflow": findings,
        "index_synced": bool(args.sync_index),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
