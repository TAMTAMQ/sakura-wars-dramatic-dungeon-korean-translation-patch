#!/usr/bin/env python3
"""Build a side-by-side Japanese source/prior Chinese patch PNG gallery."""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path, PurePosixPath


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--source-png-root", type=Path, required=True)
    parser.add_argument("--prior-png-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def png_relative(record: dict) -> PurePosixPath | None:
    path = PurePosixPath(record["path"])
    if record["asset_kind"] == "game_specific_binary_image":
        return path.with_suffix(".png")
    if record["asset_kind"] == "nintendo_ds_ncgr_image":
        return path.with_suffix(".cells.png")
    return None


def href(from_dir: Path, target: Path) -> str:
    return Path(os.path.relpath(target, from_dir)).as_posix()


def main() -> None:
    args = parse_args()
    index = json.loads(args.index.read_text(encoding="utf-8"))
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    cards = []
    counts: dict[str, int] = {}
    for record in index["records"]:
        relative = png_relative(record)
        if relative is None:
            continue
        source = (args.source_png_root.resolve() / relative).resolve()
        prior = (args.prior_png_root.resolve() / relative).resolve()
        if not source.exists() or not prior.exists():
            raise ValueError(f"missing comparison PNG: {record['id']}")
        category = record["category"]
        counts[category] = counts.get(category, 0) + 1
        source_href = html.escape(href(output.parent, source))
        prior_href = html.escape(href(output.parent, prior))
        label = html.escape(record["path"])
        cards.append(
            f"<section data-category='{html.escape(category)}'><h2>{label}</h2>"
            f"<div class='pair'><figure><a href='{source_href}'><img src='{source_href}'></a>"
            f"<figcaption>일본어 원본</figcaption></figure>"
            f"<figure><a href='{prior_href}'><img src='{prior_href}'></a>"
            f"<figcaption>중국어 패치</figcaption></figure></div>"
            f"<p>변경 바이트: {int(record['changed_byte_count']):,}</p></section>"
        )
    buttons = " ".join(
        f"<button onclick=\"filterCategory('{html.escape(name)}')\">{html.escape(name)} ({count})</button>"
        for name, count in sorted(counts.items())
    )
    document = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>일본어/중국어 이미지 비교</title><style>
body{{font-family:Malgun Gothic,sans-serif;background:#202124;color:#eee;margin:24px}}
nav{{position:sticky;top:0;background:#202124;padding:12px 0;z-index:2}}button{{margin:3px;padding:7px 12px}}
section{{background:#303134;margin:16px 0;padding:16px;border-radius:8px}}.pair{{display:flex;gap:20px;flex-wrap:wrap}}
figure{{margin:0;min-width:280px}}img{{image-rendering:pixelated;min-width:224px;max-width:720px;height:auto;background:#111}}
figcaption{{text-align:center;margin-top:6px}}h2{{font-size:16px;word-break:break-all}}
</style><script>function filterCategory(c){{for(const e of document.querySelectorAll('section'))e.hidden=c&&e.dataset.category!==c}}</script>
</head><body><h1>일본어 원본 / 중국어 패치 이미지 비교</h1>
<nav><button onclick="filterCategory('')">전체 ({len(cards)})</button> {buttons}</nav>
{''.join(cards)}</body></html>\n"""
    output.write_text(document, encoding="utf-8")
    print(json.dumps({"output": str(output), "record_count": len(cards), "category_counts": counts}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
