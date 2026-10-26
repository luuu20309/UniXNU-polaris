#!/usr/bin/env python3
"""Extract a raw disk image from an Apple UDIF with LZFSE blkx runs.

The iPadOS 17 OS DMG is ULFO compressed. dmg2img in this build environment
does not decode it. This tool reads the koly trailer and mish block maps,
validates every run, and writes the decoded disk to a temporary file before
renaming it to the requested output. It does not access a device.
"""
import argparse
import bz2
import os
from pathlib import Path
import plistlib
import struct
import zlib

import liblzfse


ZERO = 0
RAW = 1
IGNORE = 2
ZLIB = 0x80000005
BZIP2 = 0x80000006
LZFSE = 0x80000007
COMMENT = 0x7FFFFFFE
END = 0xFFFFFFFF
SECTOR = 512


def read_exact(handle, offset, size):
    handle.seek(offset)
    data = handle.read(size)
    if len(data) != size:
        raise ValueError(f'short read at {offset:#x}: {len(data)} of {size} bytes')
    return data


def extract(source, output):
    source_bytes = source.stat().st_size
    with source.open('rb') as handle:
        trailer = read_exact(handle, source_bytes - 512, 512)
        if trailer[:4] != b'koly':
            raise ValueError('missing UDIF koly trailer')
        fork_offset, fork_bytes = struct.unpack_from('>QQ', trailer, 24)
        xml_offset, xml_bytes = struct.unpack_from('>QQ', trailer, 216)
        if fork_offset + fork_bytes > source_bytes - 512 or xml_offset + xml_bytes > source_bytes - 512:
            raise ValueError('UDIF data fork or resource plist is out of bounds')
        plist = plistlib.loads(read_exact(handle, xml_offset, xml_bytes))
        blocks = plist['resource-fork']['blkx']
        if not blocks:
            raise ValueError('UDIF has no blkx regions')

        regions = []
        for block in blocks:
            mish = block['Data']
            if mish[:4] != b'mish' or len(mish) < 244:
                raise ValueError('invalid mish header')
            first, sectors = struct.unpack_from('>QQ', mish, 8)
            count = struct.unpack_from('>I', mish, 200)[0]
            if not sectors or not count or len(mish) != 204 + 40 * count:
                raise ValueError('invalid mish run count')
            regions.append((first, sectors, mish, count))
        regions.sort(key=lambda region: region[0])
        cursor = 0
        for first, sectors, _, _ in regions:
            if first != cursor:
                raise ValueError(f'gap or overlap in UDIF regions at sector {first}')
            cursor += sectors
        total = cursor * SECTOR
        print(f'{source}: {total} raw bytes ({len(regions)} blkx regions)', flush=True)

        partial = output.with_name(output.name + '.partial')
        try:
            with partial.open('wb') as target:
                target.truncate(total)
                last_report = 0
                for first, sectors, mish, count in regions:
                    run_cursor = 0
                    ended = False
                    for index in range(count):
                        kind, _, start, length, offset, size = struct.unpack_from('>IIQQQQ', mish, 204 + 40 * index)
                        if kind == END:
                            if length or index != count - 1:
                                raise ValueError('invalid UDIF terminator')
                            ended = True
                            break
                        if kind == COMMENT:
                            continue
                        if start != run_cursor or not length or length > sectors - start:
                            raise ValueError(f'bad UDIF run geometry at sector {first + start}')
                        run_cursor += length
                        plain_bytes = length * SECTOR
                        if kind in (ZERO, IGNORE):
                            continue
                        if offset + size > fork_bytes:
                            raise ValueError('UDIF run exceeds data fork')
                        encoded = read_exact(handle, fork_offset + offset, size)
                        if kind == RAW:
                            decoded = encoded
                        elif kind == LZFSE:
                            decoded = liblzfse.decompress(encoded)
                        elif kind == ZLIB:
                            decoded = zlib.decompress(encoded)
                        elif kind == BZIP2:
                            decoded = bz2.decompress(encoded)
                        else:
                            raise ValueError(f'unsupported UDIF run type {kind:#x}')
                        if len(decoded) != plain_bytes:
                            raise ValueError(f'UDIF run decoded {len(decoded)} of {plain_bytes} bytes')
                        target.seek((first + start) * SECTOR)
                        target.write(decoded)
                        progress = (first + run_cursor) * SECTOR
                        if progress - last_report >= 512 * 1024 * 1024:
                            print(f'  {progress // (1024 * 1024)} MiB decoded', flush=True)
                            last_report = progress
                    if not ended or run_cursor != sectors:
                        raise ValueError(f'incomplete UDIF region at sector {first}')
            os.replace(partial, output)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    print(f'done: {output} ({total} bytes)', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    extract(args.source, args.output)


if __name__ == '__main__':
    main()
