#!/usr/bin/env python3
"""Assemble the SHNGROOT userdata-root image on Linux (no macOS hdiutil).

Same on-disk contract as the base driver's userdata step (scripts/unixnu/
userdata.py): a 4096-byte SHNGROOT manifest, 1 MiB-aligned windows with the
raw bytes of the IPSW OS and SystemOS disk images (here produced by dmg2img),
and the manifest SHA-256 trailer. Nothing is flashed; the report prints the
fastboot command.

  make-userdata-linux.py --os OS.img --system-os SystemOS.img --out userdata-root.img
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

MIB = 1 << 20
CHUNK = 8 * MIB


def copy_window(source: Path, target, offset: int, size: int):
    digest = hashlib.sha256()
    target.seek(offset)
    done = 0
    with source.open('rb', buffering=0) as handle:
        while done < size:
            want = min(CHUNK, size - done)
            data = handle.read(want)
            if len(data) != want:
                raise SystemExit(f'short read from {source} at {done:#x}')
            digest.update(data)
            target.write(data)
            done += want
            if done % (256 * MIB) < CHUNK:
                print(f'  {done >> 20} / {size >> 20} MiB', flush=True)
    return digest.digest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--os', type=Path, required=True, help='raw OS disk image from dmg2img')
    parser.add_argument('--system-os', type=Path, required=True, help='raw SystemOS disk image from dmg2img')
    parser.add_argument('--out', type=Path, default=Path('userdata-root.img'))
    args = parser.parse_args()

    sources = [('OS', args.os.resolve()), ('SystemOS', args.system_os.resolve())]
    layout, cursor = [], MIB
    for name, path in sources:
        size = path.stat().st_size
        layout.append((name, path, cursor, size))
        cursor += (size + MIB - 1) // MIB * MIB
    total = cursor
    print(f'userdata-root image: {total} bytes ({total / 2**30:.2f} GiB)')

    entries = []
    with args.out.open('wb') as target:
        target.truncate(total)
        for name, path, offset, size in layout:
            print(f'{name}: {size} bytes at {offset:#x} from {path.name}', flush=True)
            digest = copy_window(path, target, offset, size)
            entries.append((name, offset, size, digest))
            print(f'  sha256 {digest.hex()}', flush=True)
        header = bytearray(4096)
        header[0:8] = b'SHNGROOT'
        struct.pack_into('<IIQ', header, 8, 1, len(entries), total)
        for index, (name, offset, size, digest) in enumerate(entries):
            struct.pack_into('<32sQQII32s40s', header, 24 + 128 * index,
                             name.encode(), offset, size, 512, 1, digest, bytes(40))
        header[4064:4096] = hashlib.sha256(bytes(header[:4064])).digest()
        target.seek(0)
        target.write(header)

    image_sha = hashlib.sha256()
    with args.out.open('rb', buffering=0) as handle:
        while True:
            data = handle.read(CHUNK)
            if not data:
                break
            image_sha.update(data)
    report = {'image_bytes': total, 'image_sha256': image_sha.hexdigest(),
              'manifest_sha256': hashlib.sha256(bytes(header)).hexdigest(),
              'windows': [{'name': n, 'offset': o, 'size': s, 'sha256': d.hex()} for n, o, s, d in entries],
              'flash_command': f'fastboot flash userdata {args.out}',
              'hardware_tested': False, 'assembled_on': 'linux/dmg2img'}
    args.out.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'done: {args.out}')
    print(f'flash it with: {report["flash_command"]}')


if __name__ == '__main__':
    main()
