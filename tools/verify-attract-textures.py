"""Read translated preview textures back from a ROM; enforce the v0.2 boundary."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import struct

from cpk_raw_textures import apply_raw_edit, archive_resources, digest, render_texture

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('decompress_cpk_entries',
                                             Path(__file__).with_name('decompress-cpk-entries.py'))
codec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(codec)


def rom_cpk(rom: bytes, file_id: int) -> tuple[int, int, bytes]:
    fat = struct.unpack_from('<I', rom, 0x48)[0]
    start, end = struct.unpack_from('<II', rom, fat + file_id * 8)
    return start, end, rom[start:end]


def verify(rom_path: Path, baseline_path: Path, output: Path) -> dict:
    manifest = json.loads((ROOT / 'work/cpk-extracted/manifest.json').read_text(encoding='utf-8'))
    visual = json.loads((ROOT / 'assets/visual/index.json').read_text(encoding='utf-8'))
    cpk_row = next(r for r in visual['records'] if r['path'] == 'cpk/faCpkData.cpk')
    plan = json.loads((ROOT / 'assets/visual/cpk-raw-texture-translation.json').read_text(encoding='utf-8'))
    rom, baseline = rom_path.read_bytes(), baseline_path.read_bytes()
    if digest(baseline) != '0222f53da7e02645ac71626ecd9140675a0f579c2faaafbb6c5dd9c5a7f3c7c4':
        raise ValueError('baseline must be the published v0.2 ROM')
    start, end, cpk = rom_cpk(rom, cpk_row['file_id'])
    old_start, old_end, old_cpk = rom_cpk(baseline, cpk_row['file_id'])
    assert (start, end, len(rom)) == (old_start, old_end, len(baseline))
    assert rom[:start] == baseline[:start] and rom[end:] == baseline[end:]
    assert digest(cpk) == cpk_row['replacement_sha256']
    source_cpk = (ROOT / 'faCpkData.cpk').read_bytes()
    assert digest(source_cpk) == manifest['cpk_sha256'] == plan['source_cpk_sha256']
    permitted = {}
    for record in plan['records']:
        for target in record['targets']:
            permitted.setdefault(target['cpk_entry_id'], []).append((record, target))
    changed, checked, protected = [], [], 0
    output.mkdir(parents=True, exist_ok=True)
    for slot in manifest['records']:
        offset, size = slot['offset'], slot['file_size']
        old_raw, new_raw = old_cpk[offset:offset + size], cpk[offset:offset + size]
        if old_raw == new_raw:
            continue
        entry_id = slot['id']
        assert entry_id in permitted or entry_id == 1538, entry_id
        changed.append(entry_id)
        unpack = codec.decompress if slot['compressed'] else lambda b: b
        old, new = unpack(old_raw), unpack(new_raw)
        assert len(old) == len(new), entry_id
        original = unpack(source_cpk[offset:offset + size])
        allowed = bytearray(len(new))
        if entry_id == 1538:
            # The old combined pass omitted the already translated opening logo.
            allowed[96:96 + 51232] = b'\x01' * 51232
        for record, target in permitted.get(entry_id, []):
            begin, rebuilt = apply_raw_edit(original, record, target,
                ROOT / 'assets/visual/edits/cpk/text-images' / record['png'])
            assert new[begin:begin + len(rebuilt)] == rebuilt, (entry_id, record['name'])
            allowed[begin:begin + len(rebuilt)] = b'\x01' * len(rebuilt)
            resources = archive_resources(new)
            po, ps = resources[target['palette_index']]
            original_resources = archive_resources(original)
            assert resources == original_resources, entry_id
            assert new[po:po + ps] == original[po:po + ps], entry_id
            image = render_texture(rebuilt, new[po:po + ps], record['width'], record['height'], record['bpp'])
            image.save(output / f'{entry_id}-{record["name"]}.png')
            checked.append({'entry': entry_id, 'name': record['name'], 'width': image.width,
                            'height': image.height, 'texture_sha256': digest(rebuilt)})
        for i, (a, b) in enumerate(zip(old, new, strict=True)):
            if not allowed[i]:
                assert a == b, (entry_id, hex(i), 'unregistered payload change')
                protected += 1
    assert sorted(changed) == [1538, 1539, 1540, 1542, 1544]
    assert len(checked) == 5
    return {'ok': True, 'rom_sha256': digest(rom), 'baseline_sha256': digest(baseline),
            'changed_cpk_entries': changed, 'raw_textures_readback': checked,
            'outside_cpk_byte_identical_to_v02': True,
            'other_cpk_slots_byte_identical_to_v02': True,
            'protected_payload_bytes_verified': protected,
            'archive_extents_and_palettes_preserved': True,
            'texture_part_boundaries_enforced': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--rom', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.rom, args.baseline, args.output)
    (args.output / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
