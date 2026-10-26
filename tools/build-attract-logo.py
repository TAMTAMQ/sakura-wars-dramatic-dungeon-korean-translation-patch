"""Cut complete master layers by original GPU screen/UV coordinates."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from cpk_raw_textures import archive_resources, compose_texture_parts, digest, render_texture


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cpk', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--edits-root', type=Path, required=True)
    args = parser.parse_args()
    source = args.cpk.read_bytes()
    plan = json.loads(args.plan.read_text(encoding='utf-8'))
    if digest(source) != plan['source_cpk_sha256']:
        raise ValueError('unsupported source CPK')
    record = next(r for r in plan['records'] if r['name'] == 'attract-logo')
    target = record['targets'][0]
    slot = source[target['slot_offset']:target['slot_offset'] + target['slot_size']]
    if digest(slot) != target['source_slot_sha256']:
        raise ValueError('original preview archive changed')
    if target['compressed']:
        spec = importlib.util.spec_from_file_location('logo_decompressor',
            Path(__file__).with_name('decompress-cpk-entries.py'))
        decoder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(decoder)
        slot = decoder.decompress(slot)
    resources = archive_resources(slot)
    start, size = resources[target['resource_index']]
    palette_start, palette_size = resources[target['palette_index']]
    original = render_texture(slot[start:start+size], slot[palette_start:palette_start+palette_size],
                              record['width'], record['height'], record['bpp'])
    output = args.edits_root / record['png']
    compose_texture_parts(original, record, args.edits_root).save(output)
    print(json.dumps({'output': str(output), 'coordinate_cuts': len(record['coordinate_cuts']),
                      'sha256': digest(output.read_bytes())}))


if __name__ == '__main__':
    main()
