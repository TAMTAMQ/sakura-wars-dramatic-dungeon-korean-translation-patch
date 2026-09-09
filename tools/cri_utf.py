#!/usr/bin/env python3
"""Small read-only parser for CRI @UTF tables used by this project."""

from __future__ import annotations

import struct
from dataclasses import dataclass


TYPE_FORMATS = {
    0: ">B",
    1: ">b",
    2: ">H",
    3: ">h",
    4: ">I",
    5: ">i",
    6: ">Q",
    7: ">q",
    8: ">f",
    9: ">d",
}


@dataclass(frozen=True)
class Column:
    name: str
    storage: int
    value_type: int
    constant: object | None


def _cstring(data: bytes, offset: int) -> str:
    end = data.find(b"\0", offset)
    if end < 0:
        raise ValueError("unterminated @UTF string")
    raw = data[offset:end]
    for encoding in ("utf-8", "shift_jis"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _scalar(data: bytes, position: int, value_type: int) -> tuple[object, int]:
    if value_type in TYPE_FORMATS:
        fmt = TYPE_FORMATS[value_type]
        return struct.unpack_from(fmt, data, position)[0], position + struct.calcsize(fmt)
    if value_type == 10:
        return struct.unpack_from(">I", data, position)[0], position + 4
    if value_type == 11:
        return struct.unpack_from(">II", data, position), position + 8
    raise ValueError(f"unsupported @UTF value type: {value_type}")


def parse_utf(data: bytes) -> dict:
    if data[:4] != b"@UTF":
        raise ValueError("unencrypted @UTF table required")
    (
        table_size,
        rows_offset,
        strings_offset,
        data_offset,
        table_name_offset,
        column_count,
        row_width,
        row_count,
    ) = struct.unpack_from(">IIIIIHHI", data, 4)
    table_end = 8 + table_size
    if table_end > len(data):
        raise ValueError("@UTF table exceeds input")
    rows_start = 8 + rows_offset
    strings_start = 8 + strings_offset
    data_start = 8 + data_offset
    position = 32
    columns = []
    for _ in range(column_count):
        flags = data[position]
        name_offset = struct.unpack_from(">I", data, position + 1)[0]
        position += 5
        storage = flags & 0xF0
        value_type = flags & 0x0F
        constant = None
        if storage == 0x30:
            constant, position = _scalar(data, position, value_type)
        elif storage not in (0x10, 0x50):
            raise ValueError(f"unsupported @UTF column storage: {storage:#x}")
        columns.append(Column(_cstring(data, strings_start + name_offset), storage, value_type, constant))
    rows = []
    for row_index in range(row_count):
        row_position = rows_start + row_index * row_width
        cursor = row_position
        row = {}
        for column in columns:
            if column.storage == 0x10:
                value = None
            elif column.storage == 0x30:
                value = column.constant
            else:
                value, cursor = _scalar(data, cursor, column.value_type)
            if value is not None and column.value_type == 10:
                value = _cstring(data, strings_start + int(value))
            elif value is not None and column.value_type == 11:
                offset, size = value
                value = data[data_start + offset : data_start + offset + size]
            row[column.name] = value
        if cursor - row_position != row_width:
            raise ValueError("@UTF row width mismatch")
        rows.append(row)
    return {
        "table_name": _cstring(data, strings_start + table_name_offset),
        "columns": columns,
        "rows": rows,
        "table_size": table_size,
    }
