#!/usr/bin/env python3
"""Build the sheng PMGR integration archive.

AMD's target AMFI panics with "AMFI: No PMGR?" unless an ApplePMGRNub service
exists. ApplePMGR publishes one only from an AppleARMIODevice provider, and
ArmDTPlatform publishes arm-io children as IOPlatformDevice, so the node alone
can never match. ShengPlatformPower.cpp publishes a real but provider-less
ApplePMGRNub from the sheng_pmgr_run() hook instead.

build-xnu-4k-baseline.sh force-loads build/sheng-platform-power/libShengPlatformPower.a
when XNU_SHENG_PMGR=1, and bsd_init.c calls sheng_pmgr_run() when sheng-pmgr=2.
"""
import json
from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'scripts/sheng-platform-power/ShengPlatformPower.cpp'


def main():
    if not SOURCE.is_file():
        raise SystemExit(f'missing source: {SOURCE}')
    # Reuse the exact compile command XNU used for a kernel C++ file, so the
    # archive sees the same headers, target triple and feature macros.
    record_path = ROOT / ('build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/'
                          'iokit/DEVELOPMENT/IOPlatformExpert.cpo.json')
    record = json.loads(record_path.read_text().strip().rstrip(','))
    out = ROOT / 'build/sheng-platform-power'
    out.mkdir(parents=True, exist_ok=True)
    obj = out / 'ShengPlatformPower.o'

    command = []
    arguments = record['arguments']
    index = 0
    while index < len(arguments):
        value = arguments[index]
        if value == record['file']:
            command.append(str(SOURCE))
        elif value == '-o':
            command.extend(['-o', str(obj)])
            index += 1
        elif value == '-Xclang' and arguments[index + 1].startswith('-W'):
            index += 1
        elif not value.startswith('-W'):
            command.append(value)
        index += 1
    command.extend(['-I', str(ROOT / 'build/storage-abi/include'),
                    '-I', str(ROOT / '.cache/xnu-work/osfmk'),
                    '-mgeneral-regs-only'])

    (out / 'compile-command.json').write_text(json.dumps(command, indent=2) + '\n')
    result = subprocess.run(command, cwd=record['directory'], capture_output=True, text=True)
    (out / 'compile.log').write_text(result.stdout + result.stderr)
    if result.returncode:
        raise SystemExit(result.stdout + result.stderr)

    data = obj.read_bytes()
    if struct.unpack_from('<4I', data) != (0xfeedfacf, 0x100000c, 0, 1):
        raise SystemExit('Expected an ordinary ARM64 Mach-O object')

    archive = out / 'libShengPlatformPower.a'
    archive.unlink(missing_ok=True)
    subprocess.run(['xcrun', 'ar', 'rcs', archive, obj], check=True)
    print(f'ShengPlatformPower: ARM64 archive built at {archive}')


if __name__ == '__main__':
    main()
