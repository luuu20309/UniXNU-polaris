#!/usr/bin/env python3
"""Build the payload trust-cache bundle from the IPSW trust caches.

The iPadOS 17 IPSW carries each trust cache as a DER-wrapped IM4P ('trst')
whose OCTET STRING is one trust module (little-endian version 2 header, UUID,
entries). The loader/kernel want a bundle: a little-endian module count, the
module offsets, then the modules byte for byte — the same container
scripts/make-owner-trust-bundle.py produces for sheng.

  make-trust-bundle.py --static Firmware/044-76040-038.dmg.trustcache \
                       --system Firmware/044-76034-038.dmg.trustcache \
                       --out trust.bundle
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def im4p_module(path: Path) -> bytes:
    data = path.read_bytes()
    # Hand-stepped DER: SEQUENCE { IA5 'IM4P', IA5 type, IA5 desc?, OCTET STRING }.
    if data[0] != 0x30 or b'IM4P' not in data[:16]:
        raise SystemExit(f'{path}: not a DER IM4P')

    def length_at(offset):
        n = data[offset + 1]
        if n & 0x80:
            size = n & 0x7F
            return int.from_bytes(data[offset + 2:offset + 2 + size], 'big'), offset + 2 + size
        return n, offset + 2

    cursor = 4                               # into the first inner TLV
    kind = None
    module = None
    while cursor < len(data):
        tag = data[cursor]
        length, content_at = length_at(cursor)
        if tag == 0x16 and data[content_at:content_at + length] in (b'trst', b'trcs'):
            kind = data[content_at:content_at + length]
        elif tag == 0x04:
            module = data[content_at:content_at + length]
            break
        cursor = content_at + length
    if kind is None or module is None:
        raise SystemExit(f'{path}: IM4P without a type or payload')
    version = struct.unpack_from('<I', module)[0]
    if version != 2:
        raise SystemExit(f'{path}: {kind.decode()} payload is not a version-2 module')
    entries = struct.unpack_from('<I', module, 20)[0]
    if len(module) != 24 + entries * 24:
        raise SystemExit(f'{path}: module length mismatch ({len(module)} for {entries} entries)')
    return module


def valid_like_the_loader(bundle: bytes) -> None:
    count = struct.unpack_from('<I', bundle)[0]
    assert 0 < count <= 128
    for index in range(count):
        start = struct.unpack_from('<I', bundle, 4 + 4 * index)[0]
        end = struct.unpack_from('<I', bundle, 8 + 4 * index)[0] if index + 1 < count else len(bundle)
        assert start >= 4 + 4 * count and start & 3 == 0 and end <= len(bundle) and end - start >= 24
        assert struct.unpack_from('<I', bundle, start)[0] == 2
        entries = struct.unpack_from('<I', bundle, start + 20)[0]
        assert end - start == 24 + entries * 24


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static', type=Path, required=True, help='OS volume trust cache (044-76040-038.dmg.trustcache)')
    parser.add_argument('--system', type=Path, required=True, help='SystemOS cryptex trust cache (044-76034-038.dmg.trustcache)')
    parser.add_argument('--out', type=Path, default=Path('trust.bundle'))
    args = parser.parse_args()

    modules = [im4p_module(args.static), im4p_module(args.system)]
    starts, cursor = [], 4 + 4 * len(modules)
    for module in modules:
        starts.append(cursor)
        cursor += len(module)
    bundle = struct.pack(f'<{1 + len(modules)}I', len(modules), *starts) + b''.join(modules)
    valid_like_the_loader(bundle)
    args.out.write_bytes(bundle)
    report = {'bundle_sha256': hashlib.sha256(bundle).hexdigest(), 'bundle_bytes': len(bundle),
              'modules': [{'file': str(m_path), 'entries': struct.unpack_from('<I', m, 20)[0], 'sha256': hashlib.sha256(m).hexdigest()}
                          for m_path, m in ((args.static, modules[0]), (args.system, modules[1]))]}
    args.out.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'{args.out}: {len(bundle)} bytes, {len(modules)} modules, '
          f'{sum(struct.unpack_from("<I", m, 20)[0] for m in modules)} cdhash entries')


if __name__ == '__main__':
    main()
