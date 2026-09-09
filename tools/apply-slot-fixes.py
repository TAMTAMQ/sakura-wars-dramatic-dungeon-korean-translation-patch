"""Apply shortened translations from a TSV keyed by the slot-fit report index."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

CONTROL_TOKEN = re.compile(r"\{CTRL:01:([0-9A-F]{2})\}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--todo", type=Path, required=True)
    parser.add_argument("--fixes", type=Path, nargs="+", required=True)
    parser.add_argument("--index", type=Path, required=True)
    return parser.parse_args()


def character_length(character: str) -> int:
    try:
        return len(character.encode("shift_jis"))
    except UnicodeEncodeError:
        return 2


def encoded_length(text: str) -> int:
    total = 0
    position = 0
    for match in CONTROL_TOKEN.finditer(text):
        total += sum(character_length(c) for c in text[position : match.start()]) + 2
        position = match.end()
    return total + sum(character_length(c) for c in text[position:])


def main() -> None:
    args = parse_args()
    todo = {}
    for line in args.todo.read_text(encoding="utf-8").splitlines():
        index, record_id, capacity, _over, _source, _korean = line.split("\t")
        todo[int(index)] = (record_id, int(capacity))

    capacities = {record_id: capacity for record_id, capacity in todo.values()}
    replacements: dict[str, str] = {}
    for path in args.fixes:
        # Fixes travel as JSON (record id -> text). Several records are
        # multi-line, and a line-based format loses those newlines.
        for record_id, text in json.loads(path.read_text(encoding="utf-8")).items():
            capacity = capacities[record_id]
            if encoded_length(text) > capacity:
                raise ValueError(f"{record_id} needs {encoded_length(text)} bytes but its slot holds {capacity}")
            replacements[record_id] = text

    index = json.loads(args.index.read_text(encoding="utf-8"))
    applied = 0
    for entry in index["segments"]:
        path = args.index.parent / entry["path"]
        segment = json.loads(path.read_text(encoding="utf-8"))
        changed = False
        for record in segment["records"]:
            if record["id"] in replacements:
                record["korean_text"] = replacements[record["id"]]
                changed = True
                applied += 1
        if changed:
            payload = (json.dumps(segment, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            path.write_bytes(payload)
            entry["sha256"] = hashlib.sha256(payload).hexdigest()
    args.index.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"requested": len(replacements), "applied": applied}, ensure_ascii=False))


if __name__ == "__main__":
    main()
