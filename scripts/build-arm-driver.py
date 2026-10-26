#!/usr/bin/env python3
"""Compile a platform or SDM845 NativeUfs archive using XNU's compile record.

Used inside the assembled UniXNU tree. Runs the recorded compiler and xcrun
ar; writes only build/<component>/. Does not access a device.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def polaris_native_ufs_source(out):
    upstream = ROOT / 'drivers' / 'NativeUfs'
    if not (upstream / 'NativeUfs.cpp').is_file():
        raise SystemExit(f'NativeUfs submodule is missing: {upstream}')
    source_dir = out / 'source'
    if source_dir.exists():
        shutil.rmtree(source_dir)
    shutil.copytree(upstream, source_dir, ignore=shutil.ignore_patterns('.git'))

    source = source_dir / 'NativeUfs.cpp'
    text = source.read_text()
    replacements = [
        ('contains (text, data->getLength (), "qcom,sm8550-ufshc")',
         '(contains (text, data->getLength (), "qcom,sm8550-ufshc") ||\n'
         '              contains (text, data->getLength (), "qcom,sdm845-ufshc"))'),
        ('contains (text, string->getLength (), "qcom,sm8550-ufshc")',
         '(contains (text, string->getLength (), "qcom,sm8550-ufshc") ||\n'
         '              contains (text, string->getLength (), "qcom,sdm845-ufshc"))'),
        ('smmuBypassStream (0x60,', 'smmuBypassStream (0x720,'),
        ('((smr ^ 0x60U)', '((smr ^ 0x720U)'),
        ('sid0x60 match=', 'sid0x720 match='),
    ]
    for before, after in replacements:
        count = text.count(before)
        expected = 2 if before.startswith('smmuBypassStream') else 1
        if count != expected:
            raise SystemExit(f'NativeUfs source drift: expected {expected} copies of {before!r}, found {count}')
        text = text.replace(before, after)

    before = ('gRootDiagnostic.result = result.phase == UfsRootPhase::Identity ? kIOReturnNotPrivileged :\n'
              '      result.phase == UfsRootPhase::Schedule ? kIOReturnNoResources : kIOReturnError;')
    after = ('gRootDiagnostic.result = static_cast<uint32_t> (\n'
             '      result.phase == UfsRootPhase::Identity ? kIOReturnNotPrivileged :\n'
             '      result.phase == UfsRootPhase::Schedule ? kIOReturnNoResources : kIOReturnError);')
    if text.count(before) != 1:
        raise SystemExit('NativeUfs source drift: root error status expression not found uniquely')
    text = text.replace(before, after)

    # The pinned driver only declares the read timing counter when verified
    # blocks are enabled, but records timing unconditionally in its read path.
    # The Polaris root experiment does not enable that optional policy.
    before = ('      uint64_t t0 = timerTicks ();\n'
              '      IOReturn r = hostRead4k (0, lba, blocks, out);\n'
              '      __atomic_fetch_add (&native_ufs_read_ticks, timerTicks () - t0, __ATOMIC_RELAXED);\n'
              '      return r;')
    after = ('#if defined(NATIVE_VERIFIED_BLOCK) && NATIVE_VERIFIED_BLOCK\n'
             '      uint64_t t0 = timerTicks ();\n'
             '#endif\n'
             '      IOReturn r = hostRead4k (0, lba, blocks, out);\n'
             '#if defined(NATIVE_VERIFIED_BLOCK) && NATIVE_VERIFIED_BLOCK\n'
             '      __atomic_fetch_add (&native_ufs_read_ticks, timerTicks () - t0, __ATOMIC_RELAXED);\n'
             '#endif\n'
             '      return r;')
    if text.count(before) != 1:
        raise SystemExit('NativeUfs source drift: optional read timing block not found uniquely')
    text = text.replace(before, after)

    # This diagnostic reads SM8550-only GCC clock registers. Polaris root mode
    # uses the firmware-initialized UFS host and the SDM845 SMMU stream instead.
    before = '  if (probe == 2) {\n    inspect ();\n  }'
    after = '  if (probe == 2) {\n#ifdef NATIVE_UFS_POLARIS\n'
    after += '    panic ("NATIVE_UFS: SM8550 GCC probe is disabled on polaris");\n'
    after += '#else\n    inspect ();\n#endif\n  }'
    if text.count(before) != 1:
        raise SystemExit('NativeUfs source drift: SM8550 GCC probe block not found uniquely')
    source.write_text(text.replace(before, after))

    # This phone keeps the UniXNU ESP in userdata and has a separate darwin
    # partition for the iPadOS root image. Never make the root driver consume
    # the ESP partition as its SHNGROOT store.
    layout = source_dir / 'RootLayout.h'
    layout_text = layout.read_text()
    before = 'static const char wanted[] = "userdata";'
    if layout_text.count(before) != 1:
        raise SystemExit('NativeUfs source drift: root partition selector not found uniquely')
    layout.write_text(layout_text.replace(before, 'static const char wanted[] = "darwin";'))

    mmio = source_dir / 'GccUfs.h'
    mmio_text = mmio.read_text()
    before = '#define UFS_HC_SIZE                   0x3000ULL'
    if mmio_text.count(before) != 1:
        raise SystemExit('NativeUfs source drift: UFSHCI register window not found uniquely')
    mmio.write_text(mmio_text.replace(before, '#define UFS_HC_SIZE                   0x2500ULL'))
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component', choices=['ArmDTPlatform', 'ArmCpu', 'NativeUfs'])
    args = parser.parse_args()
    if args.component == 'NativeUfs' and os.environ.get('XNU_NATIVE_UFS') != '1':
        print('NativeUfs: skipped (CONFIG_XNU_NATIVE_UFS is disabled)')
        return
    out = ROOT / 'build' / {'ArmCpu': 'armcpu', 'ArmDTPlatform': 'armdt-platform',
                            'NativeUfs': 'native-ufs'}[args.component]
    out.mkdir(parents=True, exist_ok=True)
    recipe = ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json'
    record = json.loads(recipe.read_text().strip().rstrip(','))
    source = (polaris_native_ufs_source(out) if args.component == 'NativeUfs' else
              ROOT / 'drivers' / args.component / (args.component + '.cpp'))
    destination = out / (args.component + '.o')
    command = []
    arguments = record['arguments']
    index = 0
    while index < len(arguments):
        value = arguments[index]
        if value == record['file']:
            command.append(str(source))
        elif value == '-o':
            command.extend(['-o', str(destination)])
            index += 1
        elif value == '-Xclang' and arguments[index + 1].startswith('-W'):
            index += 1
        elif not value.startswith('-W'):
            command.append(value)
        index += 1
    command.extend(['-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    '-Wno-c99-designator', '-Wno-reorder-init-list',
                    '-Wno-deprecated-enum-enum-conversion',
                    '-I', str(ROOT / '.cache/xnu-work/osfmk')])
    if args.component == 'NativeUfs':
        command.extend(['-I', str(source.parent),
                        '-I', str(ROOT / 'build/storage-abi/include'),
                        '-DNATIVE_UFS_POLARIS=1', '-mgeneral-regs-only'])
    (out / 'compile-command.json').write_text(json.dumps(command, indent=2) + '\n')
    result = subprocess.run(command, cwd=record['directory'], capture_output=True, text=True)
    (out / 'compile.log').write_text(result.stdout + result.stderr)
    if result.returncode:
        raise SystemExit(result.stdout + result.stderr)
    data = destination.read_bytes()
    if struct.unpack_from('<4I', data) != (0xfeedfacf, 0x100000c, 0, 1):
        raise SystemExit('Expected a plain ARM64 Mach-O object')
    offset = 32
    deployment = None
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        cmd, length = struct.unpack_from('<II', data, offset)
        if cmd == 0x32:
            deployment = struct.unpack_from('<II', data, offset + 8)
        offset += length
    if deployment != (2, 0x110000):
        raise SystemExit(f'Expected iOS 17 deployment, got {deployment}')
    archive = out / ('lib' + args.component + '.a')
    archive.unlink(missing_ok=True)
    subprocess.run(['xcrun', 'ar', 'rcs', archive, destination], check=True)
    print(f'{args.component}: ARM64/iOS 17 archive built')


if __name__ == '__main__':
    main()
