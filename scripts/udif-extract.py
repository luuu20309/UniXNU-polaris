#!/usr/bin/env python3
"""Extract a raw disk image from an Apple UDIF .dmg (the iOS 17+ LZFSE kind
dmg2img cannot read). Layout per the public UDIF contract: a 512-byte koly
trailer, an XML plist with the resource fork, and blkx/mish block tables whose
runs are decoded per type (raw/zero/ADC/zlib/bzip2/LZFSE).

  udif-extract.py input.dmg output.img
"""
import argparse
import base64
import bz2
import plistlib
import struct
import sys
import zlib

import liblzfse

ZERO = 0x00000000
RAW = 0x00000001
IGNORE = 0x00000002
ADC = 0x80000003
ZLIB = 0x80000004
BZ2 = 0x80000005
LZFSE = 0x80000006
COMMENT = 0x7FFFFFFE
TERMINATOR = 0xFFFFFFFF


def adc_decompress(data: bytes) -> bytes:
    out = bytearray()
    pos = 0
    while pos < len(data):
        tag = data[pos]
        pos += 1
        if tag >= 0x80:
            length = tag - 0x80 + 1
            out += data[pos:pos + length]
            pos += length
        else:
            length = tag + 1
            if pos + 3 > len(data):
                raise ValueError('short ADC run')
            chunk = bytes(data[pos:pos + 3]) * length
            out += chunk
            pos += 3
    return bytes(out)


def decode(run_type, chunk):
    if run_type == ZLIB:
        return zlib.decompress(chunk)
    if run_type == BZ2:
        return bz2.decompress(chunk)
    if run_type == LZFSE:
        return liblzfse.decompress(chunk)
    if run_type == ADC:
        return adc_decompress(chunk)
    raise ValueError(f'unsupported block type {run_type:#x}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dmg')
    parser.add_argument('out')
    args = parser.parse_args()

    with open(args.dmg, 'rb') as handle:
        handle.seek(-512, 2)
        koly = handle.read(512)
        if koly[:4] != b'koly':
            raise SystemExit('not a UDIF image (no koly trailer)')
        xml_offset, xml_length = struct.unpack_from('>QQ', koly, 216)
        data_fork_offset, data_fork_length = struct.unpack_from('>QQ', koly, 24)
        handle.seek(xml_offset)
        plist = plistlib.loads(handle.read(xml_length))
        handle.seek(data_fork_offset)
        data_fork = handle.read(data_fork_length)

    blocks = plist['resource-fork']['blkx']
    total_sectors = 0
    for block in blocks:
        mish = base64.b64decode(block['Data'])
        if mish[:4] != b'mish':
            raise SystemExit('blkx entry without a mish header')
        sector_number, sector_count = struct.unpack_from('>QQ', mish, 8)
        total_sectors = max(total_sectors, sector_number + sector_count)
    total = total_sectors * 512
    print(f'{args.dmg}: {total_sectors} sectors, {total} raw bytes, '
          f'{len(blocks)} blkx regions', flush=True)

    done = 0
    with open(args.out, 'wb') as target:
        target.truncate(total)
        for block in blocks:
            mish = base64.b64decode(block['Data'])
            sector_number, sector_count = struct.unpack_from('>QQ', mish, 8)
            descriptors = struct.unpack_from('>I', mish, 196)[0]
            target.seek(sector_number * 512)
            for index in range(descriptors):
                run = mish[200 + 40 * index: 200 + 40 * (index + 1)]
                run_type, _, sectors, offset, length = struct.unpack_from('>IIIQQ', run)
                if run_type == TERMINATOR:
                    break
                if run_type in (ZERO, IGNORE, COMMENT):
                    target.write(bytes(sectors * 512))
                elif run_type == RAW:
                    target.write(data_fork[offset:offset + length])
                else:
                    chunk = data_fork[offset:offset + length]
                    plain = decode(run_type, chunk)
                    if len(plain) != sectors * 512:
                        raise SystemExit(f'block {run_type:#x} decoded {len(plain)} bytes, '
                                         f'expected {sectors * 512}')
                    target.write(plain)
                done += sectors
                if done % 0x100000 < 0x800:
                    print(f'  {done / 2048:.0f} / {total_sectors / 2048:.0f} MiB', flush=True)
    print(f'done: {args.out}', flush=True)


if __name__ == '__main__':
    main()
