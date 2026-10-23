#!/usr/bin/env python3
"""Run the real source kernel in a disposable AArch64 QEMU platform.

Uses the already built UniXNU/DarwinBoot EFI binaries and macOS kernel
artifact. The ESP is read-only, firmware variables are disposable, networking
is disabled, and no physical device or host disk is passed to QEMU. This
checks emulated CPU registration and advancing architectural timer IRQs,
not sweet hardware, storage, Apple components or userland.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def symbols(data, wanted):
    if struct.unpack_from('<4I', data) != (0xfeedfacf, 0x100000c, 0, 2):
        raise ValueError('expected a plain ARM64 executable Mach-O')
    at, table = 32, None
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        kind, size = struct.unpack_from('<II', data, at)
        if size < 8 or at + size > len(data):
            raise ValueError('invalid Mach-O command')
        if kind == 2:
            table = struct.unpack_from('<4I', data, at + 8)
        at += size
    if table is None:
        raise ValueError('kernel must retain its symbol table')
    start, count, strings, length = table
    if start + count * 16 > len(data) or strings + length > len(data):
        raise ValueError('invalid symbol table bounds')
    found = {}
    for index in range(count):
        name, kind, section, _, value = struct.unpack_from('<IBBHQ', data, start + 16 * index)
        if name >= length or not section or kind & 0xe0:
            continue
        end = data.find(b'\0', strings + name, strings + length)
        if end == -1:
            raise ValueError('unterminated symbol')
        key = data[strings + name:end].decode(errors='replace')
        if key in wanted:
            found[key] = value
    if found.keys() != wanted:
        raise ValueError('missing platform symbols: ' + str(wanted - found.keys()))
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--efi', type=Path, default=ROOT / 'out/efi-check')
    parser.add_argument('--out', type=Path, default=ROOT / 'out/platform-check')
    args = parser.parse_args()
    out, efi = args.out.resolve(), args.efi.resolve()
    out.mkdir(parents=True, exist_ok=True)
    data = args.kernel.read_bytes()
    build = json.loads(args.kernel.with_suffix('.json').read_text())
    if (build.get('kernel_sha256') != hashlib.sha256(data).hexdigest() or
            build.get('original_components_prelinked') is not False or
            build.get('final_configured_link_passed') is not True):
        parser.error('requires the matching source-kernel build report from build-platform-kernel.py')
    counters = {'_armcpu_registered': 4, '_armcpu_gic_ready': 4,
                '_armcpu_timer_interrupts': 8, '_armcpu_ipi_interrupts': 8, '_armcpu_ipi_sent': 8,
                '_kernel_debugger_entry_count': 4, '_debugger_panic_str': 8,
                '_debugger_panic_caller': 8, '_debugger_panic_args': 8}
    addresses = symbols(data, counters.keys())
    binary = efi / 'efi/Build/Out/AARCH64-DEBUG/UniXNU.efi'
    loader = efi / 'loader/build/DarwinBoot/DEBUG_GCC/AARCH64/DarwinBoot.efi'
    esp = out / 'esp'
    for source, relative in [(binary, 'EFI/BOOT/BOOTAA64.EFI'),
                             (loader, 'EFI/UniXNU/Payloads/DarwinBoot.efi'),
                             (args.kernel, 'EFI/UniXNU/Payloads/kernel.macho')]:
        target = esp / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    # Reuse only the ADT serialization and volatile NVRAM format. All
    # resources below describe QEMU virt, never the sweet board.
    spec = importlib.util.spec_from_file_location('polaris_adt', ROOT / 'scripts/make-polaris-adt.py')
    adt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adt)
    n, u32, u64 = adt.node, adt.u32, adt.u64
    tree = n('device-tree', [('model', b'qemu-platform-check\0'),
                            ('compatible', b'linux,dummy-virt\0')], [
        n('chosen', [('random-seed', os.urandom(64)),
                     ('dram-base', u64(0x40000000)), ('dram-size', u64(0x80000000)),
                     ('darwinboot-memory-size', u64(0x40000000)),
                     *adt.empty_nvram_proxy()], [n('memory-map'), n('manifest-properties')]),
        n('cpus', children=[n('cpu0', [('device_type', b'cpu\0'),
            ('compatible', b'arm,cortex-a76\0'), ('reg', u32(0)),
            ('state', b'running\0'), ('timebase-frequency', u32(62500000))])]),
        n('defaults'), n('options'),
        n('product', [('product-name', b'QEMU virt\0'), ('model', b'linux,dummy-virt\0')]),
        n('arm-io', [('device_type', b'qemu-virt\0'),
                    ('ranges', u64(0, 0x08000000, 0x04000000))], [
            n('intc', [('compatible', b'arm,gic-v3\0'),
                       ('reg', u64(0, 0x10000, 0xa0000, 0xf60000)),
                       ('redistributor-stride', u64(0x20000)),
                       ('virtual-timer-intid', u32(27))])])])
    (esp / 'EFI/UniXNU/Payloads/kernel.macho.adt').write_bytes(tree)
    (esp / 'EFI/UniXNU/unixnu.toml').write_text(
        'version = 1\n[boot]\ntimeout = 0\n[kits]\nautoload = false\n'
        '[[driver]]\npath = "Payloads/DarwinBoot.efi"\n'
        '[[entry]]\nid = "platform"\ntitle = "QEMU source kernel"\ntype = "xnu"\n'
        'path = "Payloads/kernel.macho"\n'
        'args = "-v serial=0 debug=0x14e kt_fixed_zones=120 cpus=1 armcpu-self-ipi=1 -enable_kprintf_spam -noprogress keepsyms=1"\n')
    firmware = [(Path(c), Path(v)) for c, v in [
        ('/usr/share/edk2/aarch64/QEMU_EFI.fd', '/usr/share/edk2/aarch64/QEMU_VARS.fd'),
        ('/usr/share/AAVMF/AAVMF_CODE.fd', '/usr/share/AAVMF/AAVMF_VARS.fd')]
        if Path(c).is_file() and Path(v).is_file()]
    if not firmware or not shutil.which('qemu-system-aarch64'):
        parser.error('requires QEMU AArch64 and AAVMF code/variables images')
    code, variables = firmware[0]
    log = out / 'serial.log'
    log.unlink(missing_ok=True)
    snapshots, passed = [], False
    with tempfile.TemporaryDirectory(prefix='polaris-platform-') as temporary:
        work = Path(temporary)
        shutil.copyfile(variables, work / 'vars.fd')
        command = ['qemu-system-aarch64', '-machine', 'virt,gic-version=3',
                   '-cpu', 'cortex-a76', '-m', '2048', '-smp', '1',
                   '-drive', f'if=pflash,format=raw,readonly=on,file={code}',
                   '-drive', f'if=pflash,format=raw,file={work / "vars.fd"}',
                   '-drive', f'if=none,id=esp,format=raw,readonly=on,file=fat:{esp}',
                   '-device', 'virtio-blk-pci,drive=esp', '-device', 'ramfb',
                   '-nic', 'none', '-display', 'none', '-no-reboot',
                   '-serial', f'file:{log}', '-qmp', f'unix:{work / "qmp"},server=on,wait=off']
        (out / 'command.json').write_text(json.dumps(command, indent=2) + '\n')
        with (out / 'qemu.log').open('w') as output:
            process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 90
                sock = socket.socket(socket.AF_UNIX)
                sock.settimeout(10)
                while True:
                    try:
                        sock.connect(str(work / 'qmp'))
                        break
                    except (FileNotFoundError, ConnectionRefusedError):
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError('QEMU did not start')
                        time.sleep(0.1)
                with sock, sock.makefile('rw') as stream:
                    json.loads(stream.readline())

                    def qmp(name, **arguments):
                        stream.write(json.dumps({'execute': name, 'arguments': arguments}) + '\n')
                        stream.flush()
                        while True:
                            reply = json.loads(stream.readline())
                            if 'error' in reply:
                                raise RuntimeError(reply['error'])
                            if 'return' in reply:
                                return reply['return']

                    qmp('qmp_capabilities')
                    while process.poll() is None and time.monotonic() < deadline:
                        text = log.read_text(errors='replace') if log.exists() else ''
                        match = re.search(r'DARWINBOOT: ARM64 va=0x([0-9a-fA-F]+) pa=0x([0-9a-fA-F]+)', text)
                        if match:
                            va, pa = (int(value, 16) for value in match.groups())
                            sample = {}
                            for name, size in counters.items():
                                physical = pa + addresses[name] - va
                                if not pa <= physical < pa + 0x40000000 - size:
                                    raise RuntimeError('counter is outside the owned window')
                                dump = work / 'counter.bin'
                                qmp('pmemsave', val=physical, size=size, filename=str(dump))
                                sample[name] = int.from_bytes(dump.read_bytes(), 'little')
                            snapshots.append(sample)
                            if sample['_kernel_debugger_entry_count']:
                                pointer = sample['_debugger_panic_str']
                                if va <= pointer < va + 0x40000000 - 1024:
                                    dump = out / 'panic-format.bin'
                                    qmp('pmemsave', val=pa + pointer - va, size=1024, filename=str(dump))
                                    (out / 'panic-format.txt').write_text(dump.read_bytes().split(b'\0')[0].decode(errors='replace'))
                                # Darwin ARM64 va_list is a pointer. Preserve
                                # both the list pointer and bounded argument
                                # storage for diagnosing an early assertion.
                                pointer = sample['_debugger_panic_args']
                                if va <= pointer < va + 0x40000000 - 8:
                                    dump = out / 'panic-args-pointer.bin'
                                    qmp('pmemsave', val=pa + pointer - va, size=8, filename=str(dump))
                                    pointer = int.from_bytes(dump.read_bytes(), 'little')
                                    if va <= pointer < va + 0x40000000 - 128:
                                        dump = out / 'panic-args.bin'
                                        qmp('pmemsave', val=pa + pointer - va, size=128, filename=str(dump))
                                        for index, value in enumerate(struct.unpack('<16Q', dump.read_bytes())):
                                            if va <= value < va + 0x40000000 - 512:
                                                target = out / f'panic-arg-{index}.bin'
                                                qmp('pmemsave', val=pa + value - va, size=512, filename=str(target))
                                break
                            if (len(snapshots) > 1 and sample['_armcpu_registered'] == 1 and
                                    sample['_armcpu_gic_ready'] == 1 and
                                    sample['_armcpu_timer_interrupts'] > snapshots[-2]['_armcpu_timer_interrupts'] > 0):
                                passed = True
                                break
                        time.sleep(1)
                    qmp('screendump', filename=str(out / 'screen.ppm'))
                    (out / 'registers.txt').write_text(qmp('human-monitor-command', **{'command-line': 'info registers'}))
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    report = {'passed': passed, 'milestone': 'single-cpu registration and advancing timer IRQs',
              'self_ipi_received': bool(snapshots and snapshots[-1]['_armcpu_ipi_interrupts']),
              'smp_qualified': False, 'kernel_sha256': hashlib.sha256(data).hexdigest(),
              'source_kernel_build': build,
              'counter_snapshots': snapshots, 'physical_polaris_booted': False,
              'userland_booted': False, 'original_components_prelinked': False}
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    if not passed:
        raise SystemExit(f'FAIL: native platform check; inspect {out}')
    print('PASS: actual XNU registered ArmCpu, enabled GIC and advanced timer IRQs in QEMU')
    print('Self-IPI receipt:', report['self_ipi_received'], '; SMP remains unqualified')
    print('Emulated platform only; no physical polaris or userland boot claimed')


if __name__ == '__main__':
    main()
