#!/usr/bin/env python3
"""Export/import the indexed translation fragments as a reviewable TSV."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


FIELDS = (
    "id",
    "region",
    "source_text",
    "source_hex",
    "korean_text",
    "apply_translation",
    "review_status",
    "note",
)
REVIEW_STATES = {
    "untranslated",
    "in_progress",
    "cross_review_required",
    "human_judgment_required",
    "complete",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    export = subparsers.add_parser("export")
    export.add_argument("--index", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("--index", type=Path, required=True)
    import_parser.add_argument("--input", type=Path, required=True)
    return parser.parse_args()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(document: dict) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def escape_cell(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )


def unescape_cell(value: str) -> str:
    result = []
    index = 0
    escapes = {"r": "\r", "n": "\n", "t": "\t", "\\": "\\"}
    while index < len(value):
        if value[index] == "\\":
            if index + 1 >= len(value) or value[index + 1] not in escapes:
                raise ValueError(f"unsupported TSV escape near character {index}")
            result.append(escapes[value[index + 1]])
            index += 2
        else:
            result.append(value[index])
            index += 1
    return "".join(result)


def load(index_path: Path) -> tuple[dict, list[tuple[Path, dict]]]:
    index = json.loads(index_path.read_text(encoding="utf-8"))
    root = index_path.resolve().parent
    segments = []
    for entry in index["segments"]:
        path = (root / entry["path"]).resolve()
        payload = path.read_bytes()
        if sha256(payload) != entry["sha256"]:
            raise ValueError(f"segment hash mismatch: {entry['path']}")
        segments.append((path, json.loads(payload.decode("utf-8"))))
    return index, segments


def export_tsv(index_path: Path, output: Path) -> None:
    _, segments = load(index_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, delimiter="\t")
        writer.writeheader()
        for _, segment in segments:
            for record in segment["records"]:
                writer.writerow(
                    {
                        "id": record["id"],
                        "region": segment["region"],
                        "source_text": escape_cell(record["source_text"]),
                        "source_hex": record["source_hex"],
                        "korean_text": escape_cell(record["korean_text"] or ""),
                        "apply_translation": str(record["apply_translation"]).lower(),
                        "review_status": record["review_status"],
                        "note": escape_cell(record["note"]),
                    }
                )
    temporary.replace(output)


def import_tsv(index_path: Path, input_path: Path) -> None:
    index, segments = load(index_path)
    with input_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise ValueError("TSV columns do not match the required schema")
        rows = {}
        for row in reader:
            if row["id"] in rows:
                raise ValueError(f"duplicate TSV ID: {row['id']}")
            rows[row["id"]] = row

    known_ids = {record["id"] for _, segment in segments for record in segment["records"]}
    if set(rows) != known_ids:
        missing = sorted(known_ids - set(rows))[:5]
        extra = sorted(set(rows) - known_ids)[:5]
        raise ValueError(f"TSV population mismatch; missing={missing}, extra={extra}")

    changed = 0
    payloads = []
    for path, segment in segments:
        for record in segment["records"]:
            row = rows[record["id"]]
            if row["region"] != segment["region"]:
                raise ValueError(f"protected region changed: {record['id']}")
            if unescape_cell(row["source_text"]) != record["source_text"]:
                raise ValueError(f"protected source text changed: {record['id']}")
            if row["source_hex"].lower() != record["source_hex"]:
                raise ValueError(f"protected source bytes changed: {record['id']}")
            if row["apply_translation"] not in {"true", "false"}:
                raise ValueError(f"invalid apply_translation: {record['id']}")
            if row["review_status"] not in REVIEW_STATES:
                raise ValueError(f"invalid review status: {record['id']}")
            korean_text = unescape_cell(row["korean_text"]) or None
            apply_translation = row["apply_translation"] == "true"
            if apply_translation and not korean_text:
                raise ValueError(f"enabled translation is empty: {record['id']}")
            if row["review_status"] == "complete" and not apply_translation:
                raise ValueError(f"completed translation is disabled: {record['id']}")
            before = (
                record["korean_text"],
                record["apply_translation"],
                record["review_status"],
                record["note"],
            )
            after = (
                korean_text,
                apply_translation,
                row["review_status"],
                unescape_cell(row["note"]),
            )
            changed += before != after
            (
                record["korean_text"],
                record["apply_translation"],
                record["review_status"],
                record["note"],
            ) = after
        payloads.append((path, json_bytes(segment)))

    index_by_path = {entry["path"]: entry for entry in index["segments"]}
    root = index_path.resolve().parent
    for path, payload in payloads:
        relative = path.relative_to(root).as_posix()
        index_by_path[relative]["sha256"] = sha256(payload)
    if changed:
        index["release_approval"] = None

    for path, payload in payloads:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(path)
    index_payload = json_bytes(index)
    temporary_index = index_path.with_suffix(index_path.suffix + ".tmp")
    temporary_index.write_bytes(index_payload)
    temporary_index.replace(index_path)
    print(json.dumps({"valid": True, "changed_records": changed}, ensure_ascii=False))


def main() -> None:
    args = parse_args()
    if args.command == "export":
        export_tsv(args.index, args.output)
        print(str(args.output.resolve()))
    else:
        import_tsv(args.index, args.input)


if __name__ == "__main__":
    main()
