#!/usr/bin/env python3
"""Assemble the polaris payload ESP directory for a UEFI firmware (route A).

Lays out exactly what UniXNU.efi and DarwinBoot expect on the ESP:
  EFI/BOOT/BOOTAA64.EFI                UniXNU.efi from the EFI build
  EFI/UniXNU/Payloads/DarwinBoot.efi   loader driver from the EFI build
  EFI/UniXNU/Payloads/kernel.macho     the source kernel artifact
  EFI/UniXNU/Payloads/kernel.macho.adt the polaris platform ADT
  EFI/UniXNU/unixnu.toml               menu entry from board.toml

Host-side only. Verifies the kernel against its build report, runs the
fail-closed CPU prefix check over the generated ADT and command line, and
writes nothing outside --out. The directory can be mounted/copied to any FAT
ESP; packing it into a filesystem image is left to the environment (mtools,
mkfs.vfat) and is done in CI.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, required=True,
                        help='kernel.macho with its kernel.json beside it')
    parser.add_argument('--efi', type=Path, required=True,
                        help='EFI build output (efi/Build/... and loader/build/...), as produced by check-efi.py')
    parser.add_argument('--out', type=Path, default=ROOT / 'out/polaris-esp')
    parser.add_argument('--ram', type=int, choices=(6, 8), default=6)
    parser.add_argument('--cpus', type=int, default=1,
                        help='CPUs described by the ADT and requested by the command line (1..8)')
    parser.add_argument('--owned-memory-size', type=lambda v: int(v, 0), default=0x40000000)
    parser.add_argument('--no-probe', action='store_true',
                        help='skip the boot-progress/panic probes on the ESP kernel copy')
    parser.add_argument('--probe-extra', default='',
                        help='extra patch-xnu-entry-probe arguments (default: --no-reset); e.g. --reset-after _PE_init_platform --in _arm_init --reset-delay 8')
    args = parser.parse_args()
    if not 1 <= args.cpus <= 8:
        parser.error('--cpus must be 1..8')

    kernel = args.kernel.resolve()
    data = kernel.read_bytes()
    report = json.loads(kernel.with_suffix('.json').read_text())
    if report.get('kernel_sha256') != hashlib.sha256(data).hexdigest():
        parser.error('kernel.macho does not match its build report')
    if report.get('original_components_prelinked') is not False:
        parser.error('route A expects the source kernel without original components')

    efi = args.efi.resolve()
    binaries = [(efi / 'efi/Build/Out/AARCH64-DEBUG/UniXNU.efi', 'EFI/BOOT/BOOTAA64.EFI'),
                (efi / 'loader/build/DarwinBoot/DEBUG_GCC/AARCH64/DarwinBoot.efi',
                 'EFI/UniXNU/Payloads/DarwinBoot.efi'),
                (kernel, 'EFI/UniXNU/Payloads/kernel.macho')]
    for source, _ in binaries:
        if not source.is_file():
            parser.error(f'missing EFI input: {source}')

    board = tomllib.loads((ROOT / 'board.toml').read_text(encoding='utf-8'))
    payload = board['payload']
    kernel_args = list(payload['kernel_args'])
    if any(value.startswith('cpus=') for value in kernel_args):
        kernel_args = [f'cpus={args.cpus}' if value.startswith('cpus=') else value
                       for value in kernel_args]
    else:
        kernel_args.append(f'cpus={args.cpus}')

    adt_tool = load_module('polaris_adt', ROOT / 'scripts/make-polaris-adt.py')
    prefix = load_module('cpu_prefix', ROOT / 'scripts/check-cpu-prefix.py')
    adt = adt_tool.build(cpu_count=args.cpus,
                         dram_size=adt_tool.DRAM_SIZE_6 if args.ram == 6 else adt_tool.DRAM_SIZE_8,
                         owned_memory_size=args.owned_memory_size)
    try:
        prefix.validate(adt, ' '.join(kernel_args))
    except ValueError as error:
        parser.error(f'CPU prefix check failed: {error}')

    out = args.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    for source, relative in binaries:
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    # Patch the ESP COPY of the kernel (never the artifact) with the boot-progress
    # probes: stage bars for the early MMU milestones plus the on-screen panic
    # funnel. With no serial console on this phone these are the only early-boot
    # signals; the timer heartbeat covers the IOKit stage.
    if not args.no_probe:
        import shlex
        import subprocess as sp
        probe = ROOT / 'scripts/patch-xnu-entry-probe.py'
        sp.run([sys.executable, str(probe), out / 'EFI/UniXNU/Payloads/kernel.macho',
                '--panic-screen', *shlex.split(args.probe_extra or '--no-reset')], check=True)
        # The probes rewrite __text, which invalidates the sealed FIPS POST
        # integrity HMAC the base build wrote into __fips_hmacs. Re-run the
        # seal over the patched copy so corecrypto's integrity POST sees the
        # image it is actually measuring (no selftest is skipped or weakened).
        sp.run([sys.executable, str(ROOT / 'scripts/seal-source-crypto-kernel.py'),
                out / 'EFI/UniXNU/Payloads/kernel.macho'], check=True)
    (out / 'EFI/UniXNU/Payloads/kernel.macho.adt').write_bytes(adt)
    (out / 'EFI/UniXNU/unixnu.toml').write_text(
        'version = 1\n'
        '[boot]\n'
        f'timeout = {int(payload.get("timeout", 3))}\n'
        '[kits]\n'
        'autoload = false\n'
        '[[driver]]\n'
        'path = "Payloads/DarwinBoot.efi"\n'
        '[[entry]]\n'
        'id = "platform"\n'
        f'title = "{payload["title"]}"\n'
        'type = "xnu"\n'
        'path = "Payloads/kernel.macho"\n'
        f'args = "{" ".join(kernel_args)}"\n', encoding='utf-8')
    (out / 'esp.json').write_text(json.dumps({
        'kernel_sha256': report['kernel_sha256'],
        'kernel_bytes': len(data), 'cpus': args.cpus, 'ram_gib': args.ram,
        'owned_memory_size': args.owned_memory_size,
        'kernel_args': kernel_args, 'cpu_prefix_checked': True,
        'sources': report.get('sources', {}),
    }, indent=2) + '\n', encoding='utf-8')
    print(f'{out}: payload ESP assembled; cpus={args.cpus}, ram={args.ram} GiB; host-verified only')


if __name__ == '__main__':
    main()
