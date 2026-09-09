#!/usr/bin/env python3
"""Render indexed Nintendo DS graphics as editable PNG previews."""

from __future__ import annotations

import argparse
import html
import json
import math
import struct
from pathlib import Path, PurePosixPath

from PIL import Image


CUSTOM_DIMENSIONS = {42: (14, 3), 24: (12, 2), 768: (32, 24), 960: (32, 30)}


def custom_grid(cell_count: int) -> tuple[int, int]:
    """Pick the tile grid for a CMAP whose container stores no dimensions.

    Confirmed layouts win; anything else takes the factor pair closest to the
    DS screen ratio, which keeps the exported PNG readable. The choice only
    affects how tiles are arranged for editing - the round trip stays lossless
    because the rebuild uses the same grid.
    """
    if cell_count in CUSTOM_DIMENSIONS:
        return CUSTOM_DIMENSIONS[cell_count]
    factors = [
        (width, cell_count // width)
        for width in range(1, cell_count + 1)
        if cell_count % width == 0 and width <= 32
    ]
    if not factors:
        return (cell_count, 1)
    return min(factors, key=lambda pair: abs(pair[0] / pair[1] - 4 / 3))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


def rgb555(value: int) -> tuple[int, int, int]:
    return tuple(((value >> shift) & 31) * 255 // 31 for shift in (0, 5, 10))


def paletted_image(width: int, height: int, palette: list[tuple[int, int, int]]) -> Image.Image:
    image = Image.new("P", (width, height))
    flat = [component for color in palette for component in color]
    image.putpalette(flat + [0] * (768 - len(flat)))
    return image


def chunks(data: bytes) -> dict[bytes, tuple[int, bytes]]:
    result = {}
    position = 0
    while position + 8 <= len(data):
        tag = data[position : position + 4]
        size = struct.unpack_from("<I", data, position + 4)[0] * 4
        if size < 8 or position + size > len(data):
            raise ValueError(f"invalid chunk {tag!r} at {position:#x}")
        result[tag] = (position, data[position + 8 : position + size])
        position += size
    if position != len(data):
        raise ValueError("chunk stream has trailing bytes")
    return result


def render_custom(path: Path) -> tuple[Image.Image, dict]:
    data = path.read_bytes()
    parsed = chunks(data)
    palette_raw = parsed[b"CLUT"][1]
    map_raw = parsed[b"CMAP"][1]
    character_raw = parsed[b"CHAR"][1]
    palette = [rgb555(value) for value in struct.unpack(f"<{len(palette_raw) // 2}H", palette_raw)]
    tile_map = struct.unpack(f"<{len(map_raw) // 2}H", map_raw)
    if len(character_raw) % 64:
        raise ValueError(f"unsupported custom image geometry: {path}")
    width_tiles, height_tiles = custom_grid(len(tile_map))
    tile_count = len(character_raw) // 64
    image = paletted_image(width_tiles * 8, height_tiles * 8, palette)
    pixels = image.load()
    for cell, entry in enumerate(tile_map):
        tile_index = entry & 0x03FF
        if tile_index >= tile_count:
            raise ValueError(f"tile index outside CHAR: {path}")
        flip_x = bool(entry & 0x0400)
        flip_y = bool(entry & 0x0800)
        tile = character_raw[tile_index * 64 : tile_index * 64 + 64]
        origin_x = (cell % width_tiles) * 8
        origin_y = (cell // width_tiles) * 8
        for y in range(8):
            for x in range(8):
                source_x = 7 - x if flip_x else x
                source_y = 7 - y if flip_y else y
                pixels[origin_x + x, origin_y + y] = tile[source_y * 8 + source_x]
    return image, {
        "decoder": "clut-cmap-char-8bpp",
        "width": image.width,
        "height": image.height,
        "palette_color_count": len(palette),
        "tile_count": tile_count,
        "map_entry_count": len(tile_map),
    }


def nclr_palettes(path: Path) -> list[list[tuple[int, int, int]]]:
    data = path.read_bytes()
    if data[:4] != b"RLCN" or data[16:20] != b"TTLP":
        raise ValueError(f"unsupported NCLR: {path}")
    section = 16
    data_size = struct.unpack_from("<I", data, section + 16)[0]
    data_offset = struct.unpack_from("<I", data, section + 20)[0]
    start = section + 8 + data_offset
    raw = data[start : start + data_size]
    colors = [rgb555(value) for value in struct.unpack(f"<{len(raw) // 2}H", raw)]
    return [colors[index : index + 16] for index in range(0, len(colors), 16)]


OBJ_DIMENSIONS = {
    0: ((8, 8), (16, 16), (32, 32), (64, 64)),
    1: ((16, 8), (32, 8), (32, 16), (64, 32)),
    2: ((8, 16), (8, 32), (16, 32), (32, 64)),
}


def signed_bits(value: int, bits: int) -> int:
    sign = 1 << (bits - 1)
    return value - (1 << bits) if value & sign else value


def parse_ncgr(path: Path) -> dict:
    data = path.read_bytes()
    if data[:4] != b"RGCN" or data[16:20] != b"RAHC":
        raise ValueError(f"unsupported NCGR: {path}")
    section = 16
    pixel_format = struct.unpack_from("<I", data, section + 12)[0]
    data_size = struct.unpack_from("<I", data, section + 24)[0]
    data_offset = struct.unpack_from("<I", data, section + 28)[0]
    start = section + 8 + data_offset
    bytes_per_tile = {3: 32, 4: 64}.get(pixel_format)
    if bytes_per_tile is None:
        raise ValueError(f"unsupported NCGR pixel format {pixel_format}: {path}")
    raw = data[start : start + data_size]
    if len(raw) % bytes_per_tile:
        raise ValueError(f"NCGR tile data is not aligned: {path}")
    return {
        "pixel_format": pixel_format,
        "bytes_per_tile": bytes_per_tile,
        "raw": raw,
        "tile_count": len(raw) // bytes_per_tile,
        "data_start": start,
    }


def parse_ncer(path: Path) -> dict:
    data = path.read_bytes()
    if data[:4] != b"RECN" or data[16:20] != b"KBEC":
        raise ValueError(f"unsupported NCER: {path}")
    content = 24
    cell_count, cell_type = struct.unpack_from("<HH", data, content)
    cell_offset, mapping_type = struct.unpack_from("<II", data, content + 4)
    if cell_offset != 0x18 or cell_type not in (0, 1):
        raise ValueError(f"unsupported NCER cell table: {path}")
    cell_size = 16 if cell_type else 8
    cells_start = content + cell_offset
    objects_start = cells_start + cell_count * cell_size
    cells = []
    max_object = 0
    for index in range(cell_count):
        offset = cells_start + index * cell_size
        object_count, cell_attribute, object_offset = struct.unpack_from("<HHI", data, offset)
        object_index = object_offset // 6
        max_object = max(max_object, object_index + object_count)
        cell = {
            "index": index,
            "object_count": object_count,
            "cell_attribute": cell_attribute,
            "object_index": object_index,
        }
        if cell_type:
            cell["stored_bounds"] = list(struct.unpack_from("<hhhh", data, offset + 8))
        cells.append(cell)
    objects = []
    for index in range(max_object):
        attr0, attr1, attr2 = struct.unpack_from("<HHH", data, objects_start + index * 6)
        shape = (attr0 >> 14) & 3
        size = (attr1 >> 14) & 3
        if shape not in OBJ_DIMENSIONS:
            raise ValueError(f"invalid OBJ shape in {path}: {shape}")
        width, height = OBJ_DIMENSIONS[shape][size]
        affine = bool(attr0 & 0x0100)
        objects.append({
            "index": index,
            "x": signed_bits(attr1 & 0x01FF, 9),
            "y": signed_bits(attr0 & 0x00FF, 8),
            "width": width,
            "height": height,
            "affine": affine,
            "hidden": bool(attr0 & 0x0200) and not affine,
            "color_8bpp": bool(attr0 & 0x2000),
            "flip_x": bool(attr1 & 0x1000) and not affine,
            "flip_y": bool(attr1 & 0x2000) and not affine,
            "tile_name": attr2 & 0x03FF,
            "palette_bank": (attr2 >> 12) & 15,
            "priority": (attr2 >> 10) & 3,
        })
    for cell in cells:
        start = cell["object_index"]
        cell["objects"] = objects[start : start + cell["object_count"]]
    return {"cell_type": cell_type, "mapping_type": mapping_type, "cells": cells}


def object_tile_index(tile_name: int, mapping_type: int, pixel_format: int) -> int:
    """Resolve an OAM tile name to an index into the NCGR tile bank.

    Mapping values 0..3 select 32/64/128/256-byte OBJ name units. A 4bpp tile
    is 32 bytes, so the unit count is the tile index; a 8bpp tile is 64 bytes,
    so the same unit count addresses half as many tiles.
    """
    if mapping_type not in (0, 1, 2, 3):
        raise ValueError(f"unsupported NCER OBJ mapping type: {mapping_type:#x}")
    units = tile_name << mapping_type
    return units if pixel_format == 3 else units >> 1


def decode_tile(raw: bytes, tile_index: int, pixel_format: int) -> list[int]:
    bytes_per_tile = 32 if pixel_format == 3 else 64
    start = tile_index * bytes_per_tile
    tile = raw[start : start + bytes_per_tile]
    if len(tile) != bytes_per_tile:
        raise ValueError(f"NCER references tile {tile_index} outside NCGR")
    if pixel_format == 4:
        return list(tile)
    pixels = []
    for packed in tile:
        pixels.extend((packed & 15, packed >> 4))
    return pixels


def cells_are_separable(graphic: dict, layout: dict) -> bool:
    """Report whether a cell's picture can be split back into single tiles.

    Reuse of a tile is fine as long as every copy is drawn the same way and
    nothing is painted over it. Overlapping objects lose the pixels underneath,
    and a mirrored copy disagrees with the upright one about what to store, so
    those assets are edited as a plain tile sheet instead.
    """
    for cell in layout["cells"]:
        visible = [obj for obj in cell["objects"] if not obj["hidden"]]
        orientation: dict[int, tuple[bool, bool]] = {}
        for obj in visible:
            base = object_tile_index(obj["tile_name"], layout["mapping_type"], graphic["pixel_format"])
            span = (obj["width"] // 8) * (obj["height"] // 8)
            flips = (obj["flip_x"], obj["flip_y"])
            for tile_index in range(base, base + span):
                if orientation.get(tile_index, flips) != flips:
                    return False
                orientation[tile_index] = flips
        for index, first in enumerate(visible):
            for second in visible[index + 1 :]:
                if (
                    first["x"] < second["x"] + second["width"]
                    and second["x"] < first["x"] + first["width"]
                    and first["y"] < second["y"] + second["height"]
                    and second["y"] < first["y"] + first["height"]
                ):
                    return False
    return True


def render_ncer_cells(ncgr_path: Path, palette_path: Path, ncer_path: Path) -> tuple[list[Image.Image], list[dict], dict]:
    graphic = parse_ncgr(ncgr_path)
    layout = parse_ncer(ncer_path)
    palettes = nclr_palettes(palette_path)
    mapping_type = layout["mapping_type"]
    pixel_format = graphic["pixel_format"]
    images = []
    cell_metadata = []
    for cell in layout["cells"]:
        visible = [obj for obj in cell["objects"] if not obj["hidden"]]
        if visible:
            min_x = min(obj["x"] for obj in visible)
            min_y = min(obj["y"] for obj in visible)
            max_x = max(obj["x"] + obj["width"] for obj in visible)
            max_y = max(obj["y"] + obj["height"] for obj in visible)
        else:
            min_x = min_y = 0
            max_x = max_y = 1
        image = Image.new("RGBA", (max_x - min_x, max_y - min_y), (0, 0, 0, 0))
        # Lower OAM indices have display precedence, so draw them last.
        for obj in reversed(visible):
            object_image = Image.new("RGBA", (obj["width"], obj["height"]), (0, 0, 0, 0))
            object_pixels = object_image.load()
            base_tile = object_tile_index(obj["tile_name"], mapping_type, pixel_format)
            width_tiles = obj["width"] // 8
            height_tiles = obj["height"] // 8
            palette_bank = 0 if graphic["pixel_format"] == 4 else obj["palette_bank"]
            if palette_bank >= len(palettes):
                raise ValueError(f"NCER palette bank outside NCLR: {ncer_path}")
            palette = palettes[palette_bank]
            if graphic["pixel_format"] == 4:
                palette = [color for bank in palettes for color in bank]
            for tile_y in range(height_tiles):
                for tile_x in range(width_tiles):
                    tile = decode_tile(
                        graphic["raw"], base_tile + tile_y * width_tiles + tile_x, graphic["pixel_format"]
                    )
                    for y in range(8):
                        for x in range(8):
                            color_index = tile[y * 8 + x]
                            color = palette[color_index]
                            object_pixels[tile_x * 8 + x, tile_y * 8 + y] = (*color, 0 if color_index == 0 else 255)
            if obj["flip_x"]:
                object_image = object_image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            if obj["flip_y"]:
                object_image = object_image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            image.alpha_composite(object_image, (obj["x"] - min_x, obj["y"] - min_y))
        images.append(image)
        cell_metadata.append({
            "index": cell["index"],
            "origin_x": min_x,
            "origin_y": min_y,
            "width": image.width,
            "height": image.height,
            "object_count": len(visible),
            "empty": not visible,
        })
    return images, cell_metadata, {
        "decoder": f"ncgr-{4 if graphic['pixel_format'] == 3 else 8}bpp-ncer-cells",
        "palette_bank_count": len(palettes),
        "tile_count": graphic["tile_count"],
        "cell_count": len(images),
        "mapping_type": layout["mapping_type"],
        "mapping_type": mapping_type,
        "note": "Cell PNGs are the editable files; the .cells.png file is a read-only contact sheet.",
    }


def contact_sheet(images: list[Image.Image], metadata: list[dict]) -> Image.Image:
    shown = [(image, meta) for image, meta in zip(images, metadata) if not meta["empty"]]
    if not shown:
        return Image.new("RGB", (32, 32), (48, 49, 52))
    scale = 4 if max(image.width for image, _ in shown) <= 32 else 2
    scaled = [(image.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST), meta)
              for image, meta in shown]
    cell_width = max(image.width for image, _ in scaled) + 16
    cell_height = max(image.height for image, _ in scaled) + 28
    columns = min(4, max(1, math.ceil(math.sqrt(len(shown)))))
    rows = math.ceil(len(shown) / columns)
    sheet = Image.new("RGB", (cell_width * columns, cell_height * rows), (48, 49, 52))
    for position, (image, meta) in enumerate(scaled):
        x = (position % columns) * cell_width + 8
        y = (position // columns) * cell_height + 20
        checker = Image.new("RGB", image.size, (35, 35, 35))
        checker_pixels = checker.load()
        for py in range(image.height):
            for px in range(image.width):
                if ((px // 8) + (py // 8)) & 1:
                    checker_pixels[px, py] = (65, 65, 65)
        checker.paste(image, mask=image.getchannel("A"))
        sheet.paste(checker, (x, y))
        # Tiny built-in text is only a frame identifier; the sprite remains pixel-perfect.
        from PIL import ImageDraw
        ImageDraw.Draw(sheet).text((x, y - 14), f"cell-{meta['index']:03d}", fill=(238, 238, 238))
    return sheet


def render_tile_sheet(path: Path) -> tuple[Image.Image, dict]:
    """Render the game's CLUT+CHAR container, which carries no tile map."""
    parsed = chunks(path.read_bytes())
    palette_raw = parsed[b"CLUT"][1]
    character_raw = parsed[b"CHAR"][1]
    palette = [rgb555(value) for value in struct.unpack(f"<{len(palette_raw) // 2}H", palette_raw)]
    bytes_per_tile = 32 if len(palette) <= 16 else 64
    tile_count = len(character_raw) // bytes_per_tile
    width_tiles = math.ceil(math.sqrt(tile_count))
    height_tiles = math.ceil(tile_count / width_tiles)
    image = paletted_image(width_tiles * 8, height_tiles * 8, palette)
    pixels = image.load()
    for tile_index in range(tile_count):
        tile = character_raw[tile_index * bytes_per_tile : (tile_index + 1) * bytes_per_tile]
        origin_x = (tile_index % width_tiles) * 8
        origin_y = (tile_index // width_tiles) * 8
        for y in range(8):
            if bytes_per_tile == 32:
                for pair in range(4):
                    packed = tile[y * 4 + pair]
                    pixels[origin_x + pair * 2, origin_y + y] = packed & 15
                    pixels[origin_x + pair * 2 + 1, origin_y + y] = packed >> 4
            else:
                for x in range(8):
                    pixels[origin_x + x, origin_y + y] = tile[y * 8 + x]
    return image, {
        "decoder": f"clut-char-{4 if bytes_per_tile == 32 else 8}bpp-tile-sheet",
        "width": image.width,
        "height": image.height,
        "palette_color_count": len(palette),
        "tile_count": tile_count,
        "note": "The container has no tile map; tiles are laid out in storage order.",
    }


def render_ncgr(path: Path, palette_path: Path) -> tuple[Image.Image, dict]:
    graphic = parse_ncgr(path)
    pixel_format = graphic["pixel_format"]
    raw = graphic["raw"]
    palettes = nclr_palettes(palette_path)
    if pixel_format == 3:
        bytes_per_tile = 32
        palette = palettes[0]
    elif pixel_format == 4:
        bytes_per_tile = 64
        palette = [color for bank in palettes for color in bank]
    else:
        raise ValueError(f"unsupported NCGR pixel format {pixel_format}: {path}")
    tile_count = graphic["tile_count"]
    width_tiles = math.ceil(math.sqrt(tile_count))
    height_tiles = math.ceil(tile_count / width_tiles)
    image = paletted_image(width_tiles * 8, height_tiles * 8, palette)
    pixels = image.load()
    for tile_index in range(tile_count):
        tile = raw[tile_index * bytes_per_tile : tile_index * bytes_per_tile + bytes_per_tile]
        origin_x = (tile_index % width_tiles) * 8
        origin_y = (tile_index // width_tiles) * 8
        for y in range(8):
            if pixel_format == 3:
                for pair in range(4):
                    packed = tile[y * 4 + pair]
                    pixels[origin_x + pair * 2, origin_y + y] = packed & 15
                    pixels[origin_x + pair * 2 + 1, origin_y + y] = packed >> 4
            else:
                for x in range(8):
                    pixels[origin_x + x, origin_y + y] = tile[y * 8 + x]
    return image, {
        "decoder": f"ncgr-{4 if pixel_format == 3 else 8}bpp-tile-sheet",
        "width": image.width,
        "height": image.height,
        "palette_bank_count": len(palettes),
        "rendered_palette_bank": 0,
        "tile_count": tile_count,
        "note": "NCER sprite composition is not applied; this is an editable tile sheet.",
    }


def main() -> None:
    args = parse_args()
    index = json.loads(args.index.read_text(encoding="utf-8"))
    source_root = args.source_root.resolve()
    output_root = args.output_root.resolve()
    rendered = []
    skipped = []
    for record in index["records"]:
        source = (source_root / PurePosixPath(record["path"])).resolve()
        relative = PurePosixPath(record["path"])
        if record["asset_kind"] == "game_specific_binary_image":
            image, metadata = render_custom(source)
            output_relative = relative.with_suffix(".png")
        elif record["asset_kind"] == "game_specific_tile_sheet":
            image, metadata = render_tile_sheet(source)
            output_relative = relative.with_suffix(".tiles.png")
        elif record["asset_kind"] == "nintendo_ds_ncgr_image":
            palette = source.with_suffix(".NCLR")
            ncer = source.with_suffix(".NCER")
            separable = ncer.exists() and cells_are_separable(parse_ncgr(source), parse_ncer(ncer))
            if ncer.exists() and not separable:
                # Overlapping or mirrored objects can hide part of a tile, so an
                # edit there may not survive the trip back. Ship the tile sheet
                # as well, which always round-trips.
                sheet, sheet_metadata = render_ncgr(source, palette)
                sheet_path = (output_root / relative.with_suffix(".tiles.png")).resolve()
                sheet_path.parent.mkdir(parents=True, exist_ok=True)
                sheet.save(sheet_path, format="PNG", optimize=False)
            if ncer.exists():
                cell_images, cells, metadata = render_ncer_cells(source, palette, ncer)
                cell_dir_relative = relative.with_suffix(".cells")
                cell_paths = []
                for cell_image, cell in zip(cell_images, cells):
                    cell_relative = cell_dir_relative / f"cell-{cell['index']:03d}.png"
                    cell_output = (output_root / cell_relative).resolve()
                    cell_output.parent.mkdir(parents=True, exist_ok=True)
                    cell_image.save(cell_output, format="PNG", optimize=False)
                    cell_paths.append(cell_relative.as_posix())
                image = contact_sheet(cell_images, cells)
                output_relative = relative.with_suffix(".cells.png")
                metadata.update({"cells": cells, "cell_png_paths": cell_paths})
            else:
                image, metadata = render_ncgr(source, palette)
                output_relative = relative.with_suffix(".tiles.png")
        else:
            skipped.append({"id": record["id"], "reason": "container_requires_internal_extraction"})
            continue
        output = (output_root / output_relative).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, format="PNG", optimize=False)
        rendered.append({
            "id": record["id"],
            "source_path": record["path"],
            "png_path": output_relative.as_posix(),
            **metadata,
        })
    manifest = {
        "schema_version": 1,
        "rendered_count": len(rendered),
        "skipped_count": len(skipped),
        "records": rendered,
        "skipped": skipped,
    }
    manifest_path = output_root / "preview-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cards = []
    for record in rendered:
        path = html.escape(record["png_path"])
        label = html.escape(record["source_path"])
        cards.append(
            f'<figure><a href="{path}"><img src="{path}" alt="{label}"></a>'
            f'<figcaption>{label}<br>{record.get("width", "합성")}×{record.get("height", "셀")}</figcaption></figure>'
        )
    gallery = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>드라마틱 던전 이미지 작업본</title>
<style>
body{font-family:Malgun Gothic,sans-serif;background:#202124;color:#eee;margin:24px}
main{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:18px}
figure{margin:0;padding:12px;background:#303134;border-radius:8px;overflow:auto}
img{image-rendering:pixelated;min-width:min(100%,224px);max-width:none;height:auto;background:#111}
figcaption{margin-top:8px;font-size:13px;word-break:break-all}
a{color:inherit}
</style></head><body><h1>이미지 편집용 PNG</h1>
<p>이미지를 클릭하면 원본 크기 PNG가 열린다. 일반 PNG는 P(인덱스 팔레트) 모드를 유지한다.
NCGR 연락 시트는 확인용이며, 실제 편집은 같은 이름의 .cells 폴더 안 RGBA 셀 PNG에서 한다.</p>
<main>""" + "\n".join(cards) + "</main></body></html>\n"
    (output_root / "index.html").write_text(gallery, encoding="utf-8")
    print(json.dumps({"output_root": str(output_root), **manifest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
