"""Apply high-confidence terminology/title corrections and record every edit.

The tables below have to track the decisions that were adopted after this ran
the first time. A stale entry does not merely go unused: rerunning the tool
writes it back over the current translation.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


TITLE_TRANSLATIONS = {
    "君あるがため、です。ふふっ。": "「그대가 있기에」입니다.",
    "君あるがため、ですわね。": "「그대가 있기에」군요.",
    "君あるがため、ですね。": "「그대가 있기에」네요.",
    "君あるがため、ってか？": "「그대가 있기에」인가?",
    "君あるがため……なのかな？": "「그대가 있기에」……일까?",
    "君あるがため、やな。": "「그대가 있기에」구나.",
    "君あるがため、ですねー。": "「그대가 있기에」네요~.",
    "君あるがため、なのかな。": "「그대가 있기에」……일까.",
    "君あるがため……なのかな。": "「그대가 있기에」……일까.",
    "君あるがため、でしたっけ？": "「그대가 있기에」였죠?",
    "君あるがため、だな。": "「그대가 있기에」군.",
    "君あるがため、ってやつかね。": "「그대가 있기에」라는 건가.",
    "君あるがため、ですわ。": "「그대가 있기에」예요.",
    "君あるがため……なんちて！": "「그대가 있기에」……랄까!",
    "君あるがため……だね。": "「그대가 있기에」……구나.",
    "君あるがため……だぞっ!!": "「그대가 있기에」……다!!",
    "君あるがため……ですね。": "「그대가 있기에」……네요.",
    "君あるがため……ね。": "「그대가 있기에」……네.",
    "君あるがため……ってやつだな。": "「그대 있기에」……라는 거군.",
    "君あるがため、ってやつだな。": "「그대가 있기에」라는 거군.",
    "君あるがため、ね。": "「그대가 있기에」……네.",
    "君あるがため、ですよね。": "「그대가 있기에」, 맞죠.",
    "君あるがため、ですぅ。": "「그대가 있기에」예요~.",
    "君あるがため……ねん。": "「그대가 있기에」……라네.",
    "君あるがため……なんです。": "「그대가 있기에」…입니다.",
    "君あるがため……": "그대가 있기에……",
}

TERM_REPLACEMENTS = {
    "노트르담 사원": "노트르담 대성당",
    "대에도 대공동": "오에도 대공동",
    "작전 지령실": "작전지령실",
    "둔족": "둔화",
    "준족": "신속",
    "【통상 공격】": "【통상공격】",
    "【일반 공격】": "【통상공격】",
    "산성내성": "산성 내성",
}

DIRECT_TRANSLATIONS = {
    "text/CAMPDATA.DAT#0465": "행동할 수 없게 됩니다.　　　　　",
    "text/CITR.DAT#1150": "「싸우는 용맹한 모습을, 이치로 짱이",
    "text/CITR.DAT#2425": "도시 에너지에서 태어난 존재.",
    "text/CITR.DAT#2426": "체내에서 부글부글 소리 내는 기포는,",
    "text/CITR.DAT#2427": "희생자를 소화할 때 생긴 것.",
    "text/CITR.DAT#2450": "하지만 본질은 산업 폐기물에 고인",
    "text/CITR.DAT#2451": "도시 에너지에서 태어난 존재.",
    "text/CITR.DAT#2452": "흡수한 물질이 부패해 화학 반응을",
    "text/CITR.DAT#2453": "일으켜 붉게 물들어 섬뜩하다.",
    "text/CITR.DAT#2476": "하지만 본질은 산업 폐기물에 고인",
    "text/CITR.DAT#2477": "도시 에너지에서 태어난 존재.",
    "text/CITR.DAT#2478": "도시에서 흘러나온 부정적 힘을 몸에",
    "text/CITR.DAT#2479": "받아들여 시커멓게 변했다.",
    "text/CITR.DAT#2489": "받을 때가 있다. 속은 시커멓다.",
    "arm9@00178E34": "누굴 감쌀까?",
    "arm9@001AAFA0": "카야마 씨가……둘이나 있어!?",
    "arm9-overlay:24@00004EB0": "누구냐!?",
    "arm9@00162E48": "%d층으로 되돌려졌다!",
    "arm9@0017CDCC": "%s을 쓰러뜨렸다!",
    "arm9@00186E8C": "%s의 머리던지기!",
    "arm9@001872D0": "%s는 저주받았다!",
    "arm9@00187560": "공격은 빗나갔다!",
    "arm9@00187714": "광란이 됐다!",
    "arm9@0018799C": "%s을 빨아들였다!",
    "arm9@00189040": "오리히메 군!",
    "arm9@00189130": "오리히메 씨!",
    "arm9@001A9C94": "%s는 저주받았다!",
    "arm9@001A9F54": "대지가 흔들린다!",
    "arm9-overlay:23@00005B6C": "이젠 도망칠 수밖에 없어!",
    "arm9@0018B754": "스바루는 잠꼬대는\n하지 않아……",
    "arm9@00198D40": "날리기군",
    "arm9@00198E80": "투명군",
    "arm9@00199470": "분노군",
    "arm9-overlay:2@00003B98": "오오가미 사령관.",
    "arm9-overlay:27@00002910": "스바루는 느낀다……",
    "arm9@0017C988": "제도 사령실",
    "arm9@0017C994": "파리 사령실",
    "arm9@0017C9A0": "뉴욕 사령실",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--segments", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    changes: list[dict[str, str]] = []
    for path in sorted(args.segments.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        dirty = False
        for record in payload["records"]:
            before = record["korean_text"]
            if not isinstance(before, str):
                continue
            after = before
            source = record["source_text"]

            for old, new in TERM_REPLACEMENTS.items():
                after = after.replace(old, new)

            # Project terminology intentionally renders the title 司令 as
            # 사령관. Do not alter 司令室 compounds, which are facility names.
            if "司令" in source and "司令室" not in source:
                after = re.sub(r"부사령(?!관)", "부사령관", after)
                after = re.sub(r"(?<!부)사령(?!관|실)", "사령관", after)

            if source in TITLE_TRANSLATIONS:
                after = TITLE_TRANSLATIONS[source]

            if record["id"] in DIRECT_TRANSLATIONS:
                after = DIRECT_TRANSLATIONS[record["id"]]

            if after != before:
                record["korean_text"] = after
                changes.append(
                    {
                        "file": path.name,
                        "id": record["id"],
                        "source_text": source,
                        "before": before,
                        "after": after,
                    }
                )
                dirty = True
        if dirty:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    args.log.parent.mkdir(parents=True, exist_ok=True)
    previous = json.loads(args.log.read_text(encoding="utf-8")) if args.log.exists() else []
    previous.extend(changes)
    args.log.write_text(json.dumps(previous, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"changed_record_count": len(changes), "logged_total": len(previous)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
