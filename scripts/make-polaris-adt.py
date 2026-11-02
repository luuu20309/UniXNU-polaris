#!/usr/bin/env python3
"""Generate the UniXNU platform-stage ADT for polaris (SDM845, Mi MIX 2S).

Writes only the requested local output. No device access or Apple input is
needed. This describes real polaris hardware without an Apple product
identity. The old ABL-shim chain's iPhone X (D22AP) identity and framebuffer
do not carry over; the UFS controller node now describes the SDM845 device
used by the UniXNU native-root bring-up. Measured board constants come from
the upstream Xiaomi polaris device tree and the ios16_on_polaris_mix2s notes.
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
# SDM845 UFSHCI address from the polaris ABL shim and the public SDM845 DT.
UFS_HC_BASE = 0x01D84000
UFS_HC_SIZE = 0x2500
# SDM845 and SM7150 share the same GICv3 layout observed on both boards.
GICD = 0x17A00000
GICR = 0x17A60000
# Locally assigned node handle for the GIC. XNU's IODTMapOneInterrupt follows
# "interrupt-parent" to an "interrupt-controller" node and names it with
# IODTInterruptControllerName(), which reads "AAPL,phandle" and asserts when it
# is absent (iokit/Kernel/IODeviceTreeSupport.cpp). A controller node also needs
# "#interrupt-cells" for IODTGetICellCounts(). Pick a value no real Apple tree
# uses and keep it stable so serial logs stay comparable.
GIC_PHANDLE = 0x1
INTERRUPT_CELLS = 1
# AMFI reads this u32 from the device-tree root; only its presence and width
# are validated, so a non-Apple bring-up tree uses 0.
AMFI_PLATFORM_CODE = 0
# SDM845 APCS/PMGR block. The bring-up PMGR personality only needs an
# AppleARMIODevice provider and the two misc offsets below; it does not touch
# these registers while AMFI is initializing.
PMGR_BASE = 0x17980000
PMGR_SIZE = 0x6000
PMGR_MISC_CORES_OFFSET = 0x0
PMGR_MISC_ACG_OFFSET = 0x4
# /chosen: whether SEP-protected data is available (0 = no SEP on this board)
PROTECTED_DATA_ACCESS = 0
# APFS root volume of the selected iPadOS image. XNU's IOFindBSDRoot reads
# /chosen/root-matching (an XML-serialized dictionary) and waits for the IOMedia
# whose UUID matches; without it the fallback looks for HFS media and the boot
# panics "Failed to mount root device". Cross-checked against UniXNU's sheng
# device record, which pins the same value for this image.
ROOT_VOLUME_UUID = '04769D26-2CA0-4825-B443-E2E5F88300F2'
# /sbin/mount reads this from IODeviceTree:/chosen with
# IORegistryEntryCreateCFProperty and exits 66 (EX_NOINPUT) when it is absent:
#   "failed to get boot-manifest-hash"          (0x100004d88, returns ENOENT)
#   "failed to get boot manifest hash - %s"     (0x100006ba0, then _errx)
# sysexit_np(ENOENT) is 66, which is what launchd reports for mount-phase-1.
# Verified in the retail iPadOS 17.7.11 /sbin/mount (sha256 f7f7aa9b...).
#
# XNU accepts up to CCSHA384_OUTPUT_SIZE (48) bytes of raw digest, and this
# board has no signed owner manifest, so the value is an explicit constant
# rather than a computed identity. Anything 48 bytes or shorter is accepted;
# the directory /private/preboot/<HEX> that mount derives from it must match
# whatever is placed in the root image.
BOOT_MANIFEST_HASH = bytes.fromhex('a5' * 48)
TIMEBASE = 19200000
TIMER_INTID = 19
# 4x Cortex-A55 (MPIDR 0x000..0x300) + 4x Cortex-A75 (0x400..0x700); SDM845
# encodes the core in Aff1 (reg = core << 8), Aff0 stays 0.
SILVER_CORES = 4
SILVER_HZ = 1804800000
GOLD_HZ = 2803200000


def boot_manifest_hash():
    """Raw boot-manifest-hash digest for /chosen.

    IOGetBootManifestHash() takes up to 48 bytes verbatim and /sbin/mount turns
    the bytes into an uppercase hex string for /private/preboot/<HEX>, so the
    value has to be stable across boots: the matching preboot directory is part
    of the root image, not created at runtime.
    """
    return BOOT_MANIFEST_HASH


def root_matching():
    """Serialized IOKit matching dictionary selecting the APFS root volume.

    IOFindBSDRoot() reads this with OSUnserializeXML() and hands the resulting
    OSDictionary to waitForService, so it has to be the XML container format
    IOKit uses, not a plist. The UUID is uppercase and matches the volume on the
    root image.
    """
    return (
        '<dict>'
        '<key>IOProviderClass</key><string>IOMedia</string>'
        '<key>IOPropertyMatch</key>'
        '<dict><key>UUID</key><string>' + ROOT_VOLUME_UUID + '</string></dict>'
        '</dict>'
    ).encode() + b'\0'


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


def memory_map_placeholders(trust_cache, ramdisk):
    """Zeroed DarwinBoot placeholders under /chosen/memory-map.

    DarwinBoot requires a placeholder for every input it is handed and fails
    with EFI_COMPROMISED_DATA when one is missing, so the Data ramdisk entry
    must exist exactly when the payload carries a seed. TrustCache stays
    independent of it.

    The property name selects the delivery format, and DarwinBoot's AdtNode()
    decides from it (KeepPacked at DarwinBoot.c:222):

        RAMDisk        DarwinBoot expands DBRDISK1 in place (CopyRamdisk) and
                       writes the *expanded* length to the ADT. Window-limited.
        PackedRAMDisk  DarwinBoot copies the compressed bytes through and writes
                       the *compressed* length; the consumer must decompress.

    Verified on hardware 2026-11-03: with PackedRAMDisk the ADT received
    size=0xf0050 (the 983120-byte DBRDISK1), the kernel's mdev built a device of
    that size, and apfs mount returned ENOENT -- because the kernel has no
    DBRDISK1 decompressor. With RAMDisk the ADT receives the expanded length
    instead, which is what APFS needs, but the expanded bytes must fit the
    DarwinBoot window (WINDOW_SIZE 256 MiB minus BLOCK_SIZE 2 MiB).

    So this board must ship an *unpacked* seed and declare RAMDisk.
    """
    entries = []
    if trust_cache:
        entries.append(('TrustCache', u64(0, 0)))
    if ramdisk:
        entries.append(('PackedRAMDisk', u64(0, 0)))
    return entries


def build(*, cpu_count=1, dram_size=DRAM_SIZE_6, owned_memory_size=0x40000000,
          seed=None, ipad_identity=False, trust_cache=False, ramdisk=False):
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
    root_properties = [
        ('model', b'iPad7,1\0' if ipad_identity else b'polaris-platform-bringup\0'),
        ('compatible', b'xiaomi,polaris\0qcom,sdm845\0'),
        ('#address-cells', u32(2)), ('#size-cells', u32(2)),
    ]
    if ipad_identity:
        root_properties.insert(2, ('target-type', b'J120\0'))
    return node('device-tree', root_properties, [
        node('chosen', [
            ('random-seed', seed),
            # AMFI reads BOTH of these u32 properties from /chosen while it
            # initializes, and panics if either is absent or is not exactly
            # four bytes:
            #   "AMFI: no amfi-only-platform-code device tree node!"   :5026
            #   "AMFI: no protected-data-access device tree node!"
            # Verified against the kext: at 0xfffffff005ab4bf8 the code does
            # SecureDTLookupEntry(NULL, "/chosen", ...) and then
            # getProperty("/chosen", "amfi-only-platform-code"); putting the
            # property on the root node instead does not satisfy it.
            # Both are 0 here: this board has no SEP, and AMFI only checks
            # presence and width.
            ('amfi-only-platform-code', u32(AMFI_PLATFORM_CODE)),
            ('protected-data-access', u32(PROTECTED_DATA_ACCESS)),
            ('dram-base', u64(DRAM_BASE)), ('dram-size', u64(dram_size)),
            ('darwinboot-memory-size', u64(owned_memory_size)),
            ('darwinboot-memory-range', u64(DRAM_BASE, dram_size)),
            ('crypto-hash-method', b'sha2-384\0'),
            ('root-matching', root_matching()),
            ('boot-manifest-hash', boot_manifest_hash()),
            # UniXNU's devicetree.py sets this whenever a seed is present and
            # persistent_data is off: the Data volume lives in RAM. It tells the
            # owner Data path not to expect a persistent store, so the seed is
            # treated as scratch rather than as a durable installation.
            # No ephemeral-storage: the Data volume arrives through
            # NativeRamBlock as a real IOMedia and is mounted writable on
            # /private/var. UniXNU's devicetree.py:328-329 writes
            # ephemeral-storage only when persistent_data is off, and
            # os_variant_uses_ephemeral_storage makes datamigrator skip every
            # migration when it is present.
            *empty_nvram_proxy(),
        ], [node('memory-map', memory_map_placeholders(trust_cache, ramdisk)), node('manifest-properties')]),
        node('cpus', [('#address-cells', u32(1)), ('#size-cells', u32(0))], cpus),
        node('psci', [('compatible', b'arm,psci-1.0\0'), ('method', b'smc\0')]),
        node('defaults', [('sleep-disabled', u32(1))]), node('options'),
        # APFS refuses to mount an unencrypted Data volume. Before raising
        # "unencrypted data volume is not allowed" (apfs_vfsops.c:2276) the
        # original driver looks up /filesystems/DoNotUseSEP and skips the check
        # when the property merely exists, whatever its value. e5 hit exactly
        # this on hardware (docs/devices/e5.md:268: round 62 adds only that ADT
        # property, and APFS then advances from xid 6 to 246 with real writes),
        # and the sagit port sets the same empty value (ios_sagit
        # tools/project/build/bridge.py:239-240). Without it the Data mount
        # cannot succeed no matter how the seed is built.
        node('filesystems', [('DoNotUseSEP', b'')]),
        node('product', [('product-name', b'Mi MIX 2S\0'),
                         ('model', b'xiaomi,polaris\0')]),
        node('arm-io', [
            ('device_type', b'polaris-io\0'),
            ('#address-cells', u32(2)), ('#size-cells', u32(2)),
            ('ranges', u64(0, ARMIO_BASE, 0x100000000 - ARMIO_BASE)),
        ], [node('pmgr', [
            # AMFI's ConfigurationSettings reads ApplePMGRNub and panics with
            # "AMFI: No PMGR?" when no ApplePMGR service is present. ApplePMGR's
            # only personality is AppleBringUpPMGR, which matches this
            # compatible on an AppleARMIODevice provider - i.e. an arm-io child.
            # ArmDTPlatform::createDeviceNubs publishes every arm-io child, so
            # this node is what makes the service appear.
            ('compatible', b'pmgr1,bring-up\0'),
            ('reg', u64(PMGR_BASE - ARMIO_BASE, PMGR_SIZE)),
            # AppleBringUpPMGR::initRegGroups requires both of these offsets.
            ('misc-cores-offset', u32(PMGR_MISC_CORES_OFFSET)),
            ('misc-acg-offset', u32(PMGR_MISC_ACG_OFFSET)),
        ]), node('intc', [
            ('compatible', b'arm,gic-v3\0'), ('interrupt-controller', b''),
            # A controller is only reachable as an interrupt parent when it
            # carries both keys: AddPHandle() registers a node that has
            # "#interrupt-cells" and "AAPL,phandle", and FindPHandle() resolves
            # by the phandle. Without them IODTMapOneInterrupt() spins on the
            # parent it was handed instead of terminating at the controller.
            ('#interrupt-cells', u32(INTERRUPT_CELLS)),
            ('AAPL,phandle', u32(GIC_PHANDLE)),
            ('reg', u64(GICD - ARMIO_BASE, 0x10000, GICR - ARMIO_BASE, 0x100000)),
            ('redistributor-stride', u64(0x20000)),
            ('virtual-timer-intid', u32(TIMER_INTID)),
        ]), node('ufs@d84000', [
            ('compatible', b'qcom,sdm845-ufshc\0qcom,ufshc\0jedec,ufs-2.0\0'),
            ('reg', u64(UFS_HC_BASE - ARMIO_BASE, UFS_HC_SIZE)),
            ('interrupts', u32(265)),
            # Point the interrupt at the GIC explicitly. Relying on the bus
            # parent is not enough: IODTFindInterruptParent() would hand
            # IODeviceTreeAlloc the "arm-io" node, which has no
            # "interrupt-controller" property.
            ('interrupt-parent', u32(GIC_PHANDLE)),
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
    parser.add_argument('--ipad-identity', action='store_true',
                        help='add the iPad7,1/J120 root identity used by the local iPadOS 17 image')
    parser.add_argument('--data-ramdisk', type=Path, default=None,
                        help='Data seed supplied to the payload; adds the zeroed '
                             'DarwinBoot RAMDisk placeholder it requires')
    parser.add_argument('--trust-cache', action='store_true',
                        help='add the zeroed DarwinBoot TrustCache placeholder; requires a matching trust-cache payload')
    args = parser.parse_args()
    if args.data_ramdisk is not None and not args.data_ramdisk.is_file():
        parser.error(f'--data-ramdisk is not a file: {args.data_ramdisk}')
    try:
        regs = [int(value, 0) for value in args.cpu_regs.split(',')]
        if not regs or regs != [core << 8 for core in range(len(regs))]:
            raise ValueError('--cpu-regs must be the initial polaris affinity prefix')
        blob = build(cpu_count=len(regs), dram_size=DRAM_SIZE_6 if args.ram == 6 else DRAM_SIZE_8,
                     owned_memory_size=args.owned_memory_size, ipad_identity=args.ipad_identity,
                     trust_cache=args.trust_cache,
                     ramdisk=args.data_ramdisk is not None)
    except ValueError as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(blob)
    identity = 'iPad7,1/J120 + ' if args.ipad_identity else ''
    print(f'{args.output}: {len(blob)} bytes; {identity}Polaris ADT with passive UFS node; not hardware validated')


if __name__ == '__main__':
    main()
