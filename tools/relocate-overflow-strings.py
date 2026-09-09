"""Place translations that outgrew their slot in space other slots gave back.

A fixed slot holds the source string plus the alignment padding behind it, and
57 translations need a few bytes more than that - 56 of them exactly four. Every
one of those strings is reached through a pointer, so it can live anywhere that
stays resident, and the ARM9 static image always does.

The space comes from slots whose Korean is shorter than the Japanese. The build
pads those with spaces up to the original terminator, so their tails are not
free until the donor is terminated where its text ends; zeroing the rest keeps
the original terminator a zero byte, which is what DEC-TEXTSLOT-002 relies on.

Everything here is derived from the declared inputs: which records overflow
comes from the source ROM and the translation index, the pointers come from a
scan of the built ROM's code regions, and the donors come from the built ROM's
own slot tails. Nothing is hand-placed.
"""

from __future__ import annotations

import argparse
import bisect
import collections
import hashlib
import json
import re
import struct
from pathlib import Path

CONTROL_TOKEN = re.compile(r"\{CTRL:01:([0-9A-F]{2})\}")
NUL = bytes(1)
SPACE = 0x20
# The original slots sit on four byte boundaries because the compiler padded
# them, not because anything reads them a word at a time. Two is enough for the
# game's two byte character codes and nearly doubles the space the slot tails
# give back.
ALIGN = 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True, help="unmodified source ROM")
    parser.add_argument("--rom", type=Path, required=True, help="built ROM to relocate within")
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def character_length(character: str) -> int:
    try:
        return len(character.encode("shift_jis"))
    except UnicodeEncodeError:
        return 2


def encoded_length(text: str) -> int:
    total = 0
    position = 0
    for match in CONTROL_TOKEN.finditer(text):
        total += sum(character_length(c) for c in text[position : match.start()]) + 2
        position = match.end()
    return total + sum(character_length(c) for c in text[position:])


def encode(text: str, mapping: dict[str, str]) -> bytes:
    out = bytearray()
    position = 0
    for match in CONTROL_TOKEN.finditer(text):
        out += encode(text[position : match.start()], mapping)
        out += bytes((0x01, int(match.group(1), 16)))
        position = match.end()
    for character in text[position:]:
        if character in mapping:
            out += bytes.fromhex(mapping[character])
            continue
        try:
            out += character.encode("shift_jis")
        except UnicodeEncodeError as error:
            raise ValueError(f"no glyph allocated for {character!r}") from error
    return bytes(out)


def overlay_layout(rom: bytes) -> dict[str, tuple[int, int, int]]:
    """Where each region sits in *this* ROM: file start, file end, RAM address.

    The build lays the overlays out differently from the source, so the offsets
    recorded for the source cannot be used to read or write the built ROM. The
    overlay table and the FAT in the ROM itself are the only reliable source.
    """
    arm9_rom, _entry, arm9_ram, arm9_size = struct.unpack_from("<4I", rom, 0x20)
    layout = {"arm9": (arm9_rom, arm9_rom + arm9_size, arm9_ram)}
    overlay_offset, overlay_size = struct.unpack_from("<II", rom, 0x50)
    fat_offset, _fat_size = struct.unpack_from("<II", rom, 0x48)
    for position in range(overlay_offset, overlay_offset + overlay_size, 32):
        overlay_id, ram_address, _ram_size, _bss, _init, _fini, file_id, _reserved = struct.unpack_from(
            "<8I", rom, position
        )
        start, end = struct.unpack_from("<II", rom, fat_offset + file_id * 8)
        layout[f"arm9-overlay:{overlay_id}"] = (start, end, ram_address)
    return layout


def code_regions(layout: dict[str, tuple[int, int, int]]) -> list[tuple[str, int, int]]:
    return [(name, start, end) for name, (start, end, _ram) in layout.items()]


def word_index(rom: bytes, regions: list[tuple[str, int, int]]) -> dict[int, list[tuple[int, str]]]:
    """Every 4-aligned word in code, tagged with the region that holds it.

    Overlays 1 to 34 all load at the same address, so a word that looks like a
    pointer into one of them could belong to any of the others. The region a
    word sits in is what tells those apart.
    """
    index: dict[int, list[tuple[int, str]]] = collections.defaultdict(list)
    for name, start, end in regions:
        position = start + (-start % 4)
        for offset in range(position, end - 3, 4):
            index[int.from_bytes(rom[offset : offset + 4], "little")].append((offset, name))
    return index


