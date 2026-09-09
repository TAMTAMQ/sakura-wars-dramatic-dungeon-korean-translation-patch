"""Round-trip every compressed CPK entry through the CRILAYLA codec.

Each entry is decompressed, compressed again, and decompressed once more. The
rebuilt payload has to match byte for byte and has to fit the slot the original
entry occupies, because CPK entries sit at fixed offsets.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


def load_module(name: str):
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(name[:-3].replace("-", "_"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DECOMPRESSOR = load_module("decompress-cpk-entries.py")
COMPRESSOR = load_module("compress-cpk-entries.py")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpk-manifest", type=Path, required=True)
    parser.add_argument("--extracted-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0, help="check only the first N entries")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = json.loads(args.cpk_manifest.read_text(encoding="utf-8"))
    entries = [record for record in manifest["records"] if record.get("compressed")]
    if args.limit:
        entries = entries[: args.limit]

    checked = 0
    mismatches = []
    oversized = []
    total_original = 0
    total_repacked = 0
    for record in entries:
        packed = (args.extracted_root / record["path"]).read_bytes()
        plain = DECOMPRESSOR.decompress(packed)
        repacked = COMPRESSOR.compress(plain)
        if DECOMPRESSOR.decompress(repacked) != plain:
            mismatches.append(record["id"])
        if len(repacked) > len(packed):
            oversized.append(
                {"id": record["id"], "original": len(packed), "repacked": len(repacked)}
            )
        total_original += len(packed)
        total_repacked += len(repacked)
        checked += 1

    document = {
        "schema_version": 1,
        "checked_count": checked,
        "roundtrip_mismatch_count": len(mismatches),
        "roundtrip_mismatches": mismatches,
        "oversized_count": len(oversized),
        "oversized": oversized,
        "original_total_bytes": total_original,
        "repacked_total_bytes": total_repacked,
        "size_ratio": round(total_repacked / total_original, 5) if total_original else None,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in document.items() if k not in ("roundtrip_mismatches", "oversized")}, ensure_ascii=False, indent=2))
    if mismatches or oversized:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
