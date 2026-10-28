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

    def test_amfi_reads_both_properties_before_the_security_stack_starts(self):
        """Regression: AMFI panics on a tree without these two u32 properties.

        Observed on the phone as
          "AMFI: no amfi-only-platform-code device tree node!" @AppleMobileFileIntegrity.cpp:5026
        AMFI then reads /chosen "protected-data-access" the same way, and both
        have a sibling "unexpected size (%u)!" panic, so each must be exactly one
        u32. The root carries the first, /chosen the second.
        """
        tree = self.tree(ipad_identity=True)
        # decode() labels the root node '' and children '<parent>/<name>'.
        # Both live in /chosen: AMFI resolves "/chosen" with SecureDTLookupEntry
        # and then getProperty() on that node, so the root node does not satisfy
        # it (that mistake cost one flash round).
        for key in ('amfi-only-platform-code', 'protected-data-access'):
            props = tree['/chosen']
            self.assertIn(key, props, f'{key} missing from /chosen')
            self.assertEqual(len(props[key]), 4, f'{key} must be one u32')
            self.assertNotIn(key, tree[''], f'{key} must not be on the root node')

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

    def test_every_interrupt_resolves_to_a_registered_controller(self):
        """Regression: a dangling interrupt-parent spins IODeviceTreeAlloc forever.

        IODTMapOneInterrupt's `do { ... } while (parent)` clears `parent` only at
        an "interrupt-controller" or "interrupt-map" node. AddPHandle registers a
        node only when it has BOTH "#interrupt-cells" and "AAPL,phandle", and
        FindPHandle resolves by that phandle. So a node carrying "interrupts" must
        name a parent that carries both keys and is a controller, or XNU never
        returns from InitIOKit (observed on the phone as a silent hang).
        """
        tree = self.tree(ipad_identity=True)

        controllers = {}
        for path, props in tree.items():
            if 'interrupt-controller' not in props:
                continue
            self.assertIn('#interrupt-cells', props, f'{path} cannot be an interrupt parent')
            self.assertIn('AAPL,phandle', props, f'{path} cannot be resolved by FindPHandle')
            controllers[struct.unpack('<I', props['AAPL,phandle'])[0]] = path

        bearing = [p for p, props in tree.items()
                   if 'interrupts' in props or 'AAPL,interrupts' in props]
        self.assertTrue(bearing, 'the UFS node should still declare its interrupt')

        for path in bearing:
            props = tree[path]
            self.assertIn('interrupt-parent', props,
                          f'{path} would fall back to its bus parent, which is not a controller')
            phandle = struct.unpack('<I', props['interrupt-parent'])[0]
            self.assertIn(phandle, controllers,
                          f'{path} points at phandle {phandle:#x}, which no controller publishes')
            cells = struct.unpack('<I', tree[controllers[phandle]]['#interrupt-cells'])[0]
            self.assertGreaterEqual(cells, 1)
            self.assertEqual(len(props['interrupts']) % (cells * 4), 0,
                             f'{path} "interrupts" does not divide into {cells}-cell entries')

    def test_pmgr_node_satisfies_the_bring_up_pmgr_personality(self):
        """Regression: AMFI panics with "AMFI: No PMGR?" without this node.

        AMFI::ConfigurationSettings resolves ApplePMGRNub through
        IOService::serviceMatching and panics when nothing matches. ApplePMGR's
        only personality is AppleBringUpPMGR:
            IOProviderClass = AppleARMIODevice
            IONameMatch     = pmgr1,bring-up
        AppleARMIODevice providers are the arm-io children that
        ArmDTPlatform::createDeviceNubs publishes, so this node must be a child
        of arm-io and carry that compatible. initRegGroups() additionally
        requires misc-cores-offset and misc-acg-offset.
        """
        tree = self.tree(ipad_identity=True)
        pmgr = tree['/arm-io/pmgr']
        self.assertEqual(pmgr['compatible'], b'pmgr1,bring-up\0')
        for key in ('misc-cores-offset', 'misc-acg-offset'):
            self.assertIn(key, pmgr, f'{key} required by AppleBringUpPMGR::initRegGroups')
            self.assertEqual(len(pmgr[key]), 4)
        self.assertEqual(len(pmgr['reg']), 16)


if __name__ == '__main__':
    unittest.main()
