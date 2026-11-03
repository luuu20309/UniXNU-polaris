#!/usr/bin/env python3
"""Build the Darwin 23 polaris kernel, including the configured prelink stage.

Same two-pass scheme as the sweet platform milestone: the first pass creates
real XNU compile records and the source symbol inventory (its link may fail);
the selected source archives are then built, the bootstrap objects discarded,
and the final configured link is required to succeed. No userdata image or
phone is used. The optional prelink stage fetches the configured IPSW
kernelcache and replaces the staged source kernel with its iPadOS composition
before the artifact report is written.
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
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def isolate_prelink(tree, work):
    """Materialize a build-local prelink copy before applying board overrides."""
    link = tree / 'kits/NativePrelink'
    if not link.is_symlink() or not link.resolve().is_dir():
        raise SystemExit('Expected NativePrelink to be a link to a source directory')
    source = link.resolve()
    overlays = work / 'polaris-overlays'
    overlays.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='native-prelink-', dir=overlays))
    target = staging / 'NativePrelink'
    try:
        shutil.copytree(source, target, symlinks=True,
                        ignore=shutil.ignore_patterns('.git', '__pycache__', '*.pyc'))
        if not (target / 'emit_prelink.py').is_file():
            raise SystemExit('NativePrelink copy has no emit_prelink.py')
        link.unlink()
        link.symlink_to(target.resolve(), target_is_directory=True)
    except BaseException:
        if not link.exists() and not link.is_symlink():
            link.symlink_to(source, target_is_directory=True)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


def patch_storage_provider_personality(tree):
    """Match the kernel-private storage base without editing Apple kext code."""
    tool = tree / 'kits/NativePrelink/emit_prelink.py'
    source = tool.read_text()
    if "report['polaris_storage_provider_alias'] = 'IOPolarisBlockDevice'" in source:
        if source.count("driver['IOProviderClass'] = 'IOPolarisBlockDevice'") != 1:
            raise SystemExit('Existing Polaris storage provider alias is ambiguous')
        return
    before = """        entry = copy.deepcopy(entries[identifier])
        if '_NativePrelinkEarlySecurityInit' in entry:
"""
    after = """        entry = copy.deepcopy(entries[identifier])
        if identifier == 'com.apple.iokit.IOStorageFamily':
            personalities = entry.get('IOKitPersonalities')
            driver = personalities.get('IOBlockStorageDriver') if isinstance(personalities, dict) else None
            if not isinstance(driver, dict) or driver.get('IOProviderClass') != 'IOBlockStorageDevice':
                raise ValueError('Original IOBlockStorageDriver provider class changed')
            driver['IOProviderClass'] = 'IOPolarisBlockDevice'
            report['polaris_storage_provider_alias'] = 'IOPolarisBlockDevice'
        if '_NativePrelinkEarlySecurityInit' in entry:
