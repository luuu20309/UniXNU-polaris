#!/usr/bin/env python3
"""Build the Darwin 23 source-kernel platform milestone for polaris on macOS/Xcode.

Same two-pass scheme as the sweet platform milestone: the first pass creates
real XNU compile records and the source symbol inventory (its link may fail);
the selected source archives are then built, the bootstrap objects discarded,
and the final configured link is required to succeed. No original kernelcache,
IPSW, userdata image or phone is used. The output is a source-kernel bring-up
artifact for polaris, not an iOS image.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--out', type=Path, default=ROOT / 'out/native')
    parser.add_argument('-j', '--jobs', type=int, default=4)
    parser.add_argument('--defconfig', default='polaris_defconfig',
                        help='configs/ name; a [kernel.prelink] section additionally prelinks against the IPSW kernelcache')
    args = parser.parse_args()
    if platform.system() != 'Darwin' or not shutil.which('xcodebuild'):
        parser.error('this step requires macOS with Xcode and the iPhoneOS SDK')
    base = args.base.resolve()
    pins = json.loads((ROOT / 'upstream.json').read_text())
    revision = subprocess.check_output(['git', '-C', base, 'rev-parse', 'HEAD'], text=True).strip()
    if revision != pins['UniXNU']['commit']:
        parser.error('the base checkout does not match upstream.json')
    # These are the only common submodules this source-kernel milestone uses.
    subprocess.run(['git', '-C', base, 'submodule', 'update', '--init', '--depth', '1',
                    'drivers/ArmDTPlatform', 'kits/NativePrelink'], check=True)
    sys.path.insert(0, str(base / 'scripts'))
    from unixnu import cli, feeds, kernel, tree
    from unixnu.config import Context, parse_config
    from unixnu.util import Error, run
    options = cli.parser().parse_args(['build', 'kernel', '-D', str(ROOT), '--out', str(args.out.resolve()), '-j', str(args.jobs)])
    ctx = Context(options)
    ctx.work.mkdir(parents=True, exist_ok=True)
    config = ROOT / 'configs' / args.defconfig
    ctx.config = parse_config(config.read_text())
    shutil.copyfile(config, ctx.config_path)
    modules = feeds.resolve(ctx)
    references = set(cli.REFERENCES['kernel'])
    feeds.fetch(ctx, modules, only=references)
    tree.assemble(ctx, modules)
    # UniXNU resolves patch sets as whole directories. Materialize the pinned
    # common set and overlay the reviewed fixes without editing its checkout
    # or accidentally discarding all the other common XNU patches.
    patch_link = ctx.tree / 'patches/xnu'
    patch_copy = ctx.work / 'xnu-patches'
    if patch_copy.exists():
        shutil.rmtree(patch_copy)
    shutil.copytree(modules[('patch', 'xnu')].path, patch_copy)
    for override in (ROOT / 'kernel-overrides/xnu').glob('*.patch'):
        shutil.copyfile(override, patch_copy / override.name)
    patch_link.unlink()
    patch_link.symlink_to(patch_copy.resolve())
    # The pinned base's prepare-xnu-source.py lists headers through the GitHub
    # contents API, which hosted CI runners exhaust (HTTP 403 rate limit, seen
    # twice on the macOS runner). Overlay a git-only equivalent: same interface
    # and destinations, but it restores the headers through the git smart-HTTP
    # protocol, which has no REST rate limit.
    shutil.copyfile(ROOT / 'scripts/overlay/prepare-xnu-source.py',
                    ctx.tree / 'scripts/prepare-xnu-source.py')
    env = {key: value for key, value in os.environ.items() if not key.startswith(('XNU_', 'UNIXNU_'))}
    env.update(kernel.xnu_env(ctx))
    env.update(kernel.version_env(modules))
    env.update(JOBS=str(args.jobs), PATH=kernel.toolchain_path(), MAKEFLAGS='-k')
    script = ctx.tree / 'scripts/build-xnu-4k-baseline.sh'
    obj = ctx.tree / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'
    bootstrap = dict(env, XNU_SOURCE_CORECRYPTO14='0', XNU_SOURCE_PTHREAD='0',
                     XNU_ARM_DT_PLATFORM='0', XNU_ARMCPU='0', XNU_NATIVE_ARCH_TIMER='0',
                     XNU_SOURCE_MOCKFS='0', XNU_BOOT_PROGRESS='0')
    print('Bootstrap pass: compiling XNU records before external archives', flush=True)
    bootstrap_failed = False
    try:
        run(['bash', script], cwd=ctx.tree, env=bootstrap, log_file=ctx.logs / 'bootstrap.log')
    except Error:
        bootstrap_failed = True
    required = ['link.filelist', 'iokit/DEVELOPMENT/IOPlatformExpert.cpo.json',
                'osfmk/DEVELOPMENT/prng_random.o.json', 'osfmk/DEVELOPMENT/startup.o.json']
    missing = [name for name in required if not (obj / name).is_file()]
    if missing:
        # The recursive make tail can hide the first generator failure behind
        # thousands of unrelated warnings. Keep the full log and expose causes.
        log = ctx.logs / 'bootstrap.log'
        if log.is_file():
            errors = [line for line in log.read_text(errors='replace').splitlines()
                      if 'error:' in line or 'No rule to make target' in line]
            print('\n'.join(errors[:20]), file=sys.stderr)
        raise SystemExit('Bootstrap did not produce required XNU records: ' + ', '.join(missing))
    kernel.build_archives(ctx, env)
    # Never reuse an object compiled with the bootstrap feature selection.
    # Keep the compile records and generated headers for the archive recipes.
    for path in obj.rglob('*'):
        if path.is_file() and (path.suffix in ['.o', '.cpo'] or path.name.startswith('mach.development.')):
            path.unlink()
    print('Final pass: require the full selected source-kernel link', flush=True)
    run(['bash', script], cwd=ctx.tree, env=env, log_file=ctx.logs / 'kernel.log')
    source = obj / 'mach.development.bcm2837.unstripped.noctf'
    data = source.read_bytes()
    if struct.unpack_from('<4I', data) != (0xfeedfacf, 0x100000c, 0, 2):
        raise SystemExit('Expected an ordinary plain ARM64 source kernel')
    destination = ctx.work / 'kernel/kernel.macho'
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    prelinked = None
    if ctx.board.get('kernel', {}).get('prelink'):
        print('Prelink pass: composing with the original iPadOS 17 kernelcache', flush=True)
        prelinked = kernel.prelink(ctx)
        print(f'PASS: prelinked kernel {prelinked} sha256 '
              f'{hashlib.sha256(prelinked.read_bytes()).hexdigest()}', flush=True)
    report = {'kernel_sha256': hashlib.sha256(data).hexdigest(), 'kernel_bytes': len(data),
              'unixnu_revision': revision, 'bootstrap_link_failed': bootstrap_failed,
              'final_configured_link_passed': True,
              'original_components_prelinked': prelinked is not None,
              'hardware_booted': False, 'userland_booted': False,
              'sources': {name: module.commit for (_, name), module in modules.items() if module.commit}}
    destination.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'PASS: source-kernel platform artifact {destination}; no physical boot claimed')


if __name__ == '__main__':
    main()
