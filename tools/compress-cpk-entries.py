"""Compress data back into the CPK's CRILAYLA layout.

The decoder in tools/decompress-cpk-entries.py walks the output backwards, so
this encoder reverses the payload and runs a plain LZ77 over it. Matches must
start at least three bytes back and reach at most 8194, which is what the
13-bit offset field encodes.
"""

from __future__ import annotations

import struct

RAW_HEADER_SIZE = 0x100
MIN_MATCH = 3
MIN_DISTANCE = 3
MAX_DISTANCE = 8194
MAX_MATCH = 512
WINDOW_HASH_BITS = 16


class BitWriter:
    """Collect bits in decoder order; pack() lays them out back to front."""

    def __init__(self) -> None:
        self._bits: list[int] = []

    def write(self, value: int, count: int) -> None:
        for shift in range(count - 1, -1, -1):
            self._bits.append((value >> shift) & 1)

    def pack(self) -> bytes:
        size = (len(self._bits) + 7) // 8
        buffer = bytearray(size)
        position = size - 1
        available = 8
        for bit in self._bits:
            available -= 1
            buffer[position] |= bit << available
            if available == 0:
                position -= 1
                available = 8
        return bytes(buffer)


def write_length(writer: BitWriter, length: int) -> None:
    value = length - MIN_MATCH
    if value < 3:
        writer.write(value, 2)
        return
    writer.write(3, 2)
    value -= 3
    if value < 7:
        writer.write(value, 3)
        return
    writer.write(7, 3)
    value -= 7
    if value < 31:
        writer.write(value, 5)
        return
    writer.write(31, 5)
    value -= 31
    while value >= 255:
        writer.write(255, 8)
        value -= 255
    writer.write(value, 8)


def length_bits(length: int) -> int:
    """Bit cost of the variable-length match length field."""
    value = length - MIN_MATCH
    if value < 3:
        return 2
    value -= 3
    if value < 7:
        return 5
    value -= 7
    if value < 31:
        return 10
    value -= 31
    # 2 + 3 + 5 bits of level markers, then the 8-bit tail written below.
    total = 10
    while value >= 255:
        total += 8
        value -= 255
    return total + 8


def longest_matches(data: bytes, depth: int, cap: int = MAX_MATCH) -> tuple[list[int], list[int]]:
    """For each position, the longest match the 13-bit offset field can reach.

    Matching runs on a memoryview and compares slices rather than single bytes,
    because these payloads are large and highly repetitive - a byte-at-a-time
    scan spends minutes where this spends seconds. `cap` bounds how far a single
    match is followed; past a few hundred bytes the extra length buys almost no
    bits.
    """
    limit = len(data)
    view = memoryview(data)
    best_length = [0] * limit
    best_distance = [0] * limit
    heads: dict[int, int] = {}
    chain = [-1] * limit
    for position in range(limit):
        if position + MIN_MATCH > limit:
            continue
        key = data[position] | (data[position + 1] << 8) | (data[position + 2] << 16)
        candidate = heads.get(key, -1)
        tries = 0
        reach = min(cap, limit - position)
        longest = 0
        while candidate >= 0 and tries < depth:
            distance = position - candidate
            if distance > MAX_DISTANCE:
                break
            if distance >= MIN_DISTANCE and longest < reach:
                # Reject fast on the byte that would have to improve, then
                # confirm the whole prefix before trusting the candidate.
                if view[candidate + longest] == view[position + longest]:
                    length = longest + 1
                    if view[candidate : candidate + length] == view[position : position + length]:
                        while length < reach and view[candidate + length] == view[position + length]:
                            length += 1
                        longest = length
                        best_length[position] = length
                        best_distance[position] = distance
                        if length >= reach:
                            break
            candidate = chain[candidate]
            tries += 1
        chain[position] = heads.get(key, -1)
        heads[key] = position
    return best_length, best_distance


def parse_stream(data: bytes, depth: int = 32, window: int = 64, cap: int = MAX_MATCH) -> list[tuple[int, int]]:
    """Pick the cheapest parse and return it as (length, distance) tokens.

    A literal is (0, byte). Every literal costs 9 bits and every match 14 bits
    plus its length field, so a shortest-path walk over those costs gives the
    smallest stream - a greedy parse overshoots the CPK slot the entry has to
    fit back into. The walk only weighs the first `window` match lengths plus
    the longest one, which keeps it linear without measurably costing size.
    """
    limit = len(data)
    best_length, best_distance = longest_matches(data, depth, cap)
    cost = [0] * (limit + 1)
    choice = [0] * (limit + 1)
    for position in range(limit - 1, -1, -1):
        best = 9 + cost[position + 1]
        pick = 0
        longest = best_length[position]
        for length in range(MIN_MATCH, min(longest, window) + 1):
            candidate = 14 + length_bits(length) + cost[position + length]
            if candidate < best:
                best = candidate
                pick = length
        if longest > window:
            candidate = 14 + length_bits(longest) + cost[position + longest]
            if candidate < best:
                best = candidate
                pick = longest
        cost[position] = best
        choice[position] = pick

    tokens: list[tuple[int, int]] = []
    position = 0
    while position < limit:
        length = choice[position]
        if length:
            tokens.append((length, best_distance[position]))
            position += length
        else:
            tokens.append((0, data[position]))
            position += 1
    return tokens