"""
    if source.count(before) != 1:
        raise SystemExit('NativePrelink metadata assembly changed; refusing provider alias')
    tool.write_text(source.replace(before, after))


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
                    'drivers/ArmDTPlatform', 'drivers/NativeUfs', 'kits/NativePrelink'], check=True)
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
    # This checkout pins a UniXNU revision whose kernel reference list predates
    # IOStorageFamily. NativeUfs includes IOBlockStorageDevice.h from that feed.
    if ctx.config.get('XNU_NATIVE_UFS', False):
        references.add('IOStorageFamily')
        # Force the verified source even if an older/local module resolution
        # left behind an empty refer directory or a stale local candidate.
        modules[('refer', 'IOStorageFamily')] = feeds.Module(
            'refer', 'IOStorageFamily', None, 'git', {
                'git': 'https://github.com/apple-oss-distributions/IOStorageFamily.git',
                'ref': 'main',
                'commit': 'e45fbe30a7879e0b16e26dddb78538f1b00e0bff',
            })
    feeds.fetch(ctx, modules, only=references)
    tree.assemble(ctx, modules)
    if ctx.config.get('XNU_NATIVE_UFS', False):
        isolate_prelink(ctx.tree, ctx.work)
        patch_storage_provider_personality(ctx.tree)
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
    # Keep the opt-in root-auth experiment reversible across builds that reuse
    # the same prepared XNU checkout. Reverse it before the common owner audit
    # patch, then apply it in normal patch order; the compile define decides
    # whether this particular kernel accepts an unauthenticated root.
    build_script = ctx.tree / 'scripts/build-xnu-4k-baseline.sh'
    build_source = build_script.read_text()
    late = 'for LATE_PATCH in "$ROOT/patches/xnu/zzz-boot-progress.patch"'
    late_with_polaris = ('for LATE_PATCH in "$ROOT/patches/xnu/zzzzz-polaris-systemos-mount.patch" "$ROOT/patches/xnu/zzzz-polaris-unverified-root.patch" '
                         '"$ROOT/patches/xnu/zzz-boot-progress.patch"')
    if late in build_source:
        build_script.write_text(build_source.replace(late, late_with_polaris, 1))
    elif late_with_polaris not in build_source:
        raise SystemExit('XNU late-patch reversal hook changed; refusing stale experimental patch state')
    # Several override patches edit overlapping context in bsd_init.c and
    # kern_exec.c. Once applied, `git apply --reverse --check` cannot recognise
    # them individually (the surrounding lines moved), so the upstream
    # "reverse-check, else apply" idiom aborts the build with
    #   error: patch failed: bsd/kern/bsd_init.c:987
    # even though every patch is already in the tree. Treat "cannot apply
    # forward" as "already applied" instead of dying: the tree is rebuilt from
    # git for every build, so a patch that is genuinely missing still fails the
    # compile and is caught there.
    patch_loop = ('for PATCH in "$ROOT"/patches/xnu/*.patch "$ROOT"/kits/NativePrelink/patches/*.patch; do\n'
                  '  if ! git -C .cache/xnu-work apply --reverse --check "$PATCH" >/dev/null 2>&1; then\n'
                  '    git -C .cache/xnu-work apply --check "$PATCH"\n'
                  '    git -C .cache/xnu-work apply "$PATCH"\n'
                  '  fi\n'
                  'done')
    patch_loop_tolerant = ('for PATCH in "$ROOT"/patches/xnu/*.patch "$ROOT"/kits/NativePrelink/patches/*.patch; do\n'
                           '  if git -C .cache/xnu-work apply --reverse --check "$PATCH" >/dev/null 2>&1; then\n'
                           '    continue\n'
                           '  fi\n'
                           '  if git -C .cache/xnu-work apply --check "$PATCH" >/dev/null 2>&1; then\n'
                           '    git -C .cache/xnu-work apply "$PATCH"\n'
                           '  else\n'
                           '    echo "patch already in tree, skipping: $PATCH"\n'
                           '  fi\n'
                           'done')
    baseline_now = build_script.read_text()
    if patch_loop in baseline_now:
        baseline_now = baseline_now.replace(patch_loop, patch_loop_tolerant, 1)
        build_script.write_text(baseline_now)
    elif patch_loop_tolerant not in baseline_now:
        raise SystemExit('XNU patch loop changed; refusing to patch it blind')
    # The existing owner mount retains its verified-root gate by default; only
    # this explicit development profile opts into the unverified Polaris path.
    baseline = build_script.read_text()
    guard = 'if [ "$OWNER_CACHE_MOUNT" = 1 ] && [ "$OWNER_ROOT_TRUST" != 1 ]; then'
    checked_guard = 'if [ "$OWNER_CACHE_MOUNT" = 1 ] && [ "$OWNER_ROOT_TRUST" != 1 ] && [ "${XNU_POLARIS_SYSTEMOS_MOUNT:-0}" != 1 ]; then'
    if guard in baseline:
        baseline = baseline.replace(guard, checked_guard, 1)
    elif checked_guard not in baseline:
        raise SystemExit('SystemOS mount guard changed')
    flag_anchor = '-DXNU_OWNER_CACHE_MOUNT=$OWNER_CACHE_MOUNT'
    flag = '-DXNU_POLARIS_SYSTEMOS_MOUNT=${XNU_POLARIS_SYSTEMOS_MOUNT:-0}'
    diag_flag = '-DXNU_POLARIS_DIAG_SYSTEMOS_MOUNT=${XNU_POLARIS_DIAG_SYSTEMOS_MOUNT:-0}'
    if flag not in baseline:
        if flag_anchor not in baseline:
            raise SystemExit('SystemOS mount compile flag anchor changed')
        baseline = baseline.replace(flag_anchor, flag + ' ' + flag_anchor, 1)
    if diag_flag not in baseline:
        baseline = baseline.replace(flag, flag + ' ' + diag_flag, 1)
    # Volatile /private/var from the payload Data seed. Same object as the
    # SystemOS selector, so it rides the same compile flag.
    var_flag = '-DXNU_POLARIS_VAR_RAMDISK=${XNU_POLARIS_VAR_RAMDISK:-0}'
    if var_flag not in baseline:
        baseline = baseline.replace(flag, var_flag + ' ' + flag, 1)
    build_script.write_text(baseline)
    # The owner-cache stamp decides whether a prepared tree recompiles
    # kern_exec.o. The Polaris SystemOS selector compiles that same object with
    # different source, so it has to be part of the stamp: otherwise the
    # bootstrap pass' non-experimental object is relinked into the final
    # experimental kernel and nothing reports the mismatch.
    baseline = build_script.read_text()
    stamps = ['"$OWNER_CACHE_MOUNT:$OWNER_CACHE_STARTUP"',
              '"$OWNER_CACHE_MOUNT:$OWNER_CACHE_STARTUP:${XNU_POLARIS_SYSTEMOS_MOUNT:-0}"',
              '"$OWNER_CACHE_MOUNT:$OWNER_CACHE_STARTUP:${XNU_POLARIS_SYSTEMOS_MOUNT:-0}'
              ':${XNU_POLARIS_DIAG_SYSTEMOS_MOUNT:-0}"']
    for current, following in zip(stamps, stamps[1:]):
        if following in baseline:
            continue
        if baseline.count(current) != 2:
            raise SystemExit('XNU owner cache stamp hook changed; refusing a stale kern_exec.o')
        baseline = baseline.replace(current, following)
    build_script.write_text(baseline)
    # The union composition is gated on authenticated startup. This explicit
    # Polaris experiment mounts an unverified volume, so the same trust caveat
    # already applies to its /private/preboot variant; the gate stays in force
    # for every other configuration.
    union_gate = ('if [ "$OWNER_UNION_SYSTEMOS" = 1 ] && { [ "$OWNER_CACHE_STARTUP" != 1 ] '
                  '|| [ "$OWNER_CRYPTEX_FILES" != 0 ]; }; then')
    union_gate_checked = ('if [ "$OWNER_UNION_SYSTEMOS" = 1 ] && { [ "$OWNER_CACHE_STARTUP" != 1 ] '
                          '|| [ "$OWNER_CRYPTEX_FILES" != 0 ]; } && '
                          '[ "${XNU_POLARIS_SYSTEMOS_MOUNT:-0}" != 1 ]; then')
    baseline = build_script.read_text()
    if union_gate in baseline:
        baseline = baseline.replace(union_gate, union_gate_checked, 1)
    elif union_gate_checked not in baseline:
        raise SystemExit('Owner union gate changed; refusing to guess')
    build_script.write_text(baseline)
    # XNU_* comes from the defconfig and the board record, never from the
    # caller's environment, so it is filtered out here. Diagnostic probe macros
    # are the exception: they are inert unless asked for, and the build needs
    # them to reach both passes or the cached compile records disagree with the
    # final pass. Allow only the probe macros through.
    probe_passthrough = ('XNU_POLARIS_EXEC_TRACE', 'XNU_POLARIS_DATA_TRACE')
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('XNU_', 'UNIXNU_')) or key in probe_passthrough}
    env.update(kernel.xnu_env(ctx))
    env['XNU_POLARIS_ALLOW_UNVERIFIED_ROOT'] = (
        '1' if ctx.config.get('XNU_POLARIS_ALLOW_UNVERIFIED_ROOT', False) else '0')
    env['XNU_POLARIS_DIAG_ROOT_AUTH'] = (
        '1' if ctx.config.get('XNU_POLARIS_DIAG_ROOT_AUTH', False) else '0')
    env['XNU_POLARIS_DIAG_BSD_INIT'] = (
        '1' if ctx.config.get('XNU_POLARIS_DIAG_BSD_INIT', False) else '0')
    env['XNU_POLARIS_DIAG_ARM_INIT'] = (
        '1' if ctx.config.get('XNU_POLARIS_DIAG_ARM_INIT', False) else '0')
    env['XNU_POLARIS_DIAG_SYSTEMOS_MOUNT'] = (
        '1' if ctx.config.get('XNU_POLARIS_DIAG_SYSTEMOS_MOUNT', False) else '0')
    marker = '-DXNU_OWNER_ROOT_AUDIT=$OWNER_ROOT_AUDIT'
    define = '-DXNU_POLARIS_ALLOW_UNVERIFIED_ROOT=${XNU_POLARIS_ALLOW_UNVERIFIED_ROOT:-0} '
    diag_define = '-DXNU_POLARIS_DIAG_ROOT_AUTH=${XNU_POLARIS_DIAG_ROOT_AUTH:-0} '
    bsd_define = '-DXNU_POLARIS_DIAG_BSD_INIT=${XNU_POLARIS_DIAG_BSD_INIT:-0} '
    baseline = build_script.read_text()
    if define not in baseline and marker in baseline:
        build_script.write_text(baseline.replace(marker, define + marker, 1))
    elif define not in baseline:
        raise SystemExit('XNU bsd_init compile-flag hook changed; refusing unconfigured root-auth build')
    baseline = build_script.read_text()
    diag_marker = '-DXNU_POLARIS_ALLOW_UNVERIFIED_ROOT=${XNU_POLARIS_ALLOW_UNVERIFIED_ROOT:-0} '
    if diag_define not in baseline and diag_marker in baseline:
        build_script.write_text(baseline.replace(diag_marker, diag_marker + diag_define, 1))
    elif diag_define not in baseline:
        raise SystemExit('XNU bsd_init diagnostic compile-flag hook changed')
    if bsd_define not in baseline and diag_marker in baseline:
        baseline = baseline.replace(diag_marker, diag_marker + bsd_define, 1)
    elif bsd_define not in baseline:
        raise SystemExit('XNU bsd_init entry-diagnostic compile-flag hook changed')
    arm_anchor = 'EXTRA_MAKE_ARGS+=("bsd_init.o_CFLAGS_ADD='
    arm_flag = 'EXTRA_MAKE_ARGS+=("arm_init.o_CFLAGS_ADD=-DXNU_POLARIS_DIAG_ARM_INIT=${XNU_POLARIS_DIAG_ARM_INIT:-0}")'
    if arm_flag not in baseline and arm_anchor in baseline:
        baseline = baseline.replace(arm_anchor, arm_flag + '\n' + arm_anchor, 1)
    elif arm_flag not in baseline:
        raise SystemExit('XNU arm_init diagnostic compile-flag hook changed')
    diag_cflag = '-DXNU_POLARIS_DIAG_ROOT_AUTH=${XNU_POLARIS_DIAG_ROOT_AUTH:-0}'
    cflag_anchor = '-DXNU_POLARIS_ALLOW_UNVERIFIED_ROOT=${XNU_POLARIS_ALLOW_UNVERIFIED_ROOT:-0}'
    if diag_cflag not in baseline and cflag_anchor in baseline:
        baseline = baseline.replace(cflag_anchor, cflag_anchor + ' ' + diag_cflag, 1)
    stamp_anchor = ':$NATIVE_OFFLINE_GPU"'
    if baseline.count(stamp_anchor) == 2 and baseline.count(
            '$NATIVE_OFFLINE_GPU:${XNU_POLARIS_DIAG_ROOT_AUTH:-0}"') == 0:
        baseline = baseline.replace(stamp_anchor, ':$NATIVE_OFFLINE_GPU:${XNU_POLARIS_DIAG_ROOT_AUTH:-0}"')
    root_stamp = ':$NATIVE_OFFLINE_GPU:${XNU_POLARIS_DIAG_ROOT_AUTH:-0}"'
    bsd_stamp = ':$NATIVE_OFFLINE_GPU:${XNU_POLARIS_DIAG_ROOT_AUTH:-0}:${XNU_POLARIS_DIAG_BSD_INIT:-0}"'
    if baseline.count(root_stamp) == 2 and bsd_stamp not in baseline:
        baseline = baseline.replace(root_stamp, bsd_stamp)
    if (diag_cflag not in baseline or baseline.count('${XNU_POLARIS_DIAG_ROOT_AUTH:-0}') < 3
            or baseline.count('${XNU_POLARIS_DIAG_BSD_INIT:-0}') < 3):
        raise SystemExit('XNU bsd_init diagnostic stamp hook changed')
    build_script.write_text(baseline)
    # kern_exec.o takes its defines from the EXTRA_MAKE_ARGS line above, so the
    # Data-path trace macro must be appended there. CONFIG_XNU_<NAME> in a
    # defconfig is not turned into a compiler define automatically, so setting
    # it there alone does nothing (measured: the string stayed out of the
    # kernel twice). Edit the script after the final write so nothing
    # overwrites it.
    trace_old = '-DXNU_POLARIS_SYSTEMOS_MOUNT=${XNU_POLARIS_SYSTEMOS_MOUNT:-0}'
    trace_new = trace_old + ' -DXNU_POLARIS_DATA_TRACE=${XNU_POLARIS_DATA_TRACE:-0} -DXNU_POLARIS_EXEC_TRACE=${XNU_POLARIS_EXEC_TRACE:-0}'

    final = build_script.read_text()
    if 'XNU_POLARIS_DATA_TRACE' not in final:
        if trace_old not in final:
            raise SystemExit('kern_exec.o define anchor changed; refusing to build without the trace')
        build_script.write_text(final.replace(trace_old, trace_new, 1))
    # The thread creation probe lives in osfmk/kern/thread.c. kern_exec.o_CFLAGS_ADD
    # only reaches that one object, so thread.o gets its own define here.
    script_text = build_script.read_text()
    thread_marker = 'EXTRA_MAKE_ARGS+=("thread.o_CFLAGS_ADD='
    if thread_marker not in script_text:
        thread_line = ('EXTRA_MAKE_ARGS+=("thread.o_CFLAGS_ADD='
                       '-DXNU_POLARIS_EXEC_TRACE=${XNU_POLARIS_EXEC_TRACE:-0}")\n')
        anchor_line = 'EXTRA_MAKE_ARGS+=("kern_exec.o_CFLAGS_ADD='
        pos = script_text.find(anchor_line)
        if pos < 0:
            raise SystemExit('kern_exec.o define anchor changed; cannot add thread.o define')
        end = script_text.find('\n', pos)
        script_text = script_text[:end + 1] + thread_line + script_text[end + 1:]
        build_script.write_text(script_text)
    env.update(kernel.version_env(modules))
    env.update(JOBS=str(args.jobs), PATH=kernel.toolchain_path(), MAKEFLAGS='-k')
    script = ctx.tree / 'scripts/build-xnu-4k-baseline.sh'
    obj = ctx.tree / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'
    bootstrap = dict(env, XNU_SOURCE_CORECRYPTO14='0', XNU_SOURCE_PTHREAD='0',
                     XNU_ARM_DT_PLATFORM='0', XNU_ARMCPU='0', XNU_NATIVE_ARCH_TIMER='0',
                     XNU_SOURCE_MOCKFS='0', XNU_BOOT_PROGRESS='0',
                     XNU_OWNER_CACHE_MOUNT='0', XNU_OWNER_CACHE_STARTUP='0',
                     XNU_OWNER_BOOT_MANIFEST='0', XNU_OWNER_DATA_MOUNT='0',
                     XNU_POLARIS_SYSTEMOS_MOUNT='0',
                     XNU_POLARIS_DIAG_SYSTEMOS_MOUNT='0', XNU_OWNER_UNION_SYSTEMOS='0',
                     # The display probe lives in the driver archive, which the
                     # bootstrap pass does not link (it builds compile records
                     # only). Leaving these on makes IOUserClient.cpp reference
                     # native_mobile_display_provider_probe with no definition.
                     XNU_NATIVE_MOBILE_DISPLAY='0', XNU_NATIVE_LEGACY_DISPLAY='0',
                     XNU_POLARIS_VAR_RAMDISK='0',
                     # NativeAES needs the qualified original AES/crypto
                     # prelink closure, which the bootstrap pass does not have:
                     # leaving it on makes that pass exit with
                     # "NativeAES currently requires the qualified original
                     # AES/crypto prelink closure", no objects are produced and
                     # every later archive step fails on the missing
                     # kern_exec.o.
                     XNU_NATIVE_AES='0', XNU_NATIVE_RAM_BLOCK='0',
                     # Diagnostic probes only change the code they guard, but the
                     # bootstrap pass writes the compile records the final pass
                     # reuses. If these are 0 there, thread.o keeps a record with
                     # the probe compiled out even when the final pass asks for
                     # it, and the probe silently disappears from the kernel.
                     XNU_POLARIS_EXEC_TRACE=env.get('XNU_POLARIS_EXEC_TRACE', '0'),
                     XNU_POLARIS_DATA_TRACE=env.get('XNU_POLARIS_DATA_TRACE', '0'),
                     # build-xnu-4k-baseline.sh refuses XNU_SHENG_PMGR=1 unless
                     # the original-component (corecrypto) configuration is on,
                     # so mirror that flag into the bootstrap pass as well.
                     XNU_SHENG_PMGR='0')
    # Bootstrap reuse. The pass exists only to produce compile records and the
    # source symbol inventory; its objects are discarded and the final link
    # rebuilds everything with different defines. When the records are already
    # present and the XNU source tree is untouched since they were written, the
    # pass would reproduce byte-identical output, so it can be skipped. Set
    # POLARIS_SKIP_BOOTSTRAP=0 to force a full pass.
    required = ['link.filelist', 'iokit/DEVELOPMENT/IOPlatformExpert.cpo.json',
                'osfmk/DEVELOPMENT/prng_random.o.json', 'osfmk/DEVELOPMENT/startup.o.json']
    records_present = all((obj / name).is_file() for name in required)
    # The patch loop always leaves the tree dirty, so "status is clean" can
    # never hold. What matters is whether the patched sources are the ones the
    # records were produced from, so hash the tree state the patches produced.
    import hashlib
    xnu_root = ctx.tree / '.cache/xnu-work'
    stamp = ctx.tree / 'build/xnu-records.stamp'
    try:
        digest = hashlib.sha256()
        for patch in sorted((ctx.tree / 'patches/xnu').glob('*.patch')):
            digest.update(patch.name.encode())
            digest.update(patch.read_bytes())
        for patch in sorted((ctx.tree / 'kernel-overrides/xnu').glob('*.patch')):
            digest.update(patch.name.encode())
            digest.update(patch.read_bytes())
        for name in required:
            path = obj / name
            digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode() if path.is_file() else b'-')
        want = digest.hexdigest()
        have = stamp.read_text().strip() if stamp.is_file() else ''
        sources_match = (have == want)
        skip_bootstrap_ok = os.environ.get('POLARIS_SKIP_BOOTSTRAP', '1') != '0'
    except OSError:
        sources_match = False
        skip_bootstrap_ok = False
        want = ''
    xnu_clean = sources_match
    skip_bootstrap = skip_bootstrap_ok and records_present and xnu_clean
    if skip_bootstrap:
        print('Bootstrap pass: reusing existing XNU records (tree clean, set '
              'POLARIS_SKIP_BOOTSTRAP=0 to force)', flush=True)
    else:
        print('Bootstrap pass: compiling XNU records before external archives', flush=True)
    bootstrap_failed = False
    if not skip_bootstrap:
        try:
            run(['bash', script], cwd=ctx.tree, env=bootstrap, log_file=ctx.logs / 'bootstrap.log')
        except Error:
            bootstrap_failed = True
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
    storage_headers = ctx.tree / 'build/storage-abi/include/IOKit/storage'
    if (ctx.tree / 'refer/IOStorageFamily').is_dir() and not storage_headers.is_dir():
        run(['python3', ctx.tree / 'scripts/prepare-storage-headers.py'], cwd=ctx.tree, env=env,
            log_file=ctx.logs / 'storage-headers.log')
    kernel.build_archives(ctx, env)
    if ctx.config.get('XNU_NATIVE_UFS', False):
        run(['python3', ctx.tree / 'scripts/build-storage-base.py'], cwd=ctx.tree, env=env,
            log_file=ctx.logs / 'storage-base.log')
        storage_base = ctx.tree / 'build/storage-base/libIOBlockStorageDevice.a'
        env['XNU_EXTRA_LD_ARCHIVES'] = f'-Wl,-force_load,{storage_base}'
    if ctx.config.get('XNU_SHENG_PMGR', False):
        # build-xnu-4k-baseline.sh force-loads build/sheng-platform-power/... when
        # XNU_SHENG_PMGR=1, so the archive has to exist before the final link.
        run(['python3', ctx.tree / 'scripts/build-sheng-platform-power.py'], cwd=ctx.tree, env=env,
            log_file=ctx.logs / 'sheng-platform-power.log')
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
    final_data = destination.read_bytes()
    report = {'kernel_sha256': hashlib.sha256(final_data).hexdigest(),
              'kernel_bytes': len(final_data),
              'source_kernel_sha256': hashlib.sha256(data).hexdigest(),
              'unixnu_revision': revision, 'bootstrap_link_failed': bootstrap_failed,
              'final_configured_link_passed': True,
              'original_components_prelinked': prelinked is not None,
              'hardware_booted': False, 'userland_booted': False,
              'sources': {name: module.commit for (_, name), module in modules.items() if module.commit}}
    destination.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
    # Record what the bootstrap records were produced from, so the next build can
    # tell whether they are still valid for this patch set and kernel selection.
    if want:
        try:
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.write_text(want + '\n')
        except OSError:
            pass
    print(f'PASS: {"prelinked" if prelinked else "source"} kernel artifact '
          f'{destination}; no physical boot claimed')


if __name__ == '__main__':
    main()
