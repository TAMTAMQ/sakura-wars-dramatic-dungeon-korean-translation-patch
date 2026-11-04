"""Assemble upper-screen preview subtitles and extend the ARM9 autoload table.

ARM9 stays at its original card offset. Later ROM extents move together; every
FAT payload is checked byte for byte after relocation. The payload lives in a
64 KiB reservation made through the SDK's Main arena high boundary.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
import subprocess
from pathlib import Path

import capstone
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
LOAD = 0x023D0000
BASE = 0x02000000
HOOK = 0x0200B660
ARENA = 0x0211ECEC


def sha(data):
    return hashlib.sha256(data).hexdigest()


def crc16(data):
    crc = 0xFFFF
    for v in data:
        crc ^= v
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return crc


def assemble(armips, source, folder, sym='symbols.txt'):
    subprocess.run([str(armips), '-erroronwarning', '-sym2', sym, str(source)],
                   cwd=folder, check=True, capture_output=True, text=True)


def verify_machine(armips, folder, payload, code_end, hook):
    """Decode every instruction, reassemble its decoded form and compare bytes."""
    groups = [(LOAD, payload[:4], capstone.CS_MODE_THUMB),
              (LOAD+4, payload[4:code_end-LOAD], capstone.CS_MODE_ARM),
              (HOOK, hook, capstone.CS_MODE_THUMB)]
    reports = []
    for k, (address, data, mode) in enumerate(groups):
        cs = capstone.Cs(capstone.CS_ARCH_ARM, mode)
        decoded = list(cs.disasm(data, address))
        if sum(i.size for i in decoded) != len(data):
            raise ValueError('payload contains undecodable machine instructions')
        lines = []
        for ins in decoded:
            op = ins.op_str.replace('#', '')
            for alias, register in (('sb','r9'), ('sl','r10'), ('fp','r11'), ('ip','r12')):
                op = re.sub(r'\b'+alias+r'\b', register, op)
            # Capstone's branch syntax is a bare absolute address; armips accepts it.
            lines.append(f'    {ins.mnemonic} {op}')
        name = f'roundtrip-{k}.bin'
        asm = folder / f'roundtrip-{k}.asm'
        asm.write_text('.nds\n.create "'+name+'", '+hex(address)+'\n'+
                       ('.thumb\n' if mode == capstone.CS_MODE_THUMB else '.arm\n')+
                       '\n'.join(lines)+'\n.close\n', encoding='utf-8')
        assemble(armips, asm, folder, f'roundtrip-{k}.sym')
        if (folder/name).read_bytes() != data:
            raise ValueError(f'ARMv5TE disassembly/reassembly mismatch: {k}')
        reports.append({'address': hex(address), 'bytes': len(data),
                        'instructions': len(decoded), 'roundtrip': True})
    return reports


def make_panels(spec, folder):
    if spec['screen'] != 'top' or spec['panel_y'] != 144 or spec['panel_bpp'] != 2:
        raise ValueError('assembly supports upper-screen lower-center 2bpp panels only')
    if sha((ROOT/spec['font']).read_bytes()) != spec['font_sha256']:
        raise ValueError('subtitle font identity mismatch')
    font = ImageFont.truetype(str(ROOT/spec['font']), spec['font_size'])
    # RGB555 palette, converted through the emulator's RGB565 output for QA.
    colors555 = [(0,0,0), (9,9,8), (20,19,16), (31,30,26)]
    colors = [(r*255//31, ((g<<1)|(g>>4))*255//63, b*255//31) for r,g,b in colors555]
    panels, cues = bytearray(), bytearray()
    previews, index = [], 0
    voice_ids = set()
    for preview in spec['previews']:
        voice = preview['voice_index']
        if voice in voice_ids or not 330 <= voice <= 333 or preview['adx_cpk_id'] != voice+1:
            raise ValueError('unknown or duplicate preview voice')
        voice_ids.add(voice)
        last = 0
        for cue in preview['cues']:
            start, end = cue['start_ms'], cue['end_ms']
            if not last <= start < end <= 27000:
                raise ValueError('overlapping or out-of-range preview cue')
            last = end
            im = Image.new('L', (256, 32))
            draw = ImageDraw.Draw(im)
            lines = cue['korean'].split('\n')
            if not 1 <= len(lines) <= 2:
                raise ValueError('subtitle must contain one or two lines')
            boxes = [font.getbbox(line) for line in lines]
            heights = [box[3]-box[1] for box in boxes]
            group_height = sum(heights)+2*(len(lines)-1)
            if group_height > 32:
                raise ValueError('subtitle line group does not fit')
            y = (32-group_height)//2
            for line,box,height in zip(lines,boxes,heights):
                width = box[2]-box[0]
                if width > 248:
                    raise ValueError(f'subtitle does not fit: {line}')
                draw.text(((256-width)//2-box[0], y-box[1]), line, font=font, fill=255)
                y += height+2
            quantized = im.point(lambda v: (v*3+127)//255)
            preview_im = Image.new('RGB', im.size)
            preview_im.putdata([colors[v] for v in quantized.get_flattened_data()])
            preview_im.save(folder/f'panel-{index:02}.png')
            # DS tile order, four LSB-first 2-bit pixels per byte. Four tone AA.
            for ty in range(4):
                for tx in range(32):
                    for y in range(8):
                        for half in (0,4):
                            panels.append(sum(quantized.getpixel((tx*8+half+x, ty*8+y)) << (x*2) for x in range(4)))
            cues += struct.pack('<4I', voice, start, end, index)
            previews.append((preview['id'], index, cue))
            index += 1
    if voice_ids != {330, 331, 332, 333}:
        raise ValueError('all four previews are required')
    (folder/'panels.bin').write_bytes(panels)
    (folder/'cues.bin').write_bytes(cues)
    sheet = Image.new('RGB', (512, math.ceil(index/2)*56), '#303030')
    sd = ImageDraw.Draw(sheet)
    for ident, idx, cue in previews:
        x, y = idx%2*256, idx//2*56
        sd.text((x+3, y+2), f'{ident} {cue["start_ms"]/1000:.2f}s', fill='white')
        sheet.paste(Image.open(folder/f'panel-{idx:02}.png').convert('RGB'), (x, y+22))
    sheet.save(folder/'subtitle-sheet.png')
    return index


def build(source, rom, spec_path, armips, folder):
    original, before = source.read_bytes(), rom.read_bytes()
    if sha(original) != 'e6cafe64437080bf5474fa7f9cfec6568b2af7626b65c3ab0d27d30308cec7c4':
        raise ValueError('unsupported source ROM')
    if sha(armips.read_bytes()) != '02c589ae509a7706bf73d0507949ff722288f1d2deea67a13f33c741bc8696ba':
        raise ValueError('expected official armips v0.11.0 Windows x86 binary')
    folder.mkdir(parents=True, exist_ok=True)
    spec = json.loads(spec_path.read_text(encoding='utf-8'))
    count = make_panels(spec, folder)
    asm = ROOT/'assets/translation/preview-subtitles.asm'
    try:
        assemble(armips, asm, folder)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(e.stdout+e.stderr) from e
    symbols = {}
    for line in (folder/'symbols.txt').read_text().splitlines():
        match = re.match(r'^([0-9A-Fa-f]{8})\s+(\S+)', line)
        if match:
            symbols[match[2]] = int(match[1], 16)
    payload = (folder/'payload.bin').read_bytes()
    hook = (folder/'hook.bin').read_bytes()
    arena = (folder/'arena.bin').read_bytes()
    if not payload or len(payload) > 0x10000:
        raise ValueError('subtitle payload exceeds Main arena reservation')
    machine = verify_machine(armips, folder, payload, symbols['code_end'], hook)
    off, _, base, size = struct.unpack_from('<4I', before, 0x20)
    src_off, _, src_base, src_size = struct.unpack_from('<4I', original, 0x20)
    if (off, base) != (src_off, src_base) or size not in (src_size, src_size+12):
        raise ValueError('input ARM9 geometry changed or subtitles already applied')
    arm9 = bytearray(before[off:off+size])
    original_arm9 = original[src_off:src_off+src_size]
    writes = []
    for name, address, expected, replacement in (
        ('audio-update-hook', HOOK, bytes.fromhex('fbf058ef'), hook),
        ('main-arena-reservation', ARENA, struct.pack('<I', 0x023E0000), arena)):
        at = address-base
        if original_arm9[at:at+len(expected)] != expected or arm9[at:at+len(expected)] != expected:
            raise ValueError(f'Expected Write mismatch: {name}')
        arm9[at:at+len(replacement)] = replacement
        writes.append({'name': name, 'address': hex(address), 'expected': expected.hex(), 'replacement': replacement.hex()})
    magic = arm9.find(bytes.fromhex('2106c0dede c00621'.replace(' ', '')))
    module = magic-28
    params = struct.unpack_from('<7I', arm9, module)
    if params[:5] != (0x021C12E0, 0x021C12F8, 0x021BFB60, 0x021BFB60, 0x0222C380):
        raise ValueError('unknown ARM9 autoload/BSS layout')
    table_start, table_end, data_start = params[:3]
    old_table = bytes(arm9[table_start-base:table_end-base])
    if len(old_table) != 24 or sum(struct.unpack_from('<I', old_table, k+4)[0] for k in (0,12)) != table_start-data_start:
        raise ValueError('autoload source does not end at the original table')
    table_offset = table_start-base+len(payload)
    if table_offset%4:
        raise ValueError('unaligned payload')
    footer = bytes(arm9[table_end-base:])
    arm9[table_start-base:] = payload+old_table+struct.pack('<3I', LOAD, len(payload), 0)+footer
    struct.pack_into('<II', arm9, module, base+table_offset, base+table_offset+36)
    # Make room after ARM9 without moving the secure-area/card boot location.
    delta = (len(arm9)-size+511)//512*512
    boundary = off+size
    after = bytearray(before[:boundary]+bytes(delta)+before[boundary:])
    after[off:off+len(arm9)] = arm9
    struct.pack_into('<I', after, 0x2C, len(arm9))
    changed_header = []
    for at in (0x30, 0x40, 0x48, 0x50, 0x58, 0x68):
        old, = struct.unpack_from('<I', before, at)
        if old >= boundary:
            struct.pack_into('<I', after, at, old+delta)
            changed_header.append(hex(at))
    fat, fat_size = struct.unpack_from('<II', before, 0x48)
    new_fat, = struct.unpack_from('<I', after, 0x48)
    for at in range(0, fat_size, 8):
        first, last = struct.unpack_from('<II', before, fat+at)
        if first < boundary <= last or first < last < boundary:
            raise ValueError('unexpected FAT extent before or crossing ARM9 boundary')
        struct.pack_into('<II', after, new_fat+at, first+delta, last+delta)
        if before[first:last] != after[first+delta:last+delta]:
            raise ValueError(f'FAT file changed during relocation: {at//8}')
    struct.pack_into('<I', after, 0x80, len(after))
    if len(after) > (128*1024 << after[0x14]):
        after[0x14] = math.ceil(math.log2(len(after)/(128*1024)))
    struct.pack_into('<H', after, 0x15E, crc16(after[:0x15E]))
    # Compare every byte outside ARM9, FAT and header after undoing the shift.
    normalized = bytearray(after[:boundary]+after[boundary+delta:])
    normalized[:0x200] = before[:0x200]
    normalized[off:boundary] = before[off:boundary]
    normalized[fat:fat+fat_size] = before[fat:fat+fat_size]
    if normalized != before:
        raise ValueError('unregistered final diff outside ARM9/header/FAT')
    return bytes(after), {
        'ok': True, 'source_sha256': sha(original), 'input_sha256': sha(before),
        'output_sha256': sha(after), 'output_size': len(after),
        'preview_count': len(spec['previews']), 'cue_count': count,
        'screen': 'top', 'panel_y': spec['panel_y'], 'isa': 'ARM946E-S ARMv5TE / Thumb-1',
        'asm_sha256': sha(asm.read_bytes()), 'spec_sha256': sha(spec_path.read_bytes()),
        'font': spec['font'], 'font_sha256': sha((ROOT/spec['font']).read_bytes()),
        'assembler_sha256': sha(armips.read_bytes()),
        'payload_address': hex(LOAD), 'payload_bytes': len(payload),
        'arena_reserved_bytes': 0x10000, 'arm9_bytes': len(arm9),
        'extent_shift_bytes': delta, 'fat_files_preserved': fat_size//8,
        'expected_writes': writes, 'machine_roundtrip': machine,
        'unregistered_diff_check': True, 'symbols': {k:hex(v) for k,v in symbols.items()},
        'runtime_verified': False,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--rom', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--armips', type=Path, required=True)
    p.add_argument('--work-root', type=Path, required=True)
    p.add_argument('--spec', type=Path, default=ROOT/'assets/translation/preview-subtitles.json')
    a = p.parse_args()
    out, report = build(a.source.resolve(), a.rom.resolve(), a.spec.resolve(), a.armips.resolve(), a.work_root.resolve())
    a.output.parent.mkdir(parents=True, exist_ok=True)
    stage = a.output.with_suffix('.nds.tmp')
    stage.write_bytes(out)
    report_stage = a.report.with_suffix('.json.tmp')
    report_stage.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    stage.replace(a.output)
    report_stage.replace(a.report)
    print(json.dumps({k:report[k] for k in ('ok','preview_count','cue_count','payload_bytes','output_sha256')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
