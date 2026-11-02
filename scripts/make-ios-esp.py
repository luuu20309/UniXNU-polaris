#!/usr/bin/env python3
"""Assemble the polaris iOS-stage ESP FAT image (mtools, no mounting).

Contents: UniXNU boot manager as BOOTAA64.EFI, the DarwinBoot loader, the
prelinked kernel with its iPad-identity ADT, the trust-cache bundle and the
unixnu.toml entry. Parameters follow the sheng payload conventions (prelink
era): serial=0 (no UART), the platform memory window, cpus=1.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]

TOML = '''version = 1
[boot]
timeout = 3
[kits]
autoload = false
[[driver]]
path = "Payloads/DarwinBoot.efi"
[[entry]]
id = "ios"
title = "polaris: iPadOS 17 userland"
type = "xnu"
path = "Payloads/kernel.macho"
args = "-v serial=0 debug=0x14e kt_fixed_zones=120 cpus=1 native-ufs-root=1 -no_slto_panic"
'''


def require_trust_placeholder(data):
    """Require the unique, zeroed /chosen/memory-map/TrustCache property."""
    found = []
    def walk(offset, parent):
        count, children = struct.unpack_from('<II', data, offset)
        offset += 8
        if count > 1024 or children > 1024:
            raise ValueError('invalid ADT node counts')
        props = []
        for _ in range(count):
            key, size = struct.unpack_from('<32sI', data, offset)
            start = offset + 36
            offset = start + ((size + 3) & ~3)
            if offset > len(data):
                raise ValueError('truncated ADT property')
            props.append((key.split(b'\0', 1)[0], data[start:start + size]))
        names = [v.rstrip(b'\0').decode('ascii') for k, v in props if k == b'name']
        if len(names) != 1:
            raise ValueError('ADT node has no unique name')
        path = parent + (names[0],)
        if path[-2:] == ('chosen', 'memory-map'):
            found.extend(v for k, v in props if k == b'TrustCache')
        for _ in range(children):
            offset = walk(offset, path)
        return offset
    try:
        end = walk(0, ())
    except (struct.error, UnicodeError) as exc:
        raise ValueError('invalid ADT') from exc
    if end != len(data) or found != [bytes(16)]:
        raise ValueError('ADT needs a unique zeroed /chosen/memory-map/TrustCache (generate with --trust-cache)')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, required=True, help='prelinked kernel.macho')
    parser.add_argument('--adt', type=Path, required=True, help='iPad-identity ADT')
    parser.add_argument('--trust', type=Path, required=True, help='trust bundle from make-trust-bundle.py')
    parser.add_argument('--efi', type=Path, required=True, help='EFI build output as produced by check-efi.py')
    parser.add_argument('--out', type=Path, default=ROOT / 'out/ios-esp/ios-esp.img')
    args = parser.parse_args()

    efi = args.efi.resolve()
    files = {
        'EFI/BOOT/BOOTAA64.EFI': efi / 'efi/Build/Out/AARCH64-DEBUG/UniXNU.efi',
        'EFI/UniXNU/Payloads/DarwinBoot.efi': efi / 'loader/build/DarwinBoot/DEBUG_GCC/AARCH64/DarwinBoot.efi',
        'EFI/UniXNU/Payloads/kernel.macho': args.kernel.resolve(),
        'EFI/UniXNU/Payloads/kernel.macho.adt': args.adt.resolve(),
        'EFI/UniXNU/Payloads/kernel.macho.trustcache': args.trust.resolve(),
        'EFI/UniXNU/unixnu.toml': None,
    }
    for name, path in files.items():
        if path is not None and not path.is_file():
            raise SystemExit(f'missing input: {path}')

    try:
        require_trust_placeholder(args.adt.read_bytes())
    except ValueError as error:
        parser.error(str(error))

    out = args.out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['truncate', '-s', '256M', str(out)], check=True)
    subprocess.run(['mformat', '-i', str(out), '-v', 'POLARISESP', '::'], check=True)
    made = {'/'}
    for name in files:
        parts = name.split('/')[:-1]
        current = ''
        for part in parts:
            current += '/' + part
            if current not in made:
                subprocess.run(['mmd', '-i', str(out), f'::{current}'], check=True)
                made.add(current)
    for name, path in files.items():
        target = f'::{name}'
        if path is None:
            subprocess.run(['mcopy', '-i', str(out), '-', target], input=TOML.encode(), check=True)
        else:
            subprocess.run(['mcopy', '-i', str(out), str(path), target], check=True)
    listing = subprocess.run(['mdir', '-i', str(out), '-/', '::'], capture_output=True, text=True, check=True).stdout
    print(listing)
    report = {'image': str(out), 'image_bytes': out.stat().st_size,
              'kernel_sha256': hashlib.sha256(args.kernel.read_bytes()).hexdigest(),
              'trust_sha256': hashlib.sha256(args.trust.read_bytes()).hexdigest(),
              'flash_command': f'fastboot flash <esp-partition> {out}'}
    out.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
    print('done:', out)


if __name__ == '__main__':
    main()