def token_bits(tokens: list[tuple[int, int]]) -> int:
    total = 0
    for length, _ in tokens:
        total += 9 if length == 0 else 14 + length_bits(length)
    return total


def emit(data: bytes, tokens: list[tuple[int, int]]) -> bytes:
    writer = BitWriter()
    for length, value in tokens:
        if length:
            writer.write(1, 1)
            writer.write(value - MIN_DISTANCE, 13)
            write_length(writer, length)
        else:
            writer.write(0, 1)
            writer.write(value, 8)
    return writer.pack()


def expand_tokens(data: bytes, tokens: list[tuple[int, int]], needed_bits: int) -> list[tuple[int, int]]:
    """Grow the parse until it very nearly fills the slot it has to sit in.

    Every original entry ends its bit stream 4 to 7 bytes short of the slot, so
    a stream that stops thousands of bytes early is a shape the game never
    produced. Splitting a match back into literals leaves the decoded bytes
    untouched while making the stream longer, which is the only lever that
    lengthens a stream without changing what it decodes to. Matches are split
    largest-gain-first so the count stays small; the last few bits are made up
    by three-byte matches, whose 11-bit gain is finer than the seven bytes of
    slack the target allows.
    """
    if needed_bits <= 0:
        return tokens
    gains = []
    for index, (length, _) in enumerate(tokens):
        if length:
            gains.append((9 * length - 14 - length_bits(length), index))
    gains.sort(reverse=True)
    chosen: set[int] = set()
    remaining = needed_bits
    low = 0
    high = len(gains)
    # gains is descending, so walk it once picking the largest gain that still fits.
    while remaining > 0 and low < high:
        while low < high and gains[low][0] > remaining:
            low += 1
        if low >= high:
            break
        gain, index = gains[low]
        chosen.add(index)
        remaining -= gain
        low += 1

    if not chosen:
        return tokens
    grown: list[tuple[int, int]] = []
    position = 0
    for index, (length, value) in enumerate(tokens):
        if length == 0:
            grown.append((length, value))
            position += 1
            continue
        if index in chosen:
            for offset in range(length):
                grown.append((0, data[position + offset]))
        else:
            grown.append((length, value))
        position += length
    return grown


def compress_stream(data: bytes, depth: int = 32, window: int = 64, cap: int = MAX_MATCH) -> bytes:
    return emit(data, parse_stream(data, depth=depth, window=window, cap=cap))


# Search effort levels: (chain depth, parse window, match cap). Level 0 packs a
# 300 KB entry in seconds; the deeper levels cost minutes and are only worth it
# when the result still has to shrink to fit its CPK slot.
EFFORT_LEVELS = ((32, 64, 512), (128, 256, 2048), (512, 1024, MAX_DISTANCE))


def compress(payload: bytes, effort: int = 0) -> bytes:
    """Build a complete entry: header, backwards bit stream, raw 0x100 block."""
    if len(payload) <= RAW_HEADER_SIZE:
        raise ValueError("payload is smaller than the raw CRILAYLA header block")
    depth, window, cap = EFFORT_LEVELS[min(effort, len(EFFORT_LEVELS) - 1)]
    body = payload[RAW_HEADER_SIZE:]
    stream = compress_stream(body[::-1], depth=depth, window=window, cap=cap)
    header = struct.pack("<IIII", 0, 0, len(body), len(stream))
    return header + stream + payload[:RAW_HEADER_SIZE]


def compress_to_fit(payload: bytes, capacity: int) -> tuple[bytes, int]:
    """Compress to exactly `capacity` bytes, the size the CPK slot declares.

    Every original entry satisfies 16 + compressed + 0x100 == file size and its
    bit stream stops only 4 to 7 bytes short of that, so the game finds both the
    raw header block and the start of the stream where the slot ends. Zeroing
    the leftover bytes at either end produces a layout the original packer never
    emits, so the parse is grown to fill the slot instead and the remainder is
    the same few bytes of alignment slack the originals carry.
    """
    target = capacity - RAW_HEADER_SIZE - 16
    if target <= 0:
        raise ValueError(f"slot of {capacity} bytes is too small for a CRILAYLA entry")
    body = payload[RAW_HEADER_SIZE:]
    reversed_body = body[::-1]
    for effort in range(len(EFFORT_LEVELS)):
        depth, window, cap = EFFORT_LEVELS[effort]
        tokens = parse_stream(reversed_body, depth=depth, window=window, cap=cap)
        bits = token_bits(tokens)
        if (bits + 7) // 8 > target:
            continue
        tokens = expand_tokens(reversed_body, tokens, target * 8 - token_bits(tokens))
        stream = emit(reversed_body, tokens)
        if len(stream) > target:
            raise ValueError(f"grown stream of {len(stream)} bytes overflows its {target} byte slot")
        padded = bytes(target - len(stream)) + stream
        header = struct.pack("<IIII", 0, 0, len(body), target)
        return header + padded + payload[:RAW_HEADER_SIZE], effort
    raise ValueError(f"entry does not fit its {capacity} byte slot at any search effort")
