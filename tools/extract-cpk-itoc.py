#!/usr/bin/env python3
"""Extract and compare the ID-only CRI CPK ITOC used by faCpkData.cpk."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from cri_utf import parse_utf


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpk", type=Path, required=True)
    parser.add_argument("--compare-rom", type=Path)
    parser.add_argument("--compare-inventory", type=Path)
    parser.add_argument("--nitrofs-path", default="cpk/faCpkData.cpk")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    return parser.parse_args()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def align(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def packet(data: bytes, offset: int, magic: bytes) -> bytes:
    if data[offset : offset + 4] != magic:
        raise ValueError(f"missing {magic!r} packet at {offset:#x}")
    size = struct.unpack_from("<Q", data, offset + 8)[0]
    payload = data[offset + 16 : offset + 16 + size]
    if len(payload) != size:
        raise ValueError(f"truncated {magic!r} packet")
    return payload


def parse_cpk(data: bytes) -> tuple[dict, list[dict]]:
    header = parse_utf(packet(data, 0, b"CPK "))["rows"][0]
    itoc_offset = int(header["ItocOffset"])
    content_offset = int(header["ContentOffset"])
    alignment = int(header["Align"])
    outer = parse_utf(packet(data, itoc_offset, b"ITOC"))["rows"][0]
    size_rows = []
    for name in ("DataL", "DataH"):
        nested = outer.get(name)
        if nested:
            size_rows.extend(parse_utf(nested)["rows"])
    entries = []
    current = content_offset
    for row in sorted(size_rows, key=lambda item: int(item["ID"])):
        file_size = int(row["FileSize"])
        extract_size = int(row.get("ExtractSize") or file_size)
        entries.append({
            "id": int(row["ID"]),
            "offset": current,
            "file_size": file_size,
            "extract_size": extract_size,
            "compressed": extract_size != file_size,
        })
        current = align(current + file_size, alignment)
    if len(entries) != int(header["Files"]):
        raise ValueError("CPK Files count does not match ITOC population")
    for entry in entries:
        if entry["offset"] + entry["file_size"] > itoc_offset:
            raise ValueError(f"CPK entry overlaps ITOC: {entry['id']}")
    return header, entries


def classify(payload: bytes, compressed: bool) -> str:
    signatures = (
        (b"CRILAYLA", "crilayla"),
        (b"HCA\x00", "hca"),
        (b"\xC8\xC3\xC1\x00", "encrypted_hca"),
        (b"CRID", "usm"),
        (b"RGCN", "ncgr"),
        (b"RLCN", "nclr"),
        (b"RECN", "ncer"),
        (b"RNAN", "nanr"),
        (b"CLUT", "clut_cmap_char"),
    )
    for signature, kind in signatures:
        if payload.startswith(signature):
            return kind
    if len(payload) >= 4 and payload[0] == 0x80 and b"(c)CRI" in payload[:256]:
        return "adx"
    return "compressed_unknown" if compressed else "unknown"


def cpk_from_rom(rom_path: Path, inventory_path: Path, nitrofs_path: str) -> bytes:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    item = next(entry for entry in inventory["files"] if entry["path"] == nitrofs_path)
    with rom_path.open("rb") as stream:
        stream.seek(int(item["start"]))
        return stream.read(int(item["size"]))


def main() -> None:
    args = parse_args()
    original = args.cpk.read_bytes()
    header, entries = parse_cpk(original)
    comparison = None
    comparison_entries = None
    if args.compare_rom or args.compare_inventory:
        if not args.compare_rom or not args.compare_inventory:
            raise ValueError("--compare-rom and --compare-inventory must be provided together")
        comparison = cpk_from_rom(args.compare_rom, args.compare_inventory, args.nitrofs_path)
        comparison_header, comparison_entries = parse_cpk(comparison)
        if [(e["id"], e["file_size"], e["offset"]) for e in entries] != [
            (e["id"], e["file_size"], e["offset"]) for e in comparison_entries
        ]:
            raise ValueError("comparison CPK ITOC layout differs")
        if int(header["ContentOffset"]) != int(comparison_header["ContentOffset"]):
            raise ValueError("comparison CPK content offset differs")
    output_root = args.output_root.resolve()
    records = []
    changed = []
    type_counts: dict[str, int] = {}
    for entry in entries:
        start = entry["offset"]
        end = start + entry["file_size"]
        payload = original[start:end]
        kind = classify(payload, entry["compressed"])
        extension = kind if kind not in {"unknown", "compressed_unknown", "clut_cmap_char"} else "bin"
        relative = Path(f"{entry['id']:04d}.{extension}")
        output = output_root / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(payload)
        record = {**entry, "path": relative.as_posix(), "kind": kind, "sha256": sha256(payload)}
        if comparison is not None:
            comparison_payload = comparison[start:end]
            record["prior_sha256"] = sha256(comparison_payload)
            record["changed_by_prior_patch"] = payload != comparison_payload
            if record["changed_by_prior_patch"]:
                prior_output = output_root / "prior-patch" / relative
                prior_output.parent.mkdir(parents=True, exist_ok=True)
                prior_output.write_bytes(comparison_payload)
                record["prior_path"] = prior_output.relative_to(output_root).as_posix()
                changed.append(entry["id"])
        records.append(record)
        type_counts[kind] = type_counts.get(kind, 0) + 1
    manifest = {
        "schema_version": 1,
        "cpk_sha256": sha256(original),
        "content_offset": int(header["ContentOffset"]),
        "itoc_offset": int(header["ItocOffset"]),
        "alignment": int(header["Align"]),
        "record_count": len(records),
        "type_counts": type_counts,
        "changed_by_prior_patch_count": len(changed),
        "changed_by_prior_patch_ids": changed,
        "records": records,
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in manifest.items() if key != "records"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
