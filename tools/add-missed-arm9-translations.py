#!/usr/bin/env python3
"""Add pointer-addressed ARM9 strings missed by the original NUL-only scan."""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path


RECORDS = [
    (
        "arm9@00162B84",
        1452932,
        "Wi-Fiコネクションでは、DSカードと\nDS本体をセットで扱います。\nDSカードにDS本体のWi-Fiユーザー\n情報を保存して、Wi-Fiコネクションに\n接続しますか？",
        "Wi-Fi 커넥션에서는 DS 카드와\nDS 본체를 한 세트로 취급합니다.\nDS 카드에 DS 본체의 Wi-Fi 사용자\n정보를 저장하고 Wi-Fi 커넥션에\n접속하시겠습니까?",
    ),
    ("arm9@0017614C", 1532236, "銀色の薬", "은색 약"),
    ("arm9@0017D7A8", 1562536, "持ち物を売る", "소지품을 판다"),
    ("arm9@0017DB10", 1563408, "%sだ！", "%s다!"),
    (
        "arm9@0017E094",
        1564820,
        "セーブデータを書き込んでいます。",
        "세이브 데이터를 기록 중입니다.",
    ),
    (
        "arm9@00183698",
        1586840,
        "ゲーム中に再生されるボイスの　　\n音量を調整することができます。　\n数字が大きいほどボイスの音量は、\n大きくなります。　　　　　　　　\nＯＦＦを選ぶと再生されません。　",
        "게임 중에 재생되는 보이스의　　\n음량을 조절할 수 있습니다.　\n숫자가 클수록 보이스의 음량은,\n커집니다.　　　　　　　　\nOFF를 고르면 재생되지 않습니다.",
    ),
    ("arm9@00183A88", 1587848, "狼虎滅却・超新星", "낭호멸각・초신성"),
    (
        "arm9@00194320",
        1655584,
        "あ……あれ？\nボクは、いったい……？",
        "아……어라?\n난, 대체……?",
    ),
    (
        "arm9@0019F140",
        1700160,
        "読むと、部屋にあるすべてのワナが\n消滅する。\n",
        "읽으면 방 안의 모든 함정이\n사라진다.\n",
    ),
    (
        "arm9@0019F21C",
        1700380,
        "けだるくなる霊水。\nとてもけだるいのでスピードがにぶくなる。\n",
        "몸이 나른해지는 영수.\n너무 나른해져 속도가 느려진다.\n",
    ),
    (
        "arm9@0019FF04",
        1703684,
        "「撃つ」コマンドで直線上の\n遠くの魔物を攻撃できる。\n鉛の弾より与えるダメージが\n大きい。\n",
        "「쏘기」 커맨드로 직선상의\n먼 마물을 공격할 수 있다.\n납 탄환보다 주는 대미지가\n크다.\n",
    ),
    (
        "arm9@001A6554",
        1729876,
        "ほとんど氷に近い、冷たいジュース。\n飲むと、あまりの冷たさに、正面に\n氷の息を吐いてしまう。氷の息に\n当たった相手は凍り付いてしまう。\n",
        "거의 얼음에 가까운 차가운 주스.\n마시면 너무 차가워서 정면으로\n얼음 숨결을 내뿜는다. 얼음 숨결에\n맞은 상대는 얼어붙는다.\n",
    ),
    ("arm9@001A9830", 1742896, "銀色の", "은색의"),
    (
        "arm9@001A4764",
        1722212,
        "\u6e80\u8179\u5ea6\u304c\x01\x0b\uff15\uff10\u30dd\u30a4\u30f3\u30c8\x01\x01\u56de\u5fa9\u3059\u308b\u3002\n\uff28\uff30\u3082\x01\x0b\uff13\uff10\u30dd\u30a4\u30f3\u30c8\x01\x01\u56de\u5fa9\u3059\u308b\u3002\n\u6e80\u8179\u306e\u6642\u306b\u98df\u3079\u308b\u3068\u3001\n\u6700\u5927\u6e80\u8179\u5ea6\u304c\x01\x0b\uff13\u30dd\u30a4\u30f3\u30c8\x01\x01\u4e0a\u304c\u308b\u3002\n",
        "\ud3ec\ub9cc\ub3c4\uac00{CTRL:01:0B}50\ud3ec\uc778\ud2b8{CTRL:01:01} \ud68c\ubcf5\ub41c\ub2e4.\nHP\ub3c4{CTRL:01:0B}30\ud3ec\uc778\ud2b8{CTRL:01:01} \ud68c\ubcf5\ub41c\ub2e4.\n\ubc30\ubd80\ub97c \ub54c \uba39\uc73c\uba74,\n\ucd5c\ub300 \ud3ec\ub9cc\ub3c4\uac00{CTRL:01:0B}3\ud3ec\uc778\ud2b8{CTRL:01:01} \uc624\ub978\ub2e4.\n",
    ),
    (
        "arm9@001AD2C8",
        1757896,
        "対戦プレイの賞品が届いています。\n倉庫を出る前に忘れずに回収して\nください。",
        "대전 플레이 상품이 도착했습니다.\n창고를 나가기 전에 잊지 말고\n회수하세요.",
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--segment", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rom = args.source.read_bytes()
    arm9_offset = struct.unpack_from("<I", rom, 32)[0]
    arm9_size = struct.unpack_from("<I", rom, 44)[0]
    arm9 = rom[arm9_offset : arm9_offset + arm9_size]
    document = json.loads(args.segment.read_text(encoding="utf-8"))
    records = document["records"]
    by_id = {record["id"]: record for record in records}

    added = 0
    for record_id, offset, source_text, korean_text in RECORDS:
        source_raw = source_text.encode("shift_jis")
        actual = arm9[offset : offset + len(source_raw)]
        if actual != source_raw or arm9[offset + len(source_raw)] != 0:
            raise ValueError(f"source verification failed: {record_id}")
        expected_id = f"arm9@{offset:08X}"
        if record_id != expected_id:
            raise ValueError(f"id/offset mismatch: {record_id} != {expected_id}")
        new_record = {
            "id": record_id,
            "locator": {"offset": offset},
            "source_text": source_text,
            "source_hex": source_raw.hex(),
            "korean_text": korean_text,
            "apply_translation": True,
            "review_status": "cross_review_required",
            "note": "포인터 참조 문자열: 기존 NUL 경계 추출에서 누락되어 복구",
        }
        if record_id in by_id:
            # Only the recovered locator and source are this tool's to own; the
            # Korean side moves on with review and must not be reset here.
            existing = by_id[record_id]
            for field in ("locator", "source_text", "source_hex"):
                if existing[field] != new_record[field]:
                    raise ValueError(f"existing record differs at {field}: {record_id}")
            continue
        records.append(new_record)
        by_id[record_id] = new_record
        added += 1

    records.sort(key=lambda record: int(record["locator"]["offset"]))
    args.segment.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"added={added} total={len(records)}")


if __name__ == "__main__":
    main()
