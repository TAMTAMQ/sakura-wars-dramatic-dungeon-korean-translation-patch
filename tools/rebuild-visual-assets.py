#!/usr/bin/env python3
"""Rebuild edited PNG previews into fixed-size Nintendo DS graphic assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from pathlib import Path, PurePosixPath

from PIL import Image

from importlib.util import module_from_spec, spec_from_file_location


CONVERTER_PATH = Path(__file__).with_name("convert-visual-assets.py")
SPEC = spec_from_file_location("convert_visual_assets", CONVERTER_PATH)
assert SPEC is not None and SPEC.loader is not None
CONVERTER = module_from_spec(SPEC)
SPEC.loader.exec_module(CONVERTER)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--png-root", type=Path, required=True)
    parser.add_argument("--replacement-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--update-index", action="store_true")
    parser.add_argument(
        "--merge-tiles",
        action="store_true",
        help="fold the closest tile pairs together when an edit needs more tiles than the bank holds",
    )
    parser.add_argument(
        "--merge-edit-box",
        nargs=4,
        type=int,
        metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
        help="when merging tiles, protect every cell outside this edited pixel rectangle",
    )
    parser.add_argument("--path-prefix", default="")
    return parser.parse_args()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def indexed_pixels(image: Image.Image, palette: list[tuple[int, int, int]]) -> list[int]:
    if image.mode == "P":
        png_palette = image.getpalette() or []
        expected = [component for color in palette for component in color]
        if png_palette[: len(expected)] == expected:
            values = list(image.get_flattened_data())
            if values and max(values) >= len(palette):
                raise ValueError("PNG uses a palette index outside the source palette")
            return values
    rgb = image.convert("RGB")
    cache: dict[tuple[int, int, int], int] = {}
    result = []
    for color in rgb.get_flattened_data():
        if color not in cache:
            cache[color] = min(
                range(len(palette)),
                key=lambda index: sum((color[channel] - palette[index][channel]) ** 2 for channel in range(3)),
            )
        result.append(cache[color])
    return result


def flip_tile(tile: bytes, flip_x: bool, flip_y: bool) -> bytes:
    """Mirror an 8x8 tile the way the tile-map flip bits do when drawing it."""
    if not flip_x and not flip_y:
        return tile
    result = bytearray(64)
    for y in range(8):
        source_y = 7 - y if flip_y else y
        for x in range(8):
            source_x = 7 - x if flip_x else x
            result[y * 8 + x] = tile[source_y * 8 + source_x]
    return bytes(result)


def merge_nearest_tiles(
    stored: list[bytes],
    assignment: list[tuple[int, bool, bool]],
    capacity: int,
    protected_cells: set[int] | None = None,
) -> tuple[list[bytes], list[tuple[int, bool, bool]]]:
    """Fold the closest tile pairs together until the tile bank fits.

    Edited artwork can need a few more unique tiles than the original bank
    holds. Each merge drops the rarer tile of the most similar pair, which
    costs a handful of pixels inside one 8x8 block.
    """
    import numpy

    while len(stored) > capacity:
        bank = numpy.frombuffer(b"".join(stored), dtype=numpy.uint8).reshape(len(stored), 8, 8)
        usage = [0] * len(stored)
        protected_usage = [0] * len(stored)
        for cell, (index, _, _) in enumerate(assignment):
            usage[index] += 1
            if protected_cells is not None and cell in protected_cells:
                protected_usage[index] += 1
        best: tuple[int, int, int, bool, bool] | None = None
        for flip_x, flip_y in ((False, False), (True, False), (False, True), (True, True)):
            variant = bank
            if flip_x:
                variant = variant[:, :, ::-1]
            if flip_y:
                variant = variant[:, ::-1, :]
            distance = (
                bank.reshape(len(stored), 1, 64) != variant.reshape(1, len(stored), 64)
            ).sum(axis=2)
            # Dropping a tile used by an untouched cell would leak an edit into
            # unrelated artwork. Prefer a tile used only by deliberately changed
            # cells, then minimize visual distance and usage count.
            score = distance.astype(numpy.int64)
            score += numpy.asarray(protected_usage, dtype=numpy.int64)[None, :] * 1_000_000
            score += numpy.asarray(usage, dtype=numpy.int64)[None, :] * 100
            invalid = numpy.asarray(usage)[:, None] < numpy.asarray(usage)[None, :]
            score[invalid] = 10**12
            if protected_cells is not None:
                score[:, numpy.asarray(protected_usage) > 0] = 10**12
                score[numpy.asarray(protected_usage) > 0, :] = 10**12
            numpy.fill_diagonal(score, 10**12)
            position = int(score.argmin())
            if int(score.flat[position]) >= 10**12:
                continue
            keep, drop = divmod(position, len(stored))
            candidate = (int(score.flat[position]), keep, drop, flip_x, flip_y)
            if best is None or candidate[0] < best[0]:
                best = candidate
        if best is None:
            raise ValueError("no tile pair could be merged")
        _, keep, drop, flip_x, flip_y = best
        merged = []
        for index, cell_flip_x, cell_flip_y in assignment:
            if index == drop:
                index = keep
                cell_flip_x = cell_flip_x != flip_x
                cell_flip_y = cell_flip_y != flip_y
            if index > drop:
                index -= 1
            merged.append((index, cell_flip_x, cell_flip_y))
        assignment = merged
        stored = stored[:drop] + stored[drop + 1 :]
    return stored, assignment


def rebuild_custom(
    source_path: Path,
    png_path: Path,
    merge_tiles: bool = False,
    merge_edit_box: tuple[int, int, int, int] | None = None,
) -> bytes:
    source = source_path.read_bytes()
    parsed = CONVERTER.chunks(source)
    palette_raw = parsed[b"CLUT"][1]
    palette = [CONVERTER.rgb555(value) for value in struct.unpack(f"<{len(palette_raw) // 2}H", palette_raw)]
    map_position, map_raw = parsed[b"CMAP"]
    char_position, character_raw = parsed[b"CHAR"]
    tile_map = list(struct.unpack(f"<{len(map_raw) // 2}H", map_raw))
    width_tiles, height_tiles = CONVERTER.custom_grid(len(tile_map))
    image = Image.open(png_path)
    expected_size = (width_tiles * 8, height_tiles * 8)
    if image.size != expected_size:
        raise ValueError(f"PNG dimensions must remain {expected_size}: {png_path}")
    pixels = indexed_pixels(image, palette)
    desired_by_original: dict[int, bytes] = {}
    conflict = False
    displayed_cells = []
    changed_cells: set[int] = set()
    for cell, entry in enumerate(tile_map):
        flip_x = bool(entry & 0x0400)
        flip_y = bool(entry & 0x0800)
        origin_x = (cell % width_tiles) * 8
        origin_y = (cell // width_tiles) * 8
        displayed = bytearray(64)
        for y in range(8):
            for x in range(8):
                displayed[y * 8 + x] = pixels[(origin_y + y) * image.width + origin_x + x]
        displayed = bytes(displayed)
        displayed_cells.append(displayed)
        raw_tile = flip_tile(displayed, flip_x, flip_y)
        original_index = entry & 0x03FF
        original_raw = character_raw[original_index * 64 : original_index * 64 + 64]
        if displayed != flip_tile(original_raw, flip_x, flip_y):
            changed_cells.add(cell)
        if original_index in desired_by_original and desired_by_original[original_index] != raw_tile:
            conflict = True
        desired_by_original[original_index] = raw_tile
    rebuilt_map = tile_map
    rebuilt_char = bytearray(character_raw)
    if conflict:
        stored: list[bytes] = []
        index_of: dict[bytes, int] = {}
        assignment: list[tuple[int, bool, bool]] = []
        for displayed in displayed_cells:
            reused = None
            for flip_x, flip_y in ((False, False), (True, False), (False, True), (True, True)):
                candidate = flip_tile(displayed, flip_x, flip_y)
                if candidate in index_of:
                    reused = (index_of[candidate], flip_x, flip_y)
                    break
            if reused is None:
                index_of[displayed] = len(stored)
                stored.append(displayed)
                reused = (index_of[displayed], False, False)
            assignment.append(reused)
        capacity = len(character_raw) // 64
        if len(stored) > capacity and merge_tiles:
            if merge_edit_box is None:
                protected_cells = set(range(len(displayed_cells))) - changed_cells
            else:
                left, top, right, bottom = merge_edit_box
                edited_cells = {
                    cell
                    for cell in range(len(displayed_cells))
                    if (cell % width_tiles) * 8 >= left
                    and (cell % width_tiles) * 8 + 8 <= right
                    and (cell // width_tiles) * 8 >= top
                    and (cell // width_tiles) * 8 + 8 <= bottom
                }
                protected_cells = set(range(len(displayed_cells))) - edited_cells
            stored, assignment = merge_nearest_tiles(
                stored, assignment, capacity, protected_cells=protected_cells
            )
        if len(stored) > capacity:
            raise ValueError(f"edited PNG needs {len(stored)} tiles but only {capacity} fit")
        rebuilt_map = []
        for cell, (tile_index, flip_x, flip_y) in enumerate(assignment):
            bits = (0x0400 if flip_x else 0) | (0x0800 if flip_y else 0)
            rebuilt_map.append((tile_map[cell] & 0xF000) | bits | tile_index)
        rebuilt_char[:] = b"\0" * len(rebuilt_char)
        for tile_index, tile in enumerate(stored):
            rebuilt_char[tile_index * 64 : tile_index * 64 + 64] = tile
    else:
        for tile_index, tile in desired_by_original.items():
            rebuilt_char[tile_index * 64 : tile_index * 64 + 64] = tile
    result = bytearray(source)
    result[map_position + 8 : map_position + 8 + len(map_raw)] = struct.pack(f"<{len(rebuilt_map)}H", *rebuilt_map)
    result[char_position + 8 : char_position + 8 + len(character_raw)] = rebuilt_char
    return bytes(result)


def rebuild_tile_sheet(source_path: Path, png_path: Path) -> bytes:
    """Write an edited tile sheet back into a CLUT+CHAR container."""
    source = source_path.read_bytes()
    parsed = CONVERTER.chunks(source)
    palette_raw = parsed[b"CLUT"][1]
    char_position, character_raw = parsed[b"CHAR"]
    palette = [CONVERTER.rgb555(value) for value in struct.unpack(f"<{len(palette_raw) // 2}H", palette_raw)]
    bytes_per_tile = 32 if len(palette) <= 16 else 64
    tile_count = len(character_raw) // bytes_per_tile
    width_tiles = math.ceil(math.sqrt(tile_count))
    height_tiles = math.ceil(tile_count / width_tiles)
    image = Image.open(png_path)
    if image.size != (width_tiles * 8, height_tiles * 8):
        raise ValueError(f"tile-sheet dimensions must remain {(width_tiles * 8, height_tiles * 8)}: {png_path}")
    pixels = indexed_pixels(image, palette)
    raw = bytearray(len(character_raw))
    for tile_index in range(tile_count):
        origin_x = (tile_index % width_tiles) * 8
        origin_y = (tile_index // width_tiles) * 8
        for y in range(8):
            if bytes_per_tile == 32:
                for pair in range(4):
                    low = pixels[(origin_y + y) * image.width + origin_x + pair * 2]
                    high = pixels[(origin_y + y) * image.width + origin_x + pair * 2 + 1]
                    if low > 15 or high > 15:
                        raise ValueError(f"PNG uses a color outside the 16-color bank: {png_path}")
                    raw[tile_index * 32 + y * 4 + pair] = low | (high << 4)
            else:
                for x in range(8):
                    raw[tile_index * 64 + y * 8 + x] = pixels[(origin_y + y) * image.width + origin_x + x]
    result = bytearray(source)
    result[char_position + 8 : char_position + 8 + len(character_raw)] = raw
    return bytes(result)


def rebuild_ncgr(source_path: Path, png_path: Path, palette_path: Path) -> bytes:
    graphic = CONVERTER.parse_ncgr(source_path)
    source = source_path.read_bytes()
    section = 16
    data_size = struct.unpack_from("<I", source, section + 24)[0]
    data_offset = struct.unpack_from("<I", source, section + 28)[0]
    data_start = section + 8 + data_offset
    bytes_per_tile = graphic["bytes_per_tile"]
    tile_count = graphic["tile_count"]
    width_tiles = math.ceil(math.sqrt(tile_count))
    height_tiles = math.ceil(tile_count / width_tiles)
    image = Image.open(png_path)
    if image.size != (width_tiles * 8, height_tiles * 8):
        raise ValueError(f"NCGR tile-sheet dimensions changed: {png_path}")
    palettes = CONVERTER.nclr_palettes(palette_path)
    # 8bpp tiles index one flat 256-colour table; 4bpp tiles index a single bank.
    palette = [color for bank in palettes for color in bank] if bytes_per_tile == 64 else palettes[0]
    pixels = indexed_pixels(image, palette)
    raw = bytearray(data_size)
    for tile_index in range(tile_count):
        origin_x = (tile_index % width_tiles) * 8
        origin_y = (tile_index // width_tiles) * 8
        for y in range(8):
            row = (origin_y + y) * image.width + origin_x
            if bytes_per_tile == 32:
                for pair in range(4):
                    low = pixels[row + pair * 2]
                    high = pixels[row + pair * 2 + 1]
                    if low > 15 or high > 15:
                        raise ValueError(f"NCGR PNG uses a color outside the 16-color bank: {png_path}")
                    raw[tile_index * 32 + y * 4 + pair] = low | (high << 4)
            else:
                raw[tile_index * 64 + y * 8 : tile_index * 64 + y * 8 + 8] = bytes(pixels[row : row + 8])
    result = bytearray(source)
    result[data_start : data_start + data_size] = raw
    return bytes(result)


def rgba_palette_indexes(image: Image.Image, palette: list[tuple[int, int, int]]) -> list[int]:
    rgba = image.convert("RGBA")
    cache: dict[tuple[int, int, int], int] = {}
    result = []
    for red, green, blue, alpha in rgba.get_flattened_data():
        if alpha < 128:
            result.append(0)
            continue
        color = (red, green, blue)
        if color not in cache:
            # Index zero is transparent in an OBJ palette. Opaque pixels use 1..N.
            candidates = range(1, len(palette)) if len(palette) > 1 else range(len(palette))
            cache[color] = min(
                candidates,
                key=lambda index: sum((color[channel] - palette[index][channel]) ** 2 for channel in range(3)),
            )
        result.append(cache[color])
    return result


def encode_tile(pixels: list[int], pixel_format: int) -> bytes:
    if pixel_format == 4:
        return bytes(pixels)
    raw = bytearray(32)
    for index in range(32):
        low = pixels[index * 2]
        high = pixels[index * 2 + 1]
        if low > 15 or high > 15:
            raise ValueError("4bpp NCGR cell uses a color outside its 16-color OBJ palette")
        raw[index] = low | (high << 4)
    return bytes(raw)


def prefer_original_indexes(
    pixels: list[int], original: list[int], palette: list[tuple[int, int, int]]
) -> list[int]:
    """Keep the source index wherever the color did not change.

    Palettes repeat colors, so a reverse RGB lookup can land on a different
    index that draws the same pixel. Preserving the original index keeps an
    untouched tile byte-identical.
    """
    result = []
    for new_index, old_index in zip(pixels, original):
        if new_index != old_index and old_index < len(palette) and new_index < len(palette):
            if palette[old_index] == palette[new_index]:
                new_index = old_index
        result.append(new_index)
    return result


def object_ownership(
    cell: dict,
    visible: list[dict],
    graphic: dict,
    layout: dict,
    origin: tuple[int, int],
    size: tuple[int, int],
) -> list[int]:
    """Say which object each composed pixel came from.

    Objects can overlap, and the renderer paints lower OAM indices last, so a
    pixel shows the topmost object that is opaque there. Pixels an object lost
    to a neighbour cannot be read back out of the composed picture.
    """
    min_x, min_y = origin
    width, height = size
    owner = [-1] * (width * height)
    for index in reversed(range(len(visible))):
        obj = visible[index]
        base_tile = CONVERTER.object_tile_index(obj["tile_name"], layout["mapping_type"], graphic["pixel_format"])
        width_tiles = obj["width"] // 8
        for local_y in range(obj["height"]):
            for local_x in range(obj["width"]):
                source_x = obj["width"] - 1 - local_x if obj["flip_x"] else local_x
                source_y = obj["height"] - 1 - local_y if obj["flip_y"] else local_y
                tile_index = base_tile + (source_y // 8) * width_tiles + (source_x // 8)
                tile = CONVERTER.decode_tile(graphic["raw"], tile_index, graphic["pixel_format"])
                if tile[(source_y % 8) * 8 + (source_x % 8)] == 0:
                    continue
                x = obj["x"] - min_x + local_x
                y = obj["y"] - min_y + local_y
                if 0 <= x < width and 0 <= y < height:
                    owner[y * width + x] = index
    return owner


def composed_original(
    visible: list[dict],
    graphic: dict,
    layout: dict,
    origin: tuple[int, int],
    size: tuple[int, int],
) -> tuple[list[int], list[int]]:
    """Return the original composed picture and the geometric stacking order.

    `values` is what the untouched cell draws at each pixel, and `top` is the
    object that would draw there if it had anything opaque to draw - the object
    an edit has to be written into when the artwork gains a pixel where the
    original was empty.
    """
    min_x, min_y = origin
    width, height = size
    values = [0] * (width * height)
    top = [-1] * (width * height)
    for index in reversed(range(len(visible))):
        obj = visible[index]
        base_tile = CONVERTER.object_tile_index(obj["tile_name"], layout["mapping_type"], graphic["pixel_format"])
        width_tiles = obj["width"] // 8
        for local_y in range(obj["height"]):
            y = obj["y"] - min_y + local_y
            if not 0 <= y < height:
                continue
            for local_x in range(obj["width"]):
                x = obj["x"] - min_x + local_x
                if not 0 <= x < width:
                    continue
                top[y * width + x] = index
                source_x = obj["width"] - 1 - local_x if obj["flip_x"] else local_x
                source_y = obj["height"] - 1 - local_y if obj["flip_y"] else local_y
                tile_index = base_tile + (source_y // 8) * width_tiles + (source_x // 8)
                tile = CONVERTER.decode_tile(graphic["raw"], tile_index, graphic["pixel_format"])
                pixel = tile[(source_y % 8) * 8 + (source_x % 8)]
                if pixel:
                    values[y * width + x] = pixel
    return values, top


def canonical_indexes(palette: list[tuple[int, int, int]]) -> list[int]:
    """Map every palette index onto the first index that draws the same color.

    Palettes repeat colors, so the reverse lookup that turns an edited PNG back
    into indexes can pick a different index for an unchanged pixel. Comparing
    canonical indexes asks whether the picture changed rather than whether the
    bytes did.
    """
    first: dict[tuple[int, int, int], int] = {}
    result = [0] * len(palette)
    for index in range(1, len(palette)):
        result[index] = first.setdefault(palette[index], index)
    return result


def place_edited_pixels(
    pixels: list[int],
    obj: dict,
    owner: list[int],
    top: list[int],
    original: list[int],
    canonical: list[int],
    index: int,
    origin: tuple[int, int],
    size: tuple[int, int],
    graphic: dict,
    layout: dict,
) -> list[int]:
    """Decide what this object stores for every pixel of the composed picture.

    Where the artwork is unchanged the original bytes are put back, so an
    untouched cell rebuilds byte for byte. Where it changed, only the object on
    top may carry the new pixel and every object under it is cleared: leaving
    the old bytes in a covered object makes the previous artwork reappear
    wherever the edit erased the object above it.

    `pixels` is in the object's stored orientation, so the flips are undone
    again here to look the pixel up in the composed picture.
    """
    min_x, min_y = origin
    width, height = size
    base_tile = CONVERTER.object_tile_index(obj["tile_name"], layout["mapping_type"], graphic["pixel_format"])
    width_tiles = obj["width"] // 8
    result = list(pixels)
    for source_y in range(obj["height"]):
        for source_x in range(obj["width"]):
            local_x = obj["width"] - 1 - source_x if obj["flip_x"] else source_x
            local_y = obj["height"] - 1 - source_y if obj["flip_y"] else source_y
            x = obj["x"] - min_x + local_x
            y = obj["y"] - min_y + local_y
            if not (0 <= x < width and 0 <= y < height):
                continue
            offset = source_y * obj["width"] + source_x
            if canonical[result[offset]] == canonical[original[y * width + x]]:
                if owner[y * width + x] == index:
                    continue
                tile_index = base_tile + (source_y // 8) * width_tiles + (source_x // 8)
                tile = CONVERTER.decode_tile(graphic["raw"], tile_index, graphic["pixel_format"])
                result[offset] = tile[(source_y % 8) * 8 + (source_x % 8)]
            elif top[y * width + x] != index:
                result[offset] = 0
    return result


def rebuild_ncer_ncgr(source_path: Path, cell_root: Path, palette_path: Path, ncer_path: Path) -> bytes:
    source = source_path.read_bytes()
    graphic = CONVERTER.parse_ncgr(source_path)
    layout = CONVERTER.parse_ncer(ncer_path)
    palettes = CONVERTER.nclr_palettes(palette_path)
    mapping_type = layout["mapping_type"]
    pixel_format = graphic["pixel_format"]
    desired_tiles: dict[int, bytes] = {}
    for cell in layout["cells"]:
        visible = [obj for obj in cell["objects"] if not obj["hidden"]]
        if visible:
            min_x = min(obj["x"] for obj in visible)
            min_y = min(obj["y"] for obj in visible)
            max_x = max(obj["x"] + obj["width"] for obj in visible)
            max_y = max(obj["y"] + obj["height"] for obj in visible)
            expected_size = (max_x - min_x, max_y - min_y)
        else:
            expected_size = (1, 1)
        png_path = cell_root / f"cell-{cell['index']:03d}.png"
        image = Image.open(png_path)
        if image.size != expected_size:
            raise ValueError(f"NCER cell dimensions must remain {expected_size}: {png_path}")
        owner = object_ownership(cell, visible, graphic, layout, (min_x, min_y), expected_size)
        original_pixels, top_object = composed_original(visible, graphic, layout, (min_x, min_y), expected_size)
        for obj in visible:
            box = (
                obj["x"] - min_x,
                obj["y"] - min_y,
                obj["x"] - min_x + obj["width"],
                obj["y"] - min_y + obj["height"],
            )
            object_image = image.crop(box)
            if obj["flip_y"]:
                object_image = object_image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            if obj["flip_x"]:
                object_image = object_image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            palette_bank = 0 if graphic["pixel_format"] == 4 else obj["palette_bank"]
            palette = [color for bank in palettes for color in bank] if graphic["pixel_format"] == 4 else palettes[palette_bank]
            canonical = canonical_indexes(palette)
            pixels = rgba_palette_indexes(object_image, palette)
            pixels = place_edited_pixels(
                pixels,
                obj,
                owner,
                top_object,
                original_pixels,
                canonical,
                visible.index(obj),
                (min_x, min_y),
                expected_size,
                graphic,
                layout,
            )
            width_tiles = obj["width"] // 8
            height_tiles = obj["height"] // 8
            base_tile = CONVERTER.object_tile_index(obj["tile_name"], mapping_type, pixel_format)
            for tile_y in range(height_tiles):
                for tile_x in range(width_tiles):
                    tile_pixels = []
                    for y in range(8):
                        row = (tile_y * 8 + y) * object_image.width + tile_x * 8
                        tile_pixels.extend(pixels[row : row + 8])
                    tile_index = base_tile + tile_y * width_tiles + tile_x
                    tile_pixels = prefer_original_indexes(
                        tile_pixels,
                        CONVERTER.decode_tile(graphic["raw"], tile_index, graphic["pixel_format"]),
                        palette,
                    )
                    encoded = encode_tile(tile_pixels, graphic["pixel_format"])
                    bytes_per_tile = graphic["bytes_per_tile"]
                    original = graphic["raw"][tile_index * bytes_per_tile : (tile_index + 1) * bytes_per_tile]
                    previous = desired_tiles.get(tile_index)
                    if previous is not None and previous != encoded:
                        # Cells can share a tile under different palette banks, so an
                        # untouched cell may re-encode to different indexes. Keep the
                        # edit and only fail when two cells disagree about a change.
                        if encoded == original:
                            continue
                        if previous != original:
                            raise ValueError(
                                f"cell edits conflict on shared NCGR tile {tile_index}: {source_path.name}"
                            )
                    desired_tiles[tile_index] = encoded
    rebuilt_raw = bytearray(graphic["raw"])
    bytes_per_tile = graphic["bytes_per_tile"]
    for tile_index, encoded in desired_tiles.items():
        if tile_index >= graphic["tile_count"]:
            raise ValueError(f"NCER references tile {tile_index} outside {source_path.name}")
        start = tile_index * bytes_per_tile
        rebuilt_raw[start : start + bytes_per_tile] = encoded
    result = bytearray(source)
    data_start = graphic["data_start"]
    result[data_start : data_start + len(rebuilt_raw)] = rebuilt_raw
    return bytes(result)


def main() -> None:
    args = parse_args()
    index = json.loads(args.index.read_text(encoding="utf-8"))
    source_root = args.source_root.resolve()
    png_root = args.png_root.resolve()
    replacement_root = args.replacement_root.resolve()
    asset_root = args.index.resolve().parent
    changed = []
    unchanged = 0
    for record in index["records"]:
        if not record["path"].startswith(args.path_prefix):
            continue
        relative = PurePosixPath(record["path"])
        source_path = (source_root / relative).resolve()
        if record["asset_kind"] == "game_specific_binary_image":
            png_path = (png_root / relative.with_suffix(".png")).resolve()
            rebuilt = rebuild_custom(
                source_path,
                png_path,
                args.merge_tiles,
                tuple(args.merge_edit_box) if args.merge_edit_box else None,
            )
        elif record["asset_kind"] == "game_specific_tile_sheet":
            png_path = (png_root / relative.with_suffix(".tiles.png")).resolve()
            rebuilt = rebuild_tile_sheet(source_path, png_path)
        elif record["asset_kind"] == "nintendo_ds_ncgr_image":
            ncer_path = source_path.with_suffix(".NCER")
            cell_root = (png_root / relative.with_suffix(".cells")).resolve()
            sheet_path = (png_root / relative.with_suffix(".tiles.png")).resolve()
            rebuilt = None
            if ncer_path.exists() and cell_root.is_dir():
                rebuilt = rebuild_ncer_ncgr(
                    source_path, cell_root, source_path.with_suffix(".NCLR"), ncer_path
                )
            # Assets whose cells cannot be split back also ship a tile sheet.
            # Fall back to it when the cells carry no edit of their own.
            if (rebuilt is None or rebuilt == source_path.read_bytes()) and sheet_path.exists():
                rebuilt = rebuild_ncgr(source_path, sheet_path, source_path.with_suffix(".NCLR"))
            if rebuilt is None:
                raise ValueError(f"no edited PNG found for {record['id']}")
        else:
            continue
        original = source_path.read_bytes()
        if rebuilt == original:
            unchanged += 1
            continue
        output = (replacement_root / relative).resolve()
        if not output.is_relative_to(replacement_root):
            raise ValueError(f"replacement path escapes output root: {record['id']}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(rebuilt)
        if args.update_index:
            if not output.is_relative_to(asset_root):
                raise ValueError("--update-index requires replacement-root inside the visual asset directory")
            replacement_path = output.relative_to(asset_root).as_posix()
            digest = sha256(rebuilt)
            # A rebuild that lands on the same bytes is not new work, so an
            # approval already given must survive it. Demoting unconditionally
            # is how reviewed images silently drop out of the build.
            unchanged_replacement = (
                record.get("replacement_sha256") == digest
                and record.get("replacement_path") == replacement_path
            )
            record["translation_status"] = "translated"
            if not unchanged_replacement:
                record["review_status"] = "in_progress"
            record["replacement_path"] = replacement_path
            record["replacement_sha256"] = digest
        changed.append({"id": record["id"], "replacement_path": str(output), "sha256": sha256(rebuilt)})
    if args.update_index and changed:
        args.index.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "schema_version": 1,
        "unchanged_roundtrip_count": unchanged,
        "changed_replacement_count": len(changed),
        "index_updated": bool(args.update_index and changed),
        "changed": changed,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
