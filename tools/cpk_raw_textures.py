"""Lossless codecs for the raw, linear textures in the CPK preview archives.

These are separate from NCGR and CLUT/CMAP/CHAR streams. Archive offsets are
stored in bits 11..31 and resource sizes in units of 32 bytes. The DS texture
palette has 16 (4bpp) or 256 (8bpp) RGB555 colours; colour zero is transparent.
"""
from __future__ import annotations

import hashlib
import importlib.util
import struct
from pathlib import Path

from PIL import Image, ImageChops


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def archive_resources(payload: bytes) -> list[tuple[int, int]]:
    if len(payload) < 8:
        raise ValueError("truncated resource archive")
    table_end = struct.unpack_from('<I', payload)[0] >> 11
    if not 8 <= table_end <= len(payload) or table_end % 32:
        raise ValueError("invalid resource archive table")
    result = []
    for at in range(0, table_end, 8):
        address, blocks = struct.unpack_from('<II', payload, at)
        if address == blocks == 0:
            if any(payload[at:table_end]):
                raise ValueError("nonzero resource table padding")
            break
        offset, size = address >> 11, blocks * 32
        if address & 0x7ff or not size or offset < table_end or offset + size > len(payload):
            raise ValueError("invalid resource extent")
        if result and offset < sum(result[-1]):
            raise ValueError("overlapping resource extents")
        result.append((offset, size))
    return result