def main() -> None:
    args = parse_args()
    source = args.source.read_bytes()
    rom = bytearray(args.rom.read_bytes())
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    mapping = json.loads(args.build_report.read_text(encoding="utf-8"))["custom_mapping"]
    index = json.loads(args.index.read_text(encoding="utf-8"))

    arm9_rom, _entry, arm9_ram, _arm9_size = struct.unpack_from("<4I", rom, 0x20)
    # Two layouts: the source is where the slot and its padding are measured,
    # the built ROM is where everything is read and written.
    source_layout = overlay_layout(source)
    output_layout = overlay_layout(bytes(rom))
    if set(source_layout) != set(output_layout):
        raise ValueError("source and built ROM disagree about which overlays exist")
    for name, (_start, _end, ram_address) in source_layout.items():
        if output_layout[name][2] != ram_address:
            raise ValueError(f"overlay moved in RAM between source and build: {name}")
    file_base = {name: start for name, (start, _end, _ram) in output_layout.items()}
    ram_base = {name: ram for name, (_start, _end, ram) in output_layout.items()}
    source_base = {name: start for name, (start, _end, _ram) in source_layout.items()}

    records = []
    for entry in index["segments"]:
        segment = json.loads((args.index.parent / entry["path"]).read_text(encoding="utf-8"))
        region = segment["region"]
        if region.startswith("text/"):
            continue
        for record in segment["records"]:
            if not record.get("korean_text"):
                continue
            offset = int(record["locator"]["offset"])
            start = source_base[region] + offset
            end = start + len(bytes.fromhex(record["source_hex"]))
            padding_end = end
            while source[padding_end] == 0:
                padding_end += 1
            records.append(
                {
                    "id": record["id"],
                    "region": region,
                    "file_offset": file_base[region] + offset,
                    "ram_address": ram_base[region] + offset,
                    "capacity": padding_end - start - 1,
                    "text": record["korean_text"],
                    "used": encoded_length(record["korean_text"]),
                }
            )

    overflow = [r for r in records if r["used"] > r["capacity"]]
    pointers = word_index(bytes(rom), code_regions(output_layout))

    ram_span = {
        name: (ram, ram + (end - start)) for name, (start, end, ram) in output_layout.items()
    }

    def co_resident(one: str, other: str) -> bool:
        """Can both be in memory at once?

        Overlays that share a load address replace each other, so neither can
        hold a pointer into the other. Anything whose address range is disjoint
        can be resident together, which is how overlay 37 ends up holding the
        pointer table for overlay 34.
        """
        if one == other or "arm9" in (one, other):
            return True
        left, right = ram_span[one], ram_span[other]
        return left[1] <= right[0] or right[1] <= left[0]

    def pointers_to(record: dict) -> list[int]:
        """Pointer words that can really belong to this record's string."""
        return [
            offset
            for offset, holder in pointers.get(record["ram_address"], [])
            if co_resident(holder, record["region"])
        ]

    for record in records:
        record["pointers"] = pointers_to(record)
        record["encoded"] = encode(record["text"], mapping) + NUL

    def address_of(region: str, offset: int) -> int:
        return ram_base[region] + (offset - file_base[region])

    def reachable(target_region: str, hole_region: str) -> bool:
        """ARM9 is always in memory; an overlay only sees itself and ARM9."""
        return hole_region in ("arm9", target_region)

    # Donors: slots the build padded with spaces. ARM9 donors work for every
    # target, and an overlay's own slots work for that overlay - it is resident
    # whenever its strings are on screen. Only as many donors are opened as the
    # relocations need: every donor loses its trailing spaces, and there is no
    # reason to touch slots whose space nobody uses.
    targets = {r["id"] for r in overflow}
    candidates = []
    for record in records:
        if record["id"] in targets:
            continue
        start = record["file_offset"]
        terminator = rom.index(NUL, start)
        text_end = terminator
        while rom[text_end - 1] == SPACE:
            text_end -= 1
        limit = terminator
        while rom[limit] == 0:
            limit += 1
        hole_start = text_end + 1
        hole_start += -hole_start % ALIGN
        if limit - 1 - hole_start < 4:
            continue
        candidates.append(
            (limit - 1 - hole_start, record["id"], text_end, terminator, hole_start, limit - 1, record["region"])
        )
    # Smallest usable donor first, so a four byte relocation does not open a
    # slot that a longer one would have needed.
    candidates.sort()

    candidate_sizes = [candidate[0] for candidate in candidates]
    spent: set[str] = set()
    holes: list[list[int]] = []
    donors = []

    def add_hole(start: int, end: int, region: str) -> list[int]:
        """Add free space, joined to whatever it touches.

        Relocated records often sit next to each other, so the slots they leave
        behind are adjacent in the file. Kept apart they are three holes too
        small to use; joined they are one that fits.
        """
        hole = [start, end, region]
        merged = True
        while merged:
            merged = False
            for other in holes:
                if other[2] != region:
                    continue
                if other[1] == hole[0] or other[0] == hole[1] or (
                    other[0] <= hole[0] <= other[1] or hole[0] <= other[0] <= hole[1]
                ):
                    hole = [min(hole[0], other[0]), max(hole[1], other[1]), region]
                    holes.remove(other)
                    merged = True
                    break
        holes.append(hole)
        return hole

    def open_donor(need: int, region: str) -> list[int] | None:
        # candidates is sorted by hole size, so the first one big enough is the
        # smallest that fits; anything before it can be skipped outright.
        for index in range(bisect.bisect_left(candidate_sizes, need), len(candidates)):
            candidate = candidates[index]
            size, record_id, text_end, terminator, hole_start, hole_end, hole_region = candidate
            if record_id in spent or not reachable(region, hole_region):
                continue
            spent.add(record_id)
            rom[text_end:terminator] = bytes(terminator - text_end)
            hole = add_hole(hole_start, hole_end, hole_region)
            opened.add(record_id)
            # Its tail is in use now, so the slot is no longer free to hand over.
            retired.add(record_id)
            donors.append(
                {
                    "id": record_id,
                    "space_padding_reclaimed": terminator - text_end,
                    "hole_bytes": size,
                }
            )
            return hole
        return None

    # Records that could step aside to make room: an ARM9 string that fits its
    # slot today, but whose slot is larger than its Korean needs.
    # A string with no pointer cannot be moved, so it is not a candidate at all
    # and there is no reason to keep looking at it.
    evictable = sorted(
        (r for r in records if r["id"] not in targets and r["pointers"]),
        key=lambda r: r["capacity"],
    )
    evictable_capacities = [record["capacity"] for record in evictable]
    opened: set[str] = set()
    in_flight: set[str] = set()
    retired: set[str] = set()
    evicted = []

    # The eviction search recurses inside its own scan, so an unlucky target can
    # explore a combinatorial number of chains. These bound it: a frame only
    # descends for its first few candidates, and the whole search shares one
    # budget. Running out means a relocation is reported as skipped, never that
    # the tool spins.
    RECURSION_FANOUT = 16
    SEARCH_BUDGET = 20000000
    budget = [SEARCH_BUDGET]

    def evict(need: int, region: str, depth: int = 4) -> list[int] | None:
        """Move a resident string out of a slot big enough for `need`.

        The evicted string needs somewhere to go as well, and for the longest
        relocations the only slots that fit are held by strings that are just as
        awkward to place, so this recurses a bounded number of times. Each step
        consolidates: a long slot is freed by moving its text into holes too
        small for the string that needed the slot.
        """
        if depth <= 0 or budget[0] <= 0:
            return None
        descents = 0
        for index in range(bisect.bisect_left(evictable_capacities, need - 1), len(evictable)):
            budget[0] -= 1
            if budget[0] <= 0:
                return None
            record = evictable[index]
            if record["id"] in retired or record["id"] in opened:
                continue
            if record["id"] in in_flight or not reachable(region, record["region"]):
                continue
            found = record["pointers"]
            encoded = record["encoded"]
            small = len(encoded) + -len(encoded) % ALIGN
            usable = [h for h in holes if h[1] - h[0] >= small and reachable(record["region"], h[2])]
            # Reserve this record while looking for somewhere to put it: the
            # recursive call below walks the same list and would otherwise pick
            # the record this frame is already moving.
            in_flight.add(record["id"])
            try:
                if usable:
                    destination = min(usable, key=lambda h: h[1] - h[0])
                else:
                    destination = open_donor(small, record["region"])
                    if destination is None and descents < RECURSION_FANOUT:
                        descents += 1
                        destination = evict(small, record["region"], depth - 1)
            finally:
                in_flight.discard(record["id"])
            if destination is None:
                continue
            placed = destination[0]
            destination[0] += small
            rom[placed : placed + len(encoded)] = encoded
            for pointer in found:
                struct.pack_into("<I", rom, pointer, address_of(destination[2], placed))
            start = record["file_offset"]
            end = start + record["capacity"] + 1
            rom[start:end] = bytes(end - start)
            # Retire by id: the lists stay put so the recursive calls above
            # keep their positions, and a spent entry is simply skipped.
            retired.add(record["id"])
            # Its slot is a hole now; reopening it as a donor would write over
            # whatever was just placed there.
            spent.add(record["id"])
            hole = add_hole(start, end, record["region"])
            evicted.append(
                {
                    "id": record["id"],
                    "freed_bytes": end - start,
                    "new_address": address_of(destination[2], placed),
                }
            )
            return hole
        return None

    moved = []
    skipped = []
    # Shortest first: a relocated string leaves its whole slot behind, and those
    # slots are the only holes big enough for the long ones.
    for record in sorted(overflow, key=lambda r: r["used"]):
        found = record["pointers"]
        if not found:
            skipped.append({**{k: record[k] for k in ("id", "capacity", "used", "text")}, "reason": "no pointer found"})
            continue
        encoded = record["encoded"]
        need = len(encoded) + -len(encoded) % ALIGN
        # Best fit, so a short string does not eat a hole a long one will need.
        usable = [h for h in holes if h[1] - h[0] >= need and reachable(record["region"], h[2])]
        chosen = (
            min(usable, key=lambda h: h[1] - h[0])
            if usable
            else open_donor(need, record["region"]) or evict(need, record["region"])
        )
        if chosen is None:
            skipped.append({**{k: record[k] for k in ("id", "capacity", "used", "text")}, "reason": "no hole large enough"})
            continue
        placed = chosen[0]
        chosen[0] += need
        new_address = address_of(chosen[2], placed)
        rom[placed : placed + len(encoded)] = encoded
        for pointer in found:
            struct.pack_into("<I", rom, pointer, new_address)
        slot_start = record["file_offset"]
        slot_end = slot_start + record["capacity"] + 1
        if slot_end - 1 - slot_start >= 4:
            # Nothing points at the old slot any more, so all of it is free.
            rom[slot_start : slot_end - 1] = bytes(slot_end - 1 - slot_start)
            add_hole(slot_start, slot_end - 1, record["region"])
        moved.append(
            {
                "id": record["id"],
                "text": record["text"],
                "capacity": record["capacity"],
                "encoded_length": len(encoded),
                "new_address": new_address,
                "region": record["region"],
                "pointer_count": len(found),
                "pointer_rom_offsets": found,
            }
        )

    args.output.write_bytes(bytes(rom))
    document = {
        "schema_version": 1,
        "input_sha256": hashlib.sha256(args.rom.read_bytes()).hexdigest(),
        "output_sha256": hashlib.sha256(bytes(rom)).hexdigest(),
        "overflow_count": len(overflow),
        "moved_count": len(moved),
        "skipped_count": len(skipped),
        "donor_count": len(donors),
        "evicted_count": len(evicted),
        "evicted": evicted,
        "donor_bytes": sum(d["hole_bytes"] for d in donors),
        "bytes_written": sum(m["encoded_length"] for m in moved),
        "skipped": skipped,
        "moved": moved,
    }
    args.report.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in document.items() if k != "moved"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
