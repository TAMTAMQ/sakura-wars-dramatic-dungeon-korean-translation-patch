"""Build human-readable translation QA and slot-reallocation reports."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--segments", type=Path, required=True)
    parser.add_argument("--overflow", type=Path, required=True)
    parser.add_argument("--edits", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def visible(text: str) -> str:
    return text.replace("\r", "\\r").replace("\n", "\\n")


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    slot = json.loads(args.overflow.read_text(encoding="utf-8"))
    edits = json.loads(args.edits.read_text(encoding="utf-8"))
    edited_ids = {item["id"] for item in edits}

    records = []
    for path in sorted(args.segments.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        records.extend(payload["records"])

    with (args.output_dir / "slot-reallocation.tsv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["id", "capacity", "used", "over", "edited_in_review", "source_text", "korean_text"])
        for item in slot["overflow"]:
            writer.writerow(
                [
                    item["id"], item["capacity"], item["used"], item["over"],
                    "yes" if item["id"] in edited_ids else "no",
                    visible(item["source_text"]), visible(item["korean_text"]),
                ]
            )

    with (args.output_dir / "translation-edits.tsv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["file", "id", "source_text", "before", "after"])
        for item in edits:
            writer.writerow([item["file"], item["id"], visible(item["source_text"]), visible(item["before"]), visible(item["after"])])

    shortening = []
    for item in records:
        source = item.get("source_text", "")
        korean = item.get("korean_text")
        if not item.get("apply_translation") or not isinstance(korean, str):
            continue
        source_plain = re.sub(r"\{[^}]+\}", "", source).replace("　", "")
        korean_plain = re.sub(r"\{[^}]+\}", "", korean).replace(" ", "")
        if len(source_plain) < 10:
            continue
        ratio = len(korean_plain) / max(1, len(source_plain.replace(" ", "")))
        if ratio <= 0.48:
            shortening.append((ratio, item["id"], source, korean))
    shortening.sort()
    with (args.output_dir / "shortening-candidates.tsv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["length_ratio", "id", "source_text", "korean_text"])
        for ratio, record_id, source, korean in shortening:
            writer.writerow([f"{ratio:.3f}", record_id, visible(source), visible(korean)])

    region_counts = Counter(item["id"].split("@")[0] for item in slot["overflow"])
    edited_overflow = [item for item in slot["overflow"] if item["id"] in edited_ids]
    untranslated = [item for item in records if not item.get("apply_translation")]
    meaningful_untranslated = [
        item for item in untranslated if item.get("source_text", "").strip() not in {"", "『』", "Lv.00"}
    ]
    kana_residue = [
        item["id"] for item in records
        if isinstance(item.get("korean_text"), str) and re.search(r"[ぁ-ゖァ-ヺ]", item["korean_text"])
    ]
    placeholder_mismatch = []
    token_pattern = re.compile(r"%(?:\d+\$)?[sdif]|\{CTRL:[^}]+\}")
    for item in records:
        korean = item.get("korean_text")
        if isinstance(korean, str) and token_pattern.findall(item.get("source_text", "")) != token_pattern.findall(korean):
            placeholder_mismatch.append(item["id"])

    lines = [
        "# 번역 검수 보고서",
        "",
        "## 결과 요약",
        "",
        f"- 전체 레코드: {len(records):,}건",
        f"- 적용 번역: {sum(bool(item.get('apply_translation')) for item in records):,}건",
        f"- 이번 검수 교정: {len(edits):,}건",
        f"- 고정 슬롯 초과: {slot['overflow_count']:,}건",
        f"- 이번 검수에서 수정한 문장 중 슬롯 초과: {len(edited_overflow):,}건",
        f"- 가나 잔존: {len(kana_residue):,}건",
        f"- 서식/치환 토큰 불일치: {len(placeholder_mismatch):,}건",
        f"- 미적용 레코드: {len(untranslated):,}건(의미 있는 미번역 {len(meaningful_untranslated):,}건; 나머지는 공백·빈 괄호·Lv.00 자리표시자)",
        f"- 길이 비율상 축약 의심 후보: {len(shortening):,}건(문장 분할 때문에 잡힌 정상 항목 포함)",
        "",
        "## 이번 교정 범위",
        "",
        "- 호칭 `さん/くん` 누락 복원 및 프로젝트 방침에 따라 `司令/司令室`를 `사령관/사령관실`로 통일",
        "- `노트르담 대성당`, `오에도 대공동`, `작전지령실`, `둔화/신속`, `통상공격` 표기 통일",
        "- 작품명 `君あるがため`를 `그대 있기에`로 통일하고 화자별 말끝 유지",
        "- 도움말의 의미 반전 1건과 페이스트종 도감의 오배치·오역 구간 교정",
        "- 슬롯 때문에 거칠어진 발명품명 `날아감군/안보임군/도하츠텐군`을 `날리기군/투명군/분노군`으로 교정",
        "- 원문의 물음표·느낌표가 누락된 명백한 항목 복원",
        "",
        "## 슬롯 재배치 필요 수",
        "",
        "| 영역 | 건수 |",
        "|---|---:|",
    ]
    lines.extend(f"| `{region}` | {count} |" for region, count in region_counts.most_common())
    lines.extend(
        [
            "",
            "상세 ID·원문·번역·capacity/used/over 값은 `slot-reallocation.tsv`에 전부 수록했습니다.",
            "`edited_in_review=yes`는 이번 교정 때문에 온전한 번역을 유지하려면 우선 재배치해야 하는 항목입니다.",
            "",
            "## 참고",
            "",
            "`shortening-candidates.tsv`는 기계적 길이 비율 후보입니다. 앞뒤 레코드에 걸쳐 한 문장이 이어지는 구조가 많아 목록 전체를 오역으로 보면 안 됩니다.",
            "모든 번역의 `cross_review_required` 상태는 유지했습니다. 실제 게임 화면에서 줄바꿈·화자 말투를 최종 확인한 뒤 승인 상태를 바꾸는 것이 안전합니다.",
            "",
        ]
    )
    (args.output_dir / "translation-review.md").write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({"overflow": slot["overflow_count"], "edits": len(edits), "shortening_candidates": len(shortening)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
