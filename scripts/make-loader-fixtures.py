#!/usr/bin/env python3
"""Build synthetic QEMU kernels/ADTs to test DarwinBoot; no Apple binaries.

The machine code uses QEMU's PL011 serial MMIO. These outputs are strictly
emulator fixtures and must never be used as a phone image.
"""
import argparse
import importlib.util
from pathlib import Path
import shutil
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASE = 0xfffffff007004000


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    tools = {name: shutil.which(name) for name in ['clang', 'ld.lld', 'llvm-objcopy']}
    if not all(tools.values()):
        parser.error('clang, ld.lld and llvm-objcopy are required')
    subprocess.run([tools['clang'], '--target=aarch64-none-elf', '-c', ROOT / 'tests/Kernel.S',
                    '-o', out / 'kernel.o'], check=True)
    subprocess.run([tools['ld.lld'], f'-Ttext={BASE + 0x1000:#x}', '-e', '_start',
                    out / 'kernel.o', '-o', out / 'kernel.elf'], check=True)
    subprocess.run([tools['llvm-objcopy'], '-O', 'binary', out / 'kernel.elf', out / 'code.bin'], check=True)
    code = (out / 'code.bin').read_bytes()
    if len(code) > 0x1000:
        raise SystemExit('Fixture exceeds TEXT segment')
    state = bytearray(272)
    struct.pack_into('<Q', state, 256, BASE + 0x1000)
    segment = struct.pack('<II16sQQQQiiII', 0x19, 72, b'__TEXT', BASE, 0x2000,
                          0, 0x2000, 5, 5, 0, 0)
    commands = segment + struct.pack('<IIII', 5, 288, 6, 68) + state
    image = bytearray(0x2000)
    image[:32] = struct.pack('<8I', 0xfeedfacf, 0x100000c, 0, 2, 2, len(commands), 0, 0)
    image[32:32 + len(commands)] = commands
    image[0x1000:0x1000 + len(code)] = code

    spec = importlib.util.spec_from_file_location('polaris_adt', ROOT / 'scripts/make-polaris-adt.py')
    adt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adt)
    valid = ('darwinboot-memory-range', adt.u64(0x40000000, 0x40000000))
    cases = {
        'allowed': [valid],
        'absent': [],
        # A polaris upper-bank range that no QEMU memory map ever reports.
        'unavailable': [('darwinboot-memory-range', adt.u64(0x100000000, 0x7cc00000))],
        'bad-size': [('darwinboot-memory-range', bytes(8))],
        'duplicate': [valid, valid],
        'unaligned': [('darwinboot-memory-range', adt.u64(0x40000001, 0x40000000))],
        'empty': [('darwinboot-memory-range', adt.u64(0x40000000, 0))],
        'overflow': [('darwinboot-memory-range', adt.u64(0xfffffffffffff000, 0x2000))],
        'misplaced': [],
    }
    for name, properties in cases.items():
        tree = adt.node('device-tree', [valid] if name == 'misplaced' else [], [adt.node('chosen', [
            ('darwinboot-memory-size', adt.u64(0x10000000)), *properties,
        ], [adt.node('memory-map')])])
        (out / (name + '.macho')).write_bytes(image)
        (out / (name + '.macho.adt')).write_bytes(tree)
    print('Generated 9 QEMU-only Mach-O/ADT loader fixtures')


if __name__ == '__main__':
    main()
