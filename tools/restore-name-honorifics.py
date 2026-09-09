"""Put back the honorific the source attaches to a name.

The Japanese text almost always addresses people as `<name>さん` / `くん` /
`ちゃん` / `様`, and a good part of the translation dropped that, leaving a bare
name where the original is polite or familiar. Restoring it changes the particle
that follows, because Korean particles agree with the final consonant of the
word in front of them, and `씨` ends open where `군`, `짱` and `님` do not.

Only unambiguous cases are touched: the Korean name has to appear exactly once,
must not already carry an honorific or a rank, and must not be the first half of
a full name.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

NAMES = {
    "大神": "오오가미", "大河": "타이가", "さくら": "사쿠라", "すみれ": "스미레",
    "マリア": "마리아", "アイリス": "아이리스", "紅蘭": "홍란", "カンナ": "칸나",
    "レニ": "레니", "織姫": "오리히메", "エリカ": "에리카", "グリシーヌ": "글리신",
    "コクリコ": "코쿠리코", "ロベリア": "로벨리아", "花火": "하나비", "ラチェット": "라쳇",
    "かえで": "카에데", "米田": "요네다", "加山": "카야마", "ジェミニ": "제미니",
    "サジータ": "사지타", "ダイアナ": "다이아나", "リカリッタ": "리카리타",
    "スバル": "스바루", "昴": "스바루", "新次郎": "신지로", "かすみ": "카스미",
    "琴音": "코토네", "椿": "츠바키", "菊之丞": "키쿠노조", "斧彦": "오노히코",
    "メル": "멜", "プラム": "플럼", "ジャンヌ": "잔느", "サニー": "서니",
    "由里": "유리", "杏里": "안리",
}
# Longest suffix first: さん must not win over ちゃん.
SUFFIXES = [("ちゃん", "짱"), ("さま", "님"), ("さん", "씨"), ("くん", "군"), ("様", "님"), ("はん", "씨")]
CLOSED = {"군", "짱", "님"}
# Second halves of full names, where an honorific would land in the middle.
SECOND_HALF = (
    "이치로", "신지로", "스바루", "사쿠라", "오리히메", "카프리스", "레종", "다르크",
    "알타이르", "블뢰메르", "카를리니", "선라이즈", "아리에스", "와인버그", "폰티느",
    "타치바나", "밀히슈트라세", "서니사이드", "스패니얼", "잇키", "유이치", "노리미치",
    "키쿠노조", "오노히코", "카에데", "카스미", "코토네", "츠바키", "하나비", "안리", "유리",
)
BLOCKED = ("씨", "군", "짱", "님", "대장", "사령관", "부사령관", "선생")
TO_CLOSED = {"가": "이", "는": "은", "를": "을", "와": "과", "야": "아"}
TO_OPEN = {"이": "가", "은": "는", "을": "를", "과": "와", "아": "야"}
BOUNDARY = re.compile(r"[^가-힣]|$")


def fix_particle(tail: str, suffix: str) -> str:
    """Make the particle after the honorific agree with it."""
    if not tail:
        return tail
    table = TO_CLOSED if suffix in CLOSED else TO_OPEN
    if tail[0] in table and BOUNDARY.match(tail[1:2] or ""):
        return table[tail[0]] + tail[1:]
    if suffix in CLOSED and tail.startswith("로") and BOUNDARY.match(tail[1:2] or ""):
        return "으로" + tail[1:]
    if suffix not in CLOSED and tail.startswith("으로"):
        return "로" + tail[2:]
    return tail


def restore(source: str, korean: str) -> str:
    for japanese, name in NAMES.items():
        for japanese_suffix, suffix in SUFFIXES:
            if japanese + japanese_suffix not in source:
                continue
            if re.search(re.escape(name) + r"\s*" + suffix, korean):
                continue
            if korean.count(name) != 1:
                continue
            position = korean.index(name) + len(name)
            tail = korean[position:]
            if any(tail.lstrip(" ").startswith(half) for half in SECOND_HALF):
                continue
            if any(tail.lstrip(" ").startswith(word) for word in BLOCKED):
                continue
            return korean[:position] + " " + suffix + fix_particle(tail, suffix)
    return korean


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--segments", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    changed = []
    for path in sorted(args.segments.glob("*.json")):
        segment = json.loads(path.read_text(encoding="utf-8"))
        dirty = False
        for record in segment["records"]:
            korean = record.get("korean_text")
            if not korean:
                continue
            restored = restore(record.get("source_text") or "", korean)
            if restored == korean:
                continue
            changed.append({"id": record["id"], "before": korean, "after": restored})
            if args.apply:
                record["korean_text"] = restored
                dirty = True
        if dirty:
            path.write_text(json.dumps(segment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.report.write_text(
        json.dumps({"schema_version": 1, "applied": args.apply, "count": len(changed), "changed": changed},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"applied": args.apply, "count": len(changed)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
