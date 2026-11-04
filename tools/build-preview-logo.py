"""Rebuild the finished preview logo texture from the Korean attract atlas."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from cpk_raw_textures import archive_resources, compose_screen_regions, digest, render_texture


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
    record = next(r for r in plan['records'] if r['name'] == 'preview-complete-logo')
    spec = importlib.util.spec_from_file_location('logo_decompressor',
        Path(__file__).with_name('decompress-cpk-entries.py'))
    decoder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(decoder)
    # Every target holds the same texture and final palette, so one render
    # serves them all; checking each keeps a drifted copy from slipping in.
    rendered = None
    for target in record['targets']:
        slot = source[target['slot_offset']:target['slot_offset'] + target['slot_size']]
        if digest(slot) != target['source_slot_sha256']:
            raise ValueError(f"original preview archive changed: {target['cpk_entry_id']}")
        if target['compressed']:
            slot = decoder.decompress(slot)
        resources = archive_resources(slot)
        start, size = resources[target['resource_index']]
        palette_start, palette_size = resources[target['palette_index']]
        raw, palette = slot[start:start + size], slot[palette_start:palette_start + palette_size]
        if digest(raw) != target['source_sha256'] or digest(palette) != target['palette_sha256']:
            raise ValueError(f"preview logo precondition mismatch: {target['cpk_entry_id']}")
        if rendered is None:
            rendered = render_texture(raw, palette, record['width'], record['height'], record['bpp'])
    output = args.edits_root / record['png']
    compose_screen_regions(rendered, record, args.edits_root).save(output)
    print(json.dumps({'output': str(output), 'targets': len(record['targets']),
                      'sha256': digest(output.read_bytes())}))


if __name__ == '__main__':
    main()
