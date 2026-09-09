#!/usr/bin/env python3
"""Merge the first-stage build and relocation reports into the final build report."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--relocation-report", type=Path, required=True)
    parser.add_argument("--relocation-verify-report", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    args = parse_args()
    build = json.loads(args.build_report.read_text(encoding="utf-8"))
    relocation = json.loads(args.relocation_report.read_text(encoding="utf-8"))
    verification = json.loads(args.relocation_verify_report.read_text(encoding="utf-8"))

    if build["output_sha256"] != relocation["input_sha256"]:
        raise ValueError("relocation input does not match first-stage build")
    if relocation["skipped_count"] != 0:
        raise ValueError("relocation left deferred strings unresolved")
    if verification["finding_count"] != 0 or verification["unrelated_records_changed"] != 0:
        raise ValueError("relocation verification has findings")
    if verification["moved"] != relocation["moved_count"]:
        raise ValueError("relocation verification moved count mismatch")
    if build.get("deferred_slot_overflow_count") != relocation["overflow_count"]:
        raise ValueError("deferred overflow population does not match relocation population")

    final_hash = sha256(args.rom)
    if final_hash != relocation["output_sha256"]:
        raise ValueError("final ROM hash does not match relocation output")

    build["status"] = "development_translation_pipeline_with_relocation"
    build["output_sha256"] = final_hash
    build["output_size"] = args.rom.stat().st_size
    build["relocation_moved_count"] = relocation["moved_count"]
    build["relocation_skipped_count"] = relocation["skipped_count"]
    build["relocation_donor_count"] = relocation["donor_count"]
    build["relocation_evicted_count"] = relocation["evicted_count"]
    build["relocation_verified"] = True

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(build, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(
        json.dumps(
            {
                "valid": True,
                "output_sha256": final_hash,
                "deferred_slot_overflow_count": build["deferred_slot_overflow_count"],
                "relocation_moved_count": build["relocation_moved_count"],
                "relocation_skipped_count": build["relocation_skipped_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
