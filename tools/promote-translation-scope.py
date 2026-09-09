#!/usr/bin/env python3
"""Promote the verified prior-patch candidate map into versioned product inputs.

This is a one-time survey-to-spec promotion tool. It deliberately omits every byte
from the prior localized target and refuses to overwrite existing translation files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path


SOURCE_PROFILE_ID = "ys9j-rev0-e6cafe64"
SOURCE_SHA256 = "e6cafe64437080bf5474fa7f9cfec6568b2af7626b65c3ab0d27d30308cec7c4"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--roundtrip-report", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


def encoded_json(document: dict) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def segment_name(region: str) -> str:
    if region == "text/CAMPDATA.DAT":
        return "campdata.json"
    if region == "text/CITR.DAT":
        return "citr.json"
    if region == "arm9":
        return "arm9.json"
    match = re.fullmatch(r"arm9-overlay:(\d+)", region)
    if match:
        return f"overlay-{int(match.group(1)):02d}.json"
    raise ValueError(f"unsupported translation region: {region}")


def main() -> None:
    args = parse_args()
    manifest = json.loads(args.candidates.read_text(encoding="utf-8"))
    roundtrip = json.loads(args.roundtrip_report.read_text(encoding="utf-8"))

    if manifest["authority"]["source_sha256"] != SOURCE_SHA256:
        raise ValueError("candidate manifest has the wrong source authority")
    if roundtrip["source_sha256"] != SOURCE_SHA256:
        raise ValueError("round-trip report has the wrong source authority")
    if not roundtrip["all_boundaries_identical"]:
        raise ValueError("round-trip report did not prove identical boundaries")
    if roundtrip["candidate_count"] != len(manifest["candidates"]):
        raise ValueError("round-trip population does not match candidate records")

    output_root = args.output_root.resolve()
    segments_root = output_root / "segments"
    index_path = output_root / "index.json"
    existing_segments = list(segments_root.glob("*.json")) if segments_root.exists() else []
    if index_path.exists() or existing_segments:
        raise FileExistsError(
            "translation product inputs already exist; refusing to overwrite them"
        )

    grouped: dict[str, list[dict]] = defaultdict(list)
    seen_ids: set[str] = set()
    for candidate in manifest["candidates"]:
        candidate_id = candidate["id"]
        if candidate_id in seen_ids:
            raise ValueError(f"duplicate candidate ID: {candidate_id}")
        seen_ids.add(candidate_id)
        locator = (
            {"entry_id": int(candidate["entry_id"])}
            if "entry_id" in candidate
            else {"offset": int(candidate["offset"])}
        )
        grouped[candidate["region"]].append(
            {
                "id": candidate_id,
                "locator": locator,
                "source_text": candidate["source_text"],
                "source_hex": candidate["source_hex"],
                "korean_text": None,
                "apply_translation": False,
                "review_status": "untranslated",
                "note": "",
            }
        )

    segments_root.mkdir(parents=True, exist_ok=True)
    index_segments = []
    for region in sorted(grouped, key=lambda item: (segment_name(item))):
        relative_path = Path("segments") / segment_name(region)
        segment_document = {
            "schema_version": 1,
            "source_profile_id": SOURCE_PROFILE_ID,
            "source_sha256": SOURCE_SHA256,
            "region": region,
            "scope": "prior_patch_changed_nul_shift_jis_candidates",
            "records": grouped[region],
        }
        payload = encoded_json(segment_document)
        (output_root / relative_path).write_bytes(payload)
        index_segments.append(
            {
                "path": relative_path.as_posix(),
                "region": region,
                "record_count": len(grouped[region]),
                "sha256": sha256(payload),
            }
        )

    index_document = {
        "schema_version": 1,
        "source_profile_id": SOURCE_PROFILE_ID,
        "source_sha256": SOURCE_SHA256,
        "scope_decision": "DEC-PATCHMAP-001",
        "scope_population_state": "patch_selected_lower_bound",
        "scope_record_count": len(seen_ids),
        "translation_policy": {
            "untranslated_records_use_source": True,
            "korean_text_requires_apply_translation": True,
            "release_requires_human_review": True,
        },
        "segments": index_segments,
        "release_approval": None,
    }
    index_path.write_bytes(encoded_json(index_document))
    print(
        json.dumps(
            {
                "output": str(index_path),
                "segment_count": len(index_segments),
                "record_count": len(seen_ids),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
