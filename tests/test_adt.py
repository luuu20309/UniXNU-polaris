"""Check the actual binary ABI handed to ArmDTPlatform, ArmCpu and DarwinBoot."""
import importlib.util
from pathlib import Path
import struct
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('polaris_adt', ROOT / 'scripts/make-polaris-adt.py')
polaris = importlib.util.module_from_spec(spec)
spec.loader.exec_module(polaris)


def decode(blob, offset=0, parent=''):
    nprops, nchildren = struct.unpack_from('<II', blob, offset)
    offset += 8
    props = {}
    for _ in range(nprops):
        name, size = struct.unpack_from('<32sI', blob, offset)
        offset += 36
        key = name.split(b'\0', 1)[0].decode()
        if key in props or offset + size > len(blob):
            raise ValueError('invalid property')
        props[key] = blob[offset:offset + size]
        offset += (size + 3) & ~3
    name = props['name'].rstrip(b'\0').decode()
    path = parent + '/' + name if parent else ''
    result = {path: props}
    for _ in range(nchildren):
        children, offset = decode(blob, offset, path or '/')
        result.update({key.replace('//', '/'): value for key, value in children.items()})
    return result, offset


class AdtTests(unittest.TestCase):
    def tree(self, **kwargs):
        data = polaris.build(seed=bytes(range(64)), **kwargs)
        nodes, offset = decode(data)
        self.assertEqual(offset, len(data))
        return nodes

    def test_gic_resources_translate_to_measured_hardware(self):
        tree = self.tree()
        bus = struct.unpack('<3Q', tree['/arm-io']['ranges'])
        regs = struct.unpack('<4Q', tree['/arm-io/intc']['reg'])
        self.assertEqual(bus[1] + regs[0] - bus[0], 0x17a00000)
        self.assertEqual(bus[1] + regs[2] - bus[0], 0x17a60000)
        self.assertLessEqual(regs[2] + regs[3], bus[2])
        self.assertEqual(tree['/arm-io/intc']['virtual-timer-intid'], b'\x13\0\0\0')
        self.assertEqual(struct.unpack('<Q', tree['/arm-io/intc']['redistributor-stride'])[0], 0x20000)

    def test_required_platform_nodes_and_volatile_nvram(self):
        tree = self.tree()
        for path in ['/chosen', '/chosen/memory-map', '/chosen/manifest-properties',
                     '/cpus', '/defaults', '/options', '/product', '/arm-io/intc', '/psci']:
            self.assertIn(path, tree)
        chosen = tree['/chosen']
        bank = chosen['nvram-proxy-data']
        self.assertEqual(len(bank), 8192)
        self.assertEqual(struct.unpack_from('<I', bank, 16)[0], zlib.adler32(bank[20:]))
        for offset, blocks, name in [(0, 2, b'nvram'), (32, 129, b'common'), (2096, 381, b'free')]:
            sig, actual_checksum, actual_blocks, actual_name = struct.unpack_from('<BBH12s', bank, offset)
            self.assertEqual((sig, actual_blocks, actual_name.rstrip(b'\0')), (0x70, blocks, name))
            header = bytearray(bank[offset:offset + 16])
            header[1] = 0
            checksum = sum(header)
            while checksum > 255:
                checksum = (checksum & 255) + (checksum >> 8)
            self.assertEqual(actual_checksum, checksum)

    def test_default_six_gib_bank_and_single_cpu(self):
        tree = self.tree()
        self.assertEqual(struct.unpack('<Q', tree['/chosen']['dram-base'])[0], 0x100000000)
        self.assertEqual(struct.unpack('<Q', tree['/chosen']['dram-size'])[0], 0x7cc00000)
        self.assertEqual(struct.unpack('<2Q', tree['/chosen']['darwinboot-memory-range']),
                         (0x100000000, 0x7cc00000))
        self.assertIn('/cpus/cpu0', tree)
        self.assertNotIn('/cpus/cpu1', tree)
        self.assertEqual(tree['/psci']['method'], b'smc\0')

    def test_eight_gib_bank_option(self):
        tree = self.tree(dram_size=polaris.DRAM_SIZE_8)
        self.assertEqual(struct.unpack('<Q', tree['/chosen']['dram-size'])[0], 0xfcc00000)

    def test_cpu_prefix_keeps_silver_gold_boundary_at_core_four(self):
        tree = self.tree(cpu_count=8)
        for core in range(8):
            cpu = tree[f'/cpus/cpu{core}']
            self.assertEqual(struct.unpack('<I', cpu['reg'])[0], core << 8)
            expected = b'arm,cortex-a75\0' if core >= 4 else b'arm,cortex-a55\0'
            self.assertEqual(cpu['compatible'], expected)
            self.assertEqual(cpu['state'], b'running\0' if core == 0 else b'waiting\0')
            self.assertEqual(struct.unpack('<I', cpu['timebase-frequency'])[0], 19200000)

    def test_rejects_bad_counts_banks_and_seeds(self):
        for kwargs in [dict(cpu_count=0), dict(cpu_count=9),
                       dict(dram_size=0x80000000), dict(owned_memory_size=0x8000000),
                       dict(owned_memory_size=0x40000100), dict(seed=b'\0' * 8)]:
            with self.assertRaises(ValueError):
                polaris.build(**kwargs)


if __name__ == '__main__':
    unittest.main()
