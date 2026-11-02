#!/usr/bin/env python3
"""Check the embedded corecrypto kext's file-backed POST HMAC, read-only.

This checks only the prelinked com.apple.kec.corecrypto component. It does not
validate the whole kernel, perform runtime self-tests, or authenticate the root.
It deliberately refuses unfamiliar Mach-O layouts rather than guessing.
"""
import argparse
import hashlib
import hmac
import json
import plistlib
from pathlib import Path
import struct

MAGIC_64 = 0xFEEDFACF
LC_SEGMENT_64 = 0x19
MH_KEXT_BUNDLE = 0xB
MH_DYLIB_IN_CACHE = 0x80000000


def parse_macho(data, base):
    if base < 0 or base + 32 > len(data):
        raise ValueError('Mach-O header outside file')
    magic, _, _, filetype, ncmds, sizeofcmds, flags, _ = struct.unpack_from('<8I', data, base)
    if magic != MAGIC_64 or base + 32 + sizeofcmds > len(data):
        raise ValueError('Expected bounded 64-bit Mach-O load commands')
    cursor, end = base + 32, base + 32 + sizeofcmds
    segments, sections = [], []
    for _ in range(ncmds):
        if cursor + 8 > end:
            raise ValueError('Truncated load command')
        cmd, size = struct.unpack_from('<II', data, cursor)
        if size < 8 or cursor + size > end:
            raise ValueError('Invalid load command size')
        if cmd == LC_SEGMENT_64:
            if size < 72:
                raise ValueError('Truncated segment command')
            name, va, vsize, fileoff, filesize, _, _, nsects, _ = struct.unpack_from('<16sQQQQiiII', data, cursor + 8)
            name = name.rstrip(b'\0').decode('ascii')
            if 72 + 80 * nsects > size:
                raise ValueError('Truncated sections')
            segments.append((name, va, vsize, fileoff, filesize))
            for index in range(nsects):
                at = cursor + 72 + index * 80
                sect, seg, address, length, offset = struct.unpack_from('<16s16sQQI', data, at)
                sections.append((seg.rstrip(b'\0').decode('ascii'), sect.rstrip(b'\0').decode('ascii'),
                                 address, length, offset))
        cursor += size
    if cursor != end:
        raise ValueError('Load command size mismatch')
    return filetype, flags, segments, sections


def file_offset(segments, address, length, file_size):
    matches = []
    for name, va, _, off, size in segments:
        if va <= address and address + length <= va + size:
            matches.append((name, off + address - va))
    if len(matches) != 1 or matches[0][1] + length > file_size:
        raise ValueError(f'Address {address:#x} has no unique file-backed mapping')
    return matches[0]


def one(values, label):
    if len(values) != 1:
        raise ValueError(f'Expected exactly one {label}, found {len(values)}')
    return values[0]


def check(path):
    data = path.read_bytes()
    _, _, outer_segments, outer_sections = parse_macho(data, 0)
    _, _, info_va, info_len, _ = one(
        [s for s in outer_sections if s[:2] == ('__PRELINK_INFO', '__info')], 'prelink-info section')
    _, info_off = file_offset(outer_segments, info_va, info_len, len(data))
    info = data[info_off:info_off + info_len].rstrip(b'\0')
    if info.startswith(b'<dict>'):
        info = b'<?xml version="1.0" encoding="UTF-8"?><plist version="1.0">' + info + b'</plist>'
    entries = plistlib.loads(info).get('_PrelinkInfoDictionary', [])
    entry = one([e for e in entries if e.get('CFBundleIdentifier') == 'com.apple.kec.corecrypto'],
                'prelinked corecrypto metadata')
    address = entry.get('_PrelinkExecutableLoadAddr')
    if not isinstance(address, int):
        raise ValueError('corecrypto load address missing')
    _, header_off = file_offset(outer_segments, address, 32, len(data))
    kind, flags, _, sections = parse_macho(data, header_off)
    if kind != MH_KEXT_BUNDLE or flags & MH_DYLIB_IN_CACHE:
        raise ValueError('Unsupported corecrypto Mach-O type or cached-address mode')
    text = one([s for s in sections if s[1] == '__text' and s[0] in ('__TEXT', '__TEXT_EXEC')],
               'corecrypto text section')
    mac = one([s for s in sections if s[1] == '__fips_hmacs'], 'corecrypto HMAC section')
    if mac[3] != 32:
        raise ValueError('Expected a 32-byte corecrypto HMAC')
    locations = []
    for section in (text, mac):
        _, _, va, length, relative_off = section
        segment, mapped_off = file_offset(outer_segments, va, length, len(data))
        if mapped_off != header_off + relative_off:
            raise ValueError('Kext header-relative offset disagrees with outer segment mapping')
        locations.append((segment, mapped_off, length))
    text_seg, text_off, text_len = locations[0]
    mac_seg, mac_off, _ = locations[1]
    expected = data[mac_off:mac_off + 32]
    computed = hmac.new(b'\0', data[text_off:text_off + text_len], hashlib.sha256).digest()
    return {'file': str(path), 'sha256': hashlib.sha256(data).hexdigest(),
            'kext': 'com.apple.kec.corecrypto', 'kext_header_va': hex(address),
            'text_segment': text_seg, 'text_offset': hex(text_off), 'text_bytes': text_len,
            'hmac_segment': mac_seg, 'hmac_offset': hex(mac_off),
            'expected_hmac_sha256': expected.hex(), 'computed_hmac_sha256': computed.hex(),
            'matches': hmac.compare_digest(expected, computed),
            'scope': 'static prelinked corecrypto kext HMAC only; runtime POST not executed'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kernel', type=Path)
    args = parser.parse_args()
    report = check(args.kernel)
    print(json.dumps(report, indent=2))
    if not report['matches']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
