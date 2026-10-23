#!/usr/bin/env python3
"""Run the polaris host checks: ADT protocol tests, ArmCpu timer property
validation and the DarwinBoot allocation-window checks.

Compiles the real C/C++ test sources against the in-tree driver headers with
the host toolchain. No GPU kit is present at this platform stage, so there is
no HFI component here (unlike the sweet host check). Nothing on this host
touches the phone, performs MMIO or proves firmware execution.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(command):
    result = subprocess.run([str(item) for item in command], capture_output=True, text=True, timeout=60)
    if result.returncode:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(result.returncode)
    if result.stdout:
        print(result.stdout, end='')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'out/host-tests')
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    clang = shutil.which('clang')
    clangxx = shutil.which('clang++')
    if not clang or not clangxx:
        parser.error('clang and clang++ are required')
    host = ['-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
            '-fno-sanitize-recover=all']
    if shutil.which('ld.lld'):
        host += ['-fuse-ld=lld']
    run([sys.executable, '-m', 'unittest', 'discover', '-s', ROOT / 'tests', '-v'])
    timer = out / 'timer-test'
    run([clangxx, '-std=c++17', *host, '-I', ROOT / 'drivers/ArmCpu', ROOT / 'tests/timer.cpp', '-o', timer])
    run([timer])
    print('PASS: timer property validation, PPI masks, invalid inputs and unaligned cells')
    window = out / 'window-range-test'
    run([clang, '-std=c11', *host, '-I', ROOT / 'drivers/DarwinBoot/DarwinBootPkg/Driver',
         ROOT / 'tests/window-range.c', '-o', window])
    run([window])
    report = {
        'suites': ['adt', 'timer', 'loader-window'],
        'adt_tool_sha256': hashlib.sha256(
            (ROOT / 'scripts/make-polaris-adt.py').read_bytes()).hexdigest(),
        'native_code_executed': False,
        'hardware_mmio_performed': False,
    }
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print('PASS: polaris host checks; no device access or MMIO performed')


if __name__ == '__main__':
    main()
