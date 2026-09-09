#!/usr/bin/env python3
"""Assemble the release folder: the patched ROM and the xdelta that makes it.

The patch is cut against the untouched Japanese ROM, the one README tells
players to supply. Nothing is written to the release folder unless decoding
the fresh patch back over that ROM reproduces the build byte for byte, so a
lossy or mis-sourced patch can never reach a release.

xdelta copies the file names it was handed into the VCDIFF application
header and some GUI patchers show them, so encoding runs from inside a
scratch directory where both sides carry plain release names rather than
build paths.

The ROM is written for local checking only. It is the whole commercial game,
so it must never be attached to a GitHub release; players patch their own
dump with the xdelta.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import tempfile
from pathlib import Path

import pyxdelta

ROM_NAME = "Dramatic Dungeon Sakura Taisen Kimi Arugatame (Korean).nds"
PATCH_NAME = "dramatic-dungeon-kr.xdelta"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True, help="무수정 일본판 ROM")
    parser.add_argument("--build", type=Path, required=True, help="빌드된 한글판 ROM")
    parser.add_argument("--release-root", type=Path, required=True)
    return parser.parse_args()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    args = parse_args()
    args.release_root.mkdir(parents=True, exist_ok=True)
    build_hash = digest(args.build)
    print(f"원본 ROM   {args.source.stat().st_size:>11,} bytes  {digest(args.source)}")
    print(f"한글판 ROM {args.build.stat().st_size:>11,} bytes  {build_hash}")

    with tempfile.TemporaryDirectory() as directory:
        scratch = Path(directory)
        shutil.copyfile(args.source, scratch / args.source.name)
        shutil.copyfile(args.build, scratch / ROM_NAME)

        previous = os.getcwd()
        os.chdir(scratch)
        try:
            if not pyxdelta.run(args.source.name, ROM_NAME, PATCH_NAME):
                raise SystemExit("xdelta 인코딩 실패")
            if not pyxdelta.decode(args.source.name, PATCH_NAME, "restored.nds"):
                raise SystemExit("xdelta 디코딩 실패")
            restored = Path("restored.nds")
            restored_hash = digest(restored)
            restored_size = restored.stat().st_size
        finally:
            os.chdir(previous)

        print(f"되적용     {restored_size:>11,} bytes  {restored_hash}")
        if restored_hash != build_hash:
            raise SystemExit("왕복 불일치: 배포 파일을 만들지 않음")

        shutil.copyfile(scratch / PATCH_NAME, args.release_root / PATCH_NAME)
        shutil.copyfile(args.build, args.release_root / ROM_NAME)

    patch = args.release_root / PATCH_NAME
    rom = args.release_root / ROM_NAME
    print(f"패치       {patch.stat().st_size:>11,} bytes  {digest(patch)}")
    print(f"ROM        {rom.stat().st_size:>11,} bytes  {digest(rom)}")
    print("왕복 검증 통과")


if __name__ == "__main__":
    main()
