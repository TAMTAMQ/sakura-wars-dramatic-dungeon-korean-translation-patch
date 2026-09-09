#!/usr/bin/env python3
"""Extract and validate indexed image/cutscene translation assets."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("extract", "validate"):
        command = subparsers.add_parser(name)
        command.add_argument("--source", type=Path, required=True)
        command.add_argument("--inventory", type=Path, required=True)
        command.add_argument("--index", type=Path, required=True)
        if name == "extract":
            command.add_argument("--output-root", type=Path, required=True)
            command.add_argument("--variant", choices=("source", "prior-patch"), default="source")
    return parser.parse_args()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load(args: argparse.Namespace) -> tuple[bytes, dict, dict, dict[str, dict]]:
    source = args.source.read_bytes()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    index = json.loads(args.index.read_text(encoding="utf-8"))
    files = {item["path"]: item for item in inventory["files"]}
    if len(index["records"]) != int(index["record_count"]):
        raise ValueError("visual index population mismatch")
    return source, inventory, index, files


def source_asset(source: bytes, files: dict[str, dict], record: dict, variant: str = "source") -> bytes:
    item = files.get(record["path"])
    if item is None or int(item["file_id"]) != int(record["file_id"]):
        raise ValueError(f"visual inventory lookup mismatch: {record['id']}")
    payload = source[int(item["start"]) : int(item["end"])]
    size_key = "source_size" if variant == "source" else "prior_target_size"
    hash_key = "source_sha256" if variant == "source" else "prior_target_sha256"
    if len(payload) != int(record[size_key]):
        raise ValueError(f"visual source size mismatch: {record['id']}")
    if sha256(payload) != record[hash_key]:
        raise ValueError(f"visual source hash mismatch: {record['id']}")
    return payload


def validate(args: argparse.Namespace) -> dict:
    source, _, index, files = load(args)
    root = args.index.resolve().parent
    ready = []
    for record in index["records"]:
        source_asset(source, files, record)
        fields = (
            record["translation_status"] == "translated",
            record["review_status"] == "complete",
            record["replacement_path"] is not None,
        )
        if any(fields) and not all(fields):
            raise ValueError(f"incomplete visual approval tuple: {record['id']}")
        if not all(fields):
            continue
        replacement = (root / record["replacement_path"]).resolve()
        if not replacement.is_relative_to(root):
            raise ValueError(f"visual replacement escapes asset root: {record['id']}")
        payload = replacement.read_bytes()
        if len(payload) != int(record["source_size"]):
            raise ValueError(f"visual replacement size mismatch: {record['id']}")
        if record.get("replacement_sha256") and sha256(payload) != record["replacement_sha256"]:
            raise ValueError(f"visual replacement hash mismatch: {record['id']}")
        ready.append(record["id"])
    return {
        "valid": True,
        "record_count": int(index["record_count"]),
        "image_candidate_count": sum(1 for r in index["records"] if r["category"] != "cpk"),
        "cpk_container_count": sum(1 for r in index["records"] if r["category"] == "cpk"),
        "ready_replacement_count": len(ready),
        "ready_replacement_ids": ready,
    }


def extract(args: argparse.Namespace) -> dict:
    source, _, index, files = load(args)
    output_root = args.output_root.resolve()
    extracted = []
    dependencies = []
    for record in index["records"]:
        payload = source_asset(source, files, record, args.variant)
        output = (output_root / record["path"]).resolve()
        if not output.is_relative_to(output_root):
            raise ValueError(f"visual export path escapes output root: {record['id']}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(payload)
        extracted.append({"id": record["id"], "path": record["path"], "sha256": sha256(payload)})
        if record["asset_kind"] == "nintendo_ds_ncgr_image":
            stem = str(PurePosixPath(record["path"]).with_suffix(""))
            for suffix in (".NCLR", ".NCER", ".NANR"):
                dependency_path = stem + suffix
                item = files.get(dependency_path)
                if item is None:
                    continue
                dependency = source[int(item["start"]) : int(item["end"])]
                dependency_output = (output_root / dependency_path).resolve()
                dependency_output.parent.mkdir(parents=True, exist_ok=True)
                dependency_output.write_bytes(dependency)
                dependencies.append({"path": dependency_path, "sha256": sha256(dependency)})
    manifest = {
        "schema_version": 1,
        "variant": args.variant,
        "source_sha256": sha256(source),
        "record_count": len(extracted),
        "records": extracted,
        "dependency_count": len(dependencies),
        "dependencies": dependencies,
        "note": "CPK is exported as a container until its internal TOC codec is implemented.",
    }
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"valid": True, "output_root": str(output_root), **manifest}


def main() -> None:
    args = parse_args()
    report = extract(args) if args.command == "extract" else validate(args)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