def palette_colors(raw: bytes) -> list[tuple[int, int, int]]:
    if len(raw) not in (32, 512):
        raise ValueError("unsupported raw texture palette")
    return [((v & 31) * 255 // 31, ((v >> 5) & 31) * 255 // 31,
             ((v >> 10) & 31) * 255 // 31)
            for v in struct.unpack('<' + str(len(raw) // 2) + 'H', raw)]


def texture_indexes(raw: bytes, width: int, height: int, bpp: int) -> bytes:
    if bpp not in (4, 8) or width * height * bpp != len(raw) * 8:
        raise ValueError("raw texture dimensions do not match payload")
    if bpp == 8:
        return raw
    return bytes(value for packed in raw for value in (packed & 15, packed >> 4))


def render_texture(raw: bytes, palette: bytes, width: int, height: int, bpp: int) -> Image.Image:
    colors = palette_colors(palette)
    if len(colors) != 1 << bpp:
        raise ValueError("raw texture depth/palette mismatch")
    indexes = texture_indexes(raw, width, height, bpp)
    image = Image.new('P', (width, height))
    image.putpalette([v for rgb in colors for v in rgb])
    image.putdata(indexes)
    image.info['transparency'] = 0
    return image


def rebuild_texture(raw: bytes, palette: bytes, png: Path, width: int, height: int, bpp: int) -> bytes:
    image = Image.open(png).convert('RGBA')
    if image.size != (width, height):
        raise ValueError(f"raw texture PNG geometry mismatch: {png}")
    colors = palette_colors(palette)
    original = texture_indexes(raw, width, height, bpp)
    cache = {}
    indexes = bytearray()
    for rgba, old in zip(image.get_flattened_data(), original, strict=True):
        if rgba[3] < 128:
            value = 0
        elif old != 0 and rgba[:3] == colors[old]:
            value = old
        else:
            rgb = rgba[:3]
            if rgb not in cache:
                cache[rgb] = min(range(1, len(colors)), key=lambda i:
                                 sum((rgb[c] - colors[i][c]) ** 2 for c in range(3)))
            value = cache[rgb]
        indexes.append(value)
    if bpp == 8:
        return bytes(indexes)
    return bytes(indexes[i] | (indexes[i + 1] << 4) for i in range(0, len(indexes), 2))


def compose_texture_parts(original: Image.Image, record: dict, edits_root: Path) -> Image.Image:
    """Crop full master layers using the original GPU coordinate contract.

    UV rectangles intentionally overlap in this game. Apply the original draw
    order; do not infer character bounds, split alpha components, or resize
    individual glyphs. The small UV/screen scale difference is a property of
    the original textured quad, not a new layout choice.
    """
    image = original.convert('RGBA')
    regions = {tuple(box) for box in record['editable_regions']}
    for part in record['coordinate_cuts']:
        box = tuple(part['destination'])
        if box not in regions:
            raise ValueError(f"logo part is outside an original UV cell: {part['name']}")
        left, top, right, bottom = box
        path = edits_root / part['source']
        if digest(path.read_bytes()) != part['source_sha256']:
            raise ValueError(f"logo source layer changed without review: {part['name']}")
        layer = Image.open(path).convert('RGBA')
        if layer.size != (256,192):
            raise ValueError('logo master layer must use the fixed 256x192 screen canvas')
        crop = tuple(part['source_rectangle'])
        if not (0 <= crop[0] < crop[2] <= 256 and 0 <= crop[1] < crop[3] <= 192):
            raise ValueError('logo source rectangle is outside its fixed master canvas')
        layer = layer.crop(crop)
        if layer.size != (right-left,bottom-top):
            layer = layer.resize((right-left, bottom-top), Image.Resampling.LANCZOS)
        image.paste((0,0,0,0), box)
        image.alpha_composite(layer, (left,top))
    return image


def compose_screen_quads(atlas: Image.Image, quads: list[dict]) -> Image.Image:
    """Draw textured quads on the 256x192 screen in their recorded order."""
    canvas = Image.new('RGBA', (256, 192), (0, 0, 0, 0))
    for quad in quads:
        (u0, v0), _, (u1, v1), _ = quad['uv']
        (x0, y0), _, (x1, y1), _ = quad['screen']
        part = atlas.crop((int(u0), int(v0), int(u1), int(v1)))
        size = (round(x1 - x0), round(y1 - y0))
        if part.size != size:
            part = part.resize(size, Image.Resampling.NEAREST)
        canvas.alpha_composite(part, (round(x0), round(y0)))
    return canvas


def compose_screen_regions(original: Image.Image, record: dict, edits_root: Path) -> Image.Image:
    """Rebuild the text of a pre-assembled logo from the split attract logo.

    Some previews draw one finished logo texture instead of the attract's
    eleven quads. Its artwork is the attract composition shifted by a fixed
    screen offset (plus a halo the quads lack), so the Korean atlas is drawn
    through the same recorded quads and only the text quads' boxes are taken
    from it. Everything else, the ornament halo included, stays original.
    """
    spec = record['screen_composite']
    atlas_path = edits_root / spec['atlas']
    if digest(atlas_path.read_bytes()) != spec['atlas_sha256']:
        raise ValueError(f"screen composite atlas changed without review: {record['name']}")
    screen = compose_screen_quads(Image.open(atlas_path).convert('RGBA'), spec['quads'])
    dx, dy = spec['texture_from_screen']
    shifted = Image.new('RGBA', original.size, (0, 0, 0, 0))
    shifted.alpha_composite(screen.crop((-dx, -dy, -dx + original.width, -dy + original.height)))
    image = original.convert('RGBA')
    for box in record['editable_regions']:
        image.paste(shifted.crop(tuple(box)), tuple(box[:2]))
    return image


def apply_raw_edit(payload: bytes, record: dict, target: dict, png: Path) -> tuple[int, bytes]:
    resources = archive_resources(payload)
    offset, size = resources[target['resource_index']]
    palette_offset, palette_size = resources[target['palette_index']]
    if (offset, size) != (target['offset'], target['size']):
        raise ValueError(f"raw texture resource locator changed: {record['name']}")
    raw = payload[offset:offset + size]
    palette = payload[palette_offset:palette_offset + palette_size]
    if digest(raw) != target['source_sha256'] or digest(palette) != target['palette_sha256']:
        raise ValueError(f"raw texture source precondition mismatch: {record['name']}")
    if record.get('coordinate_cuts') or record.get('screen_composite'):
        original = render_texture(raw, palette, record['width'], record['height'], record['bpp'])
        if record.get('screen_composite'):
            expected_image = compose_screen_regions(original, record, png.parent)
        else:
            expected_image = compose_texture_parts(original, record, png.parent)
        current_image = Image.open(png).convert('RGBA')
        if current_image.size != expected_image.size or any(
            band.getbbox() is not None for band in ImageChops.difference(current_image, expected_image).split()
        ):
            raise ValueError('logo atlas does not match the original GPU coordinate cuts')
    rebuilt = rebuild_texture(raw, palette, png, record['width'], record['height'], record['bpp'])
    if len(rebuilt) != size:
        raise ValueError("raw texture changed resource size")
    original_pixels = texture_indexes(raw, record['width'], record['height'], record['bpp'])
    edited_pixels = texture_indexes(rebuilt, record['width'], record['height'], record['bpp'])
    for index, (old, new) in enumerate(zip(original_pixels, edited_pixels, strict=True)):
        if old == new:
            continue
        x, y = index % record['width'], index // record['width']
        if not any(left <= x < right and top <= y < bottom
                   for left, top, right, bottom in record['editable_regions']):
            raise ValueError(f"edit crosses an original texture-part boundary: {record['name']} ({x},{y})")
    return offset, rebuilt


MIN_INPLACE_MARGIN = 8


def _tool(filename: str):
    spec = importlib.util.spec_from_file_location(filename[:-3].replace('-', '_'),
        Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_inplace_decoding(source: bytes, replacement: bytes) -> int:
    """Every rebuilt CRILAYLA entry must decode in place without the writer
    overrunning unread input; a stream that does decodes fine on a PC and
    leaves the game on a black screen (chapter 3 title card, entry 1381)."""
    itoc = _tool('extract-cpk-itoc.py')
    decoder = _tool('decompress-cpk-entries.py')
    _header, entries = itoc.parse_cpk(replacement)
    checked = 0
    for entry in entries:
        begin, end = entry['offset'], entry['offset'] + entry['file_size']
        if not entry['compressed'] or source[begin:end] == replacement[begin:end]:
            continue
        margin = decoder.inplace_margin(replacement[begin:end])
        if margin < MIN_INPLACE_MARGIN:
            raise ValueError(f"CPK entry {entry['id']} overruns its input when decoded in place "
                             f"(margin {margin} < {MIN_INPLACE_MARGIN})")
        checked += 1
    return checked


def verify_container(source: bytes, replacement: bytes, plan: dict, edits_root: Path) -> int:
    """Reject a stale CPK even when its outer hash still matches the visual index."""
    if digest(source) != plan['source_cpk_sha256'] or len(source) != len(replacement):
        raise ValueError('raw texture plan/CPK source mismatch')
    spec = importlib.util.spec_from_file_location('raw_texture_decompressor',
        Path(__file__).with_name('decompress-cpk-entries.py'))
    decoder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(decoder)
    cache = {}
    verified = 0
    for record in plan['records']:
        if record.get('review_status') != 'complete':
            raise ValueError(f"unreviewed raw texture: {record['name']}")
        for target in record['targets']:
            entry_id = target['cpk_entry_id']
            if entry_id not in cache:
                begin, size = target['slot_offset'], target['slot_size']
                old, new = source[begin:begin + size], replacement[begin:begin + size]
                if digest(old) != target['source_slot_sha256']:
                    raise ValueError(f'raw texture CPK slot precondition mismatch: {entry_id}')
                if target['compressed']:
                    old, new = decoder.decompress(old), decoder.decompress(new)
                cache[entry_id] = old, new
            old, new = cache[entry_id]
            offset, expected = apply_raw_edit(old, record, target, edits_root / record['png'])
            if new[offset:offset + len(expected)] != expected:
                raise ValueError(f"stale/unapplied CPK raw translation: {entry_id}/{record['name']}")
            if archive_resources(new) != archive_resources(old):
                raise ValueError(f'CPK resource layout changed: {entry_id}')
            po, ps = archive_resources(old)[target['palette_index']]
            if new[po:po + ps] != old[po:po + ps]:
                raise ValueError(f'CPK raw texture palette changed: {entry_id}')
            verified += 1
    return verified
