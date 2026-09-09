#!/usr/bin/env python3
"""Promote prior-patch image/CPK differences into a versioned asset scope."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    comparison = json.loads(args.comparison.read_text(encoding="utf-8"))
    records = []
    for item in comparison["nitrofs_comparison"]["changed_files"]:
        path = item["path"]
        category = path.split("/", 1)[0]
        if category not in {"cinema", "icat", "lips", "cpk"}:
            continue
        suffix = Path(path).suffix.lower()
        if category == "cpk":
            asset_kind = "cri_cpk_cutscene_container_candidate"
        elif suffix == ".ncgr":
            asset_kind = "nintendo_ds_ncgr_image"
        else:
            asset_kind = "game_specific_binary_image"
        records.append(
            {
                "id": f"nitrofs:{path}",
                "path": path,
                "file_id": item["file_id"],
                "category": category,
                "asset_kind": asset_kind,
                "source_size": item["source_size"],
                "source_sha256": item["source_sha256"],
                "prior_target_size": item["target_size"],
                "prior_target_sha256": item["target_sha256"],
                "changed_byte_count": item["changed_byte_count"],
                "changed_run_count": item["changed_run_count"],
                "first_changed_offset": item["first_changed_offset"],
                "last_changed_offset": item["last_changed_offset"],
                "extraction_status": "pending",
                "translation_status": "untranslated",
                "replacement_path": None,
                "review_status": "untranslated",
            }
        )
    document = {
        "schema_version": 1,
        "source_profile_id": "ys9j-rev0-e6cafe64",
        "scope_decision": "DEC-PATCHMAP-001",
        "population_state": "prior_patch_changed_visual_and_cutscene_candidates",
        "record_count": len(records),
        "records": sorted(records, key=lambda item: item["path"]),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise FileExistsError("visual scope already exists; refusing to overwrite")
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(args.output.resolve()), "records": len(records)}))


if __name__ == "__main__":
    main()
