"""Decompress the CPK entries the game stores with CRILAYLA.

The container keeps CRI's layout - a 16-byte header, the backwards bit stream,
then the first 0x100 bytes of the original stored raw - but zeroes the
"CRILAYLA" magic, so a stock CPK tool walks past these entries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

RAW_HEADER_SIZE = 0x100
LENGTH_LEVELS = (2, 3, 5, 8)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpk-manifest", type=Path, required=True)
    parser.add_argument("--extracted-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


class BackwardBitReader:
    """CRILAYLA reads its bit stream from the end of the block towards the start."""

    def __init__(self, data: bytes, start: int) -> None:
        self._data = data
        self._position = start
        self._pool = 0
        self._available = 0

    def read(self, count: int) -> int:
        value = 0
        while count > 0:
            if self._available == 0:
                if self._position < 0:
                    raise ValueError("CRILAYLA bit stream ran out of input")
                self._pool = self._data[self._position]
                self._position -= 1
                self._available = 8
            take = min(count, self._available)
            value = (value << take) | ((self._pool >> (self._available - take)) & ((1 << take) - 1))
            self._available -= take
            count -= take
        return value


def decompress(data: bytes) -> bytes:
    if len(data) < 16:
        raise ValueError("entry is too small to carry a CRILAYLA header")
    uncompressed_size, raw_header_offset = struct.unpack_from("<II", data, 8)
    raw_start = 16 + raw_header_offset
    if raw_start + RAW_HEADER_SIZE > len(data):
        raise ValueError("CRILAYLA raw header lies outside the entry")
    output = bytearray(uncompressed_size + RAW_HEADER_SIZE)
    output[:RAW_HEADER_SIZE] = data[raw_start : raw_start + RAW_HEADER_SIZE]
    reader = BackwardBitReader(data, raw_start - 1)
    position = len(output) - 1
    while position >= RAW_HEADER_SIZE:
        if reader.read(1):
            reference = position + reader.read(13) + 3
            length = 3
            for level in LENGTH_LEVELS:
                bits = reader.read(level)
                length += bits
                if bits != (1 << level) - 1:
                    break
            else:
                while True:
                    bits = reader.read(8)
                    length += bits
                    if bits != 0xFF:
                        break
            for _ in range(length):
                if position < RAW_HEADER_SIZE:
                    break
                if reference >= len(output):
                    raise ValueError("CRILAYLA back reference points past the output")
                output[position] = output[reference]
                reference -= 1
                position -= 1
        else:
            output[position] = reader.read(8)
            position -= 1
    return bytes(output)


def main() -> None:
    args = parse_args()
    manifest = json.loads(args.cpk_manifest.read_text(encoding="utf-8"))
    extracted_root = args.extracted_root.resolve()
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    results = []
    failures = []
    for record in manifest["records"]:
        if not record.get("compressed"):
            continue
        payload = (extracted_root / record["path"]).read_bytes()
        try:
            plain = decompress(payload)
        except ValueError as error:
            failures.append({"id": record["id"], "reason": str(error)})
            continue
        output = output_root / f"{int(record['id']):04d}.bin"
        output.write_bytes(plain)
        results.append(
            {
                "id": record["id"],
                "path": output.name,
                "packed_size": len(payload),
                "size": len(plain),
                "declared_extract_size": record["extract_size"],
                "matches_declared_size": len(plain) == int(record["extract_size"]),
                "magic": plain[:4].decode("latin1"),
                "sha256": hashlib.sha256(plain).hexdigest(),
            }
        )

    document = {
        "schema_version": 1,
        "codec": "crilayla-without-magic",
        "decompressed_count": len(results),
        "failure_count": len(failures),
        "size_mismatch_count": sum(1 for item in results if not item["matches_declared_size"]),
        "failures": failures,
        "records": results,
    }
    (output_root / "manifest.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in document.items() if k != "records"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
