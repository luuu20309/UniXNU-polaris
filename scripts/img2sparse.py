#!/usr/bin/env python3
"""Stream an Android sparse image from a raw one, in two passes (no RAM blowup,
no seeking the input): pass 1 counts runs, pass 2 writes. Zero blocks become
don't-care runs; non-zero blocks are written 1:1. The sparse CRC field stays
zero, which every fastboot implementation accepts.

  img2sparse.py raw.img sparse.img
"""
import argparse
from pathlib import Path
import struct
import zlib

BLOCK = 131072
RAW_CHUNK = 0xCAC1
DONT_CARE = 0xCAC2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('raw')
    parser.add_argument('sparse')
    args = parser.parse_args()

    raw = Path(args.raw)
    out_path = Path(args.sparse)
    total = raw.stat().st_size
    assert total % BLOCK == 0, 'raw size must be block aligned'
    blocks_total = total // BLOCK

    # Pass 1: collect runs (block index -> kind), coalescing don't-care runs.
    runs = []  # (kind, count) sequence, plus data offsets for raw runs
    with raw.open('rb', buffering=0) as handle:
        pending_zero = 0
        block_index = 0
        while block_index < blocks_total:
            block = handle.read(BLOCK)
            if len(block) != BLOCK:
                raise SystemExit('unexpected end of input')
            if block.count(0) == BLOCK:
                pending_zero += 1
            else:
                if pending_zero:
                    runs.append((DONT_CARE, pending_zero, None))
                    pending_zero = 0
                runs.append((RAW_CHUNK, 1, block))
            block_index += 1
        if pending_zero:
            runs.append((DONT_CARE, pending_zero, None))

    print(f'{blocks_total} blocks; {len(runs)} runs', flush=True)
    with out_path.open('wb', buffering=1 << 20) as out:
        out.write(struct.pack('<IIIIIIIIII', 0xED26FF3A, 1, BLOCK, blocks_total,
                              len(runs), 28 + len(runs) * 12, 0, BLOCK // BLOCK, 0, 0))
        crc = 0
        done = 0
        for kind, count, data in runs:
            out.write(struct.pack('<2HII', kind, 0, count, 0))
            if kind == RAW_CHUNK:
                out.write(data)
                crc = zlib.crc32(data, crc)
            done += count
            if done % 2048 < count:
                print(f'{done * BLOCK >> 20} / {total >> 20} MiB', flush=True)


if __name__ == '__main__':
    main()
