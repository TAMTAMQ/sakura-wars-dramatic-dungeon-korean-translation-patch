"""Bring translated lines back inside the width their text box can draw.

A slot check counts bytes, and Korean fits the byte budget easily because its
syllables cost the same two bytes a kanji does. What it does not fit is the box:
Hangul is full width everywhere, while the Japanese it replaces mixes full and
half width, so a line that passes the slot check can still run past the edge and
get clipped with no other sign that anything is wrong.

The box width is not recorded anywhere, so the longest source line in the same
file stands in for it - the original text has to fit, so the box is at least that
wide. The budget is one column below that maximum.

Shortening is done by substitution, not by cutting: each rule is a phrase and a
shorter phrase that says the same thing, and only as many are applied as the line
needs. A line that still does not fit is reported rather than truncated.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

CONTROL_TOKEN = re.compile(r"\{CTRL:01:[0-9A-F]{2}\}")

# Longer phrases first: "확인할 수 있습니다" must win over "있습니다".
SUBSTITUTIONS = [
    ("확인할 수 있습니다", "볼 수 있습니다"),
    ("사용할 수 있습니다", "쓸 수 있습니다"),
    ("이용할 수 있습니다", "쓸 수 있습니다"),
    ("선택할 수 있습니다", "고를 수 있습니다"),
    ("이동할 수 있습니다", "이동 가능합니다"),
    ("교환할 수 있습니다", "바꿀 수 있습니다"),
    ("되어 있습니다", "돼 있습니다"),
    ("하고 있습니다", "합니다"),
    ("되지 않습니다", "안 됩니다"),
    ("하지 않습니다", "안 합니다"),
    ("할 수 없습니다", "못 합니다"),
    ("발생합니다", "생깁니다"),
    ("표시됩니다", "나옵니다"),
    ("가능합니다", "됩니다"),
    ("확인하세요", "보세요"),
    ("사용하세요", "쓰세요"),
    ("주의하세요", "조심하세요"),
    ("그리고 ", "또 "),
    ("하지만 ", "단 "),
    ("그러나 ", "허나 "),
    ("그래서 ", "그래 "),
    ("다양한 ", "여러 "),
    ("여러 가지 ", "여러 "),
    ("대단히 ", "매우 "),
    ("~입니다", "~"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--segments", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--margin", type=int, default=1, help="columns kept below the file's longest source line")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def width(text: str) -> int:
    """Display width in half-width columns, the unit the font draws in."""
    total = 0
    for character in CONTROL_TOKEN.sub("", text):
        if character == "　":
            total += 2
            continue
        try:
            total += 1 if len(character.encode("shift_jis")) == 1 else 2
        except UnicodeEncodeError:
            total += 2
    return total


def shorten(line: str, budget: int) -> str:
    for long_form, short_form in SUBSTITUTIONS:
        if width(line) <= budget:
            break
        # Word-initial only: "하지만" as a word, never the tail of "강하지만".
        pattern = re.compile(r"(?<![가-힣])" + re.escape(long_form))
        if pattern.search(line):
            line = pattern.sub(short_form, line, count=1)
    return line


def main() -> None:
    args = parse_args()
    changed = []
    remaining = []
    for path in sorted(args.segments.glob("*.json")):
        segment = json.loads(path.read_text(encoding="utf-8"))
        longest = max(
            (width(line) for record in segment["records"] for line in (record.get("source_text") or "").split("\n")),
            default=0,
        )
        budget = longest - args.margin
        dirty = False
        for record in segment["records"]:
            korean = record.get("korean_text")
            if not korean:
                continue
            lines = korean.split("\n")
            rebuilt = [shorten(line, budget) if width(line) > budget else line for line in lines]
            for index, (before, after) in enumerate(zip(lines, rebuilt)):
                if width(before) <= budget:
                    continue
                entry = {
                    "id": record["id"],
                    "line": index,
                    "budget": budget,
                    "before": before,
                    "before_width": width(before),
                    "after": after,
                    "after_width": width(after),
                }
                (changed if width(after) <= budget else remaining).append(entry)
            if rebuilt != lines:
                if args.apply:
                    record["korean_text"] = "\n".join(rebuilt)
                    dirty = True
        if dirty:
            path.write_text(json.dumps(segment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    document = {
        "schema_version": 1,
        "applied": args.apply,
        "margin": args.margin,
        "fixed_count": len(changed),
        "remaining_count": len(remaining),
        "remaining": remaining,
        "fixed": changed,
    }
    args.report.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in document.items() if k not in ("remaining", "fixed")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
