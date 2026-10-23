#!/usr/bin/env python3
"""Generate the UniXNU platform-stage ADT for polaris (SDM845, Mi MIX 2S).

Writes only the requested local output. No device access or Apple input is
needed. This describes real polaris hardware without an Apple product
identity: the old ABL-shim chain's iPhone X (D22AP) identity, framebuffer and
UFS nodes belong to the retired XNU 8796 boot path and are deliberately not
carried over. Measured board constants come from the upstream Xiaomi polaris
device tree and the ios16_on_polaris_mix2s bring-up notes.
"""
import argparse
from pathlib import Path
import os
import struct
import zlib

# Upper DDR bank above every Qualcomm XPU carve-out. polaris ships 6 or 8 GiB;
# 6 GiB ends at 0x1_80000000 and leaves 0x7cc00000 after the top reservation,
# which is safe on both variants. --ram 8 selects the full 8 GiB bank.
DRAM_BASE = 0x100000000
DRAM_SIZE_6 = 0x7CC00000
DRAM_SIZE_8 = 0xFCC00000
ARMIO_BASE = 0x01000000
# SDM845 and SM7150 share the same GICv3 layout observed on both boards.
GICD = 0x17A00000
GICR = 0x17A60000
TIMEBASE = 19200000
TIMER_INTID = 19
# 4x Cortex-A55 (MPIDR 0x000..0x300) + 4x Cortex-A75 (0x400..0x700); SDM845
# encodes the core in Aff1 (reg = core << 8), Aff0 stays 0.
SILVER_CORES = 4
SILVER_HZ = 1804800000
GOLD_HZ = 2803200000


def u32(value):
    return struct.pack('<I', value)


def u64(*values):
    return struct.pack('<' + 'Q' * len(values), *values)


def node(name, properties=(), children=()):
    properties = [('name', name.encode() + b'\0'), *properties]
    result = bytearray(struct.pack('<II', len(properties), len(children)))
    for key, value in properties:
        encoded = key.encode()
        if len(encoded) > 31:
            raise ValueError('ADT property name exceeds 31 bytes')
        result += struct.pack('<32sI', encoded, len(value))
        result += value + bytes(-len(value) % 4)
    return bytes(result) + b''.join(children)


def empty_nvram_proxy():
    # Same volatile CHRP format as UniXNU's QEMU fixture. Nothing is written
    # to the phone's NVRAM or to UFS. Upstream IODTNVRAM validates the bank.
    def header(name, size):
        raw = bytearray(struct.pack('<BBH12s', 0x70, 0, size // 16, name))
        checksum = sum(raw)
        while checksum > 255:
            checksum = (checksum & 255) + (checksum >> 8)
        raw[1] = checksum
        return raw

    image = bytearray(8192)
    image[:16] = header(b'nvram', 32)
    image[32:48] = header(b'common', 16 + 2048)
    free_at = 32 + 16 + 2048
    image[free_at:free_at + 16] = header(b'free', len(image) - free_at)
    struct.pack_into('<I', image, 16, zlib.adler32(image[20:]))
    return [('nvram-bank-size', u32(len(image))),
            ('nvram-bank-count', u32(1)), ('nvram-current-bank', u32(0)),
            ('nvram-proxy-data', bytes(image))]


def build(*, cpu_count=1, dram_size=DRAM_SIZE_6, owned_memory_size=0x40000000, seed=None):
    if cpu_count not in range(1, 9):
        raise ValueError('cpu count must be 1..8')
    if dram_size not in (DRAM_SIZE_6, DRAM_SIZE_8):
        raise ValueError('dram size must be a recorded polaris bank (6 or 8 GiB)')
    if not 0x10000000 <= owned_memory_size <= dram_size or owned_memory_size % 0x200000:
        raise ValueError('owned memory must fit the selected bank, in 2 MiB steps, at least 256 MiB')
    seed = os.urandom(64) if seed is None else seed
    if len(seed) != 64:
        raise ValueError('random seed must contain 64 bytes')
    cpus = []
    for core in range(cpu_count):
        gold = core >= SILVER_CORES
        cpus.append(node(f'cpu{core}', [
            ('device_type', b'cpu\0'),
            ('compatible', b'arm,cortex-a75\0' if gold else b'arm,cortex-a55\0'),
            ('reg', u32(core << 8)),
            ('state', b'running\0' if core == 0 else b'waiting\0'),
            ('timebase-frequency', u32(TIMEBASE)),
            # Descriptive only; UniXNU platform drivers do not read these.
            ('clock-frequency', u32(GOLD_HZ if gold else SILVER_HZ)),
        ]))
    return node('device-tree', [
        ('model', b'polaris-platform-bringup\0'),
        ('compatible', b'xiaomi,polaris\0qcom,sdm845\0'),
        ('#address-cells', u32(2)), ('#size-cells', u32(2)),
    ], [
        node('chosen', [
            ('random-seed', seed),
            ('dram-base', u64(DRAM_BASE)), ('dram-size', u64(dram_size)),
            ('darwinboot-memory-size', u64(owned_memory_size)),
            ('darwinboot-memory-range', u64(DRAM_BASE, dram_size)),
            ('crypto-hash-method', b'sha2-384\0'),
            *empty_nvram_proxy(),
        ], [node('memory-map'), node('manifest-properties')]),
        node('cpus', [('#address-cells', u32(1)), ('#size-cells', u32(0))], cpus),
        node('psci', [('compatible', b'arm,psci-1.0\0'), ('method', b'smc\0')]),
        node('defaults', [('sleep-disabled', u32(1))]), node('options'),
        node('product', [('product-name', b'Mi MIX 2S\0'),
                         ('model', b'xiaomi,polaris\0')]),
        node('arm-io', [
            ('device_type', b'polaris-io\0'),
            ('#address-cells', u32(2)), ('#size-cells', u32(2)),
            ('ranges', u64(0, ARMIO_BASE, 0x100000000 - ARMIO_BASE)),
        ], [node('intc', [
            ('compatible', b'arm,gic-v3\0'), ('interrupt-controller', b''),
            ('reg', u64(GICD - ARMIO_BASE, 0x10000, GICR - ARMIO_BASE, 0x100000)),
            ('redistributor-stride', u64(0x20000)),
            ('virtual-timer-intid', u32(TIMER_INTID)),
        ])]),
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ram', type=int, choices=(6, 8), default=6,
                        help='installed polaris RAM in GiB (default 6, safe on both variants)')
    parser.add_argument('--owned-memory-size', type=lambda value: int(value, 0), default=0x40000000)
    parser.add_argument('--cpu-regs', default='0',
                        help='Initial prefix of polaris affinities: 0,0x100,...,0x700')
    parser.add_argument('--psci-method', choices=['smc'], default='smc')
    args = parser.parse_args()
    try:
        regs = [int(value, 0) for value in args.cpu_regs.split(',')]
        if not regs or regs != [core << 8 for core in range(len(regs))]:
            raise ValueError('--cpu-regs must be the initial polaris affinity prefix')
        blob = build(cpu_count=len(regs), dram_size=DRAM_SIZE_6 if args.ram == 6 else DRAM_SIZE_8,
                     owned_memory_size=args.owned_memory_size)
    except ValueError as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(blob)
    print(f'{args.output}: {len(blob)} bytes; platform-stage ADT, not hardware validated')


if __name__ == '__main__':
    main()
