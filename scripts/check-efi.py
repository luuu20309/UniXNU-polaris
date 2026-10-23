#!/usr/bin/env python3
"""Build UniXNU and the polaris DarwinBoot override, then exercise them in QEMU.

Requires Linux with the AArch64 GNU cross compiler, LLVM tools, QEMU and
AAVMF, plus the pinned UniXNU and Mu_Basecore checkouts. No fetch, fastboot,
ADB, real disks or firmware-variable files are used. QEMU uses read-only
ESP directories and a temporary copy of its own variables image.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(command, log, *, env=None, timeout=300):
    with log.open('w') as stream:
        result = subprocess.run([str(item) for item in command], stdout=stream,
                                stderr=subprocess.STDOUT, env=env, timeout=timeout)
    if result.returncode:
        print('\n'.join(log.read_text(errors='replace').splitlines()[-25:]))
        raise SystemExit(f'Failed: {log}')


def pin(path, wanted):
    actual = subprocess.check_output(['git', '-C', path, 'rev-parse', 'HEAD'], text=True).strip()
    if actual != wanted:
        raise SystemExit(f'{path}: expected revision {wanted}, got {actual}')


def copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--edk2', type=Path)
    parser.add_argument('--out', type=Path, default=ROOT / 'out/efi-check')
    args = parser.parse_args()
    base = args.base.resolve()
    edk2 = (args.edk2 or base / 'package/Mu_Basecore').resolve()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    pins = json.loads((ROOT / 'upstream.json').read_text())
    pin(base, pins['UniXNU']['commit'])
    pin(edk2, pins['Mu_Basecore']['commit'])
    env = dict(os.environ, JOBS='8', UNIXNU_EDK2=str(edk2), UNIXNU_BUILD_DIR=str(out / 'efi'))
    run(['bash', base / 'package/build.sh', '-b', 'DEBUG', '-t', 'GCC'], out / 'build-unixnu.log', env=env)
    print('PASS: UniXNU EFI applications and drivers compile', flush=True)
    workspace = out / 'loader'
    workspace.mkdir(exist_ok=True)
    tools = edk2 / 'BaseTools'
    env.update(WORKSPACE=str(workspace),
               PACKAGES_PATH=os.pathsep.join(map(str, [ROOT / 'drivers/DarwinBoot', base / 'package', edk2])),
               EDK_TOOLS_PATH=str(tools), CONF_PATH=str(out / 'efi/Build/Conf'),
               PYTHON_COMMAND=sys.executable, GCC_AARCH64_PREFIX='aarch64-linux-gnu-',
               PATH=os.pathsep.join(map(str, [tools / 'BinWrappers/PosixLike', tools / 'Source/C/bin', os.environ['PATH']])))
    run([tools / 'BinWrappers/PosixLike/build', '-p', 'DarwinBootPkg/DarwinBoot.dsc',
         '-a', 'AARCH64', '-b', 'DEBUG', '-t', 'GCC', '-n', '8'], out / 'build-loader.log', env=env)
    print('PASS: polaris DarwinBoot EFI driver compiles', flush=True)
    binary = out / 'efi/Build/Out/AARCH64-DEBUG'
    loader = workspace / 'build/DarwinBoot/DEBUG_GCC/AARCH64/DarwinBoot.efi'
    fixtures = out / 'fixtures'
    run([sys.executable, ROOT / 'scripts/make-loader-fixtures.py', '--output', fixtures], out / 'fixtures.log')
    runner = base / 'package/Scripts/qemu-run.py'
    results = []

    def qemu(name, esp, expectations, rejection):
        command = [sys.executable, runner, esp, '--log', out / (name + '.log'),
                   '--finish-on-expect', '--timeout', '50']
        for expected in expectations:
            command += ['--expect', expected]
        command += ['--reject', rejection]
        run(command, out / (name + '-runner.log'), timeout=60)
        results.append(name)
        print(f'PASS: QEMU {name}', flush=True)

    esp = out / 'smoke-esp'
    copy(binary / 'UniXNU.efi', esp / 'EFI/BOOT/BOOTAA64.EFI')
    for name in ['SampleDriver', 'HelloPayload']:
        copy(binary / (name + '.efi'), esp / ('EFI/UniXNU/Payloads/' + name + '.efi'))
    copy(ROOT / 'tests/efi-smoke/unixnu.toml', esp / 'EFI/UniXNU/unixnu.toml')
    qemu('efi-smoke', esp, ['UNIXNU: menu: shown', 'UNIXNU-HELLO: check driver PASS',
                           'UNIXNU-HELLO: RESULT PASS 1 passed 0 failed'], 'Not Found')
    cases = ['allowed', 'absent', 'unavailable', 'bad-size', 'duplicate', 'unaligned', 'empty', 'overflow', 'misplaced']
    for name in cases:
        esp = out / ('esp-' + name)
        copy(binary / 'UniXNU.efi', esp / 'EFI/BOOT/BOOTAA64.EFI')
        copy(loader, esp / 'EFI/UniXNU/Payloads/DarwinBoot.efi')
        copy(fixtures / (name + '.macho'), esp / 'EFI/UniXNU/Payloads/kernel.macho')
        copy(fixtures / (name + '.macho.adt'), esp / 'EFI/UniXNU/Payloads/kernel.macho.adt')
        (esp / 'EFI/UniXNU/unixnu.toml').write_text(
            'version = 1\n[boot]\ntimeout = 0\n[kits]\nautoload = false\n'
            '[[driver]]\npath = "Payloads/DarwinBoot.efi"\n'
            '[[entry]]\nid = "fixture"\ntitle = "QEMU loader fixture"\ntype = "xnu"\n'
            'path = "Payloads/kernel.macho"\nargs = "polaris.fixture=1"\n')
        if name in ['allowed', 'absent']:
            expectations = ['POLARIS-LOADER-FIXTURE: constrained range and Darwin23 boot_args PASS']
            rejection = 'POLARIS-LOADER-FIXTURE: FAIL|boot refused:'
        else:
            status = 'Out of Resources' if name == 'unavailable' else 'Compromised Data'
            expectations = [f'DARWINBOOT: boot refused: {status}; firmware retained']
            rejection = 'POLARIS-LOADER-FIXTURE:|inputs validated; exiting boot services'
        qemu('loader-' + name, esp, expectations, rejection)
    report = {'qemu_cases': results, 'physical_polaris_booted': False,
              'unixnu_revision': pins['UniXNU']['commit'], 'edk2_revision': pins['Mu_Basecore']['commit'],
              'binary_sha256': {name: hashlib.sha256(path.read_bytes()).hexdigest()
                                for name, path in [('UniXNU.efi', binary / 'UniXNU.efi'), ('DarwinBoot.efi', loader)]}}
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print('PASS: EFI build and QEMU handoff/refusal tests; physical polaris boot remains pending')


if __name__ == '__main__':
    main()
