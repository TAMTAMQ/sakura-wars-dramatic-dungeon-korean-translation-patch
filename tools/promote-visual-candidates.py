"""Add decodable images found outside the prior-patch scope to the visual index.

tools/survey-visual-candidates.py finds every image the converters can render.
The prior Chinese patch never touched some of them, so they were missing from
`assets/visual/index.json` even though they carry Japanese text.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument(
        "--exclude-category",
        action="append",
        default=[],
        help="skip a directory, e.g. face portraits that carry no text",
    )
    parser.add_argument("--apply", action="store_true", help="write the index instead of reporting")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    survey = json.loads(args.candidates.read_text(encoding="utf-8"))
    index = json.loads(args.index.read_text(encoding="utf-8"))
    known = {record["path"] for record in index["records"]}
    excluded = set(args.exclude_category)

    added = []
    for candidate in survey["candidates"]:
        if candidate["path"] in known or candidate["category"] in excluded:
            continue
        added.append(
            {
                "id": candidate["id"],
                "path": candidate["path"],
                "file_id": candidate["file_id"],
                "category": candidate["category"],
                "asset_kind": candidate["asset_kind"],
                "source_size": candidate["source_size"],
                "source_sha256": candidate["source_sha256"],
                "prior_target_size": None,
                "prior_target_sha256": None,
                "changed_byte_count": None,
                "changed_run_count": None,
                "first_changed_offset": None,
                "last_changed_offset": None,
                "extraction_status": "pending",
                "translation_status": "untranslated",
                "replacement_path": None,
                "review_status": "untranslated",
                "scope_origin": "full_nitrofs_image_survey",
            }
        )

    if args.apply:
        index["records"].extend(added)
        index["records"].sort(key=lambda record: record["path"])
        index["record_count"] = len(index["records"])
        index["population_state"] = "prior_patch_scope_plus_full_nitrofs_image_survey"
        args.index.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = {
        "added_count": len(added),
        "index_record_count": len(index["records"]),
        "applied": bool(args.apply),
        "excluded_categories": sorted(excluded),
        "added_by_category": {},
    }
    for record in added:
        report["added_by_category"][record["category"]] = report["added_by_category"].get(record["category"], 0) + 1
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
