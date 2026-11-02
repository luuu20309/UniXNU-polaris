#!/usr/bin/env python3
"""Compile a platform or SDM845 NativeUfs archive using XNU's compile record.

Used inside the assembled UniXNU tree. Runs the recorded compiler and xcrun
ar; writes only build/<component>/. Does not access a device.
"""
import argparse
import hashlib
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
    text = text.replace(before, after)

    # Root publication without owner verification.
    #
    # Upstream refuses to publish any root media unless NATIVE_VERIFIED_BLOCK is
    # compiled in:
    #     #else
    #       return false; // Root publication requires owner verification in every build.
    #       #endif
    # This port has no VerifiedRoot policy bundle (kits/VerifiedRoot is empty),
    # so that path cannot be built and the kernel would never see an IOMedia for
    # the darwin windows - XNU then panics "Failed to mount root device". The
    # user accepted the trade-off of publishing the windows unverified so the
    # root mount can be exercised. This is a bring-up build only, and it does not
    # claim the media is authenticated.
    before = ('#else\n'
              '      return false; // Root publication requires owner verification in every build.\n'
              '#endif')
    after = ('#else\n'
             '      // Polaris bring-up: publish the windows without an owner-verification\n'
             '      // policy bundle. The media is not authenticated in this build.\n'
             '      for (unsigned i = 0; i < gPending; ++i) {\n'
             '        gRootDiagnostic.publicationWindow = i;\n'
             '        gPendingUuid[i][0] = 0;\n'
             '      }\n'
             '      return gPending != 0;\n'
             '#endif')
    if text.count(before) != 1:
        raise SystemExit('NativeUfs source drift: verified-only root publication guard not found uniquely')
    text = text.replace(before, after)

    # wait() gates on mediaWithUuidExists(uuid, ...). With no signed volume
    # identity the uuid is empty, and an empty IOPropertyMatch value can never
    # match, so the publisher would stall for 30 s and then fail. When the
    # window started successfully the IOMedia is already published by start();
    # treat that as ready instead of waiting for a UUID we do not have.
    before = ('      bool ready = false;\n'
              '      // Diagnostic mode affects reporting only, never the dependency gate.\n'
              '      for (unsigned tick = 0; tick <= 300; ++tick) {')
    after = ('      if (!uuid || !uuid[0]) {\n'
             '        IOLog ("NativeUfs: %s published without a signed UUID\\n", device->windowLabel ());\n'
             '        return true;\n'
             '      }\n'
             '      bool ready = false;\n'
             '      // Diagnostic mode affects reporting only, never the dependency gate.\n'
             '      for (unsigned tick = 0; tick <= 300; ++tick) {')
    if text.count(before) != 1:
        raise SystemExit('NativeUfs source drift: media wait gate not found uniquely')
    text = text.replace(before, after)

    if os.environ.get('XNU_POLARIS_SYSTEMOS_MOUNT') == '1':
        anchor = 'static void captureMissingMedia (const char *uuid)\n'
        if text.count(anchor) != 1:
            raise SystemExit('NativeUfs source drift: SystemOS resolver anchor')
        resolver = r"""
// Experimental unverified Polaris bring-up. Return a BSD path only when the
// unique expected UUID belongs to this driver's SystemOS window. Not a proof
// of cryptographic authenticity.
extern "C" int polaris_systemos_bsd_path (char *out, size_t outSize)
{
  static const char wanted[] = "8846FE78-2910-4297-AF97-D59BB6F8FC16";
  if (!out || outSize < 16) return 0;
  for (unsigned tick = 0; tick < 300; ++tick) {
    NativeUfsBlock *system = nullptr;
    for (unsigned i = 0; i < gPending; ++i) {
      if (gPendingDevice[i] && !strcmp (gPendingDevice[i]->windowLabel (), "SystemOS")) {
        if (system) return 0;
        system = gPendingDevice[i];
      }
    }
    OSDictionary *matching = IOService::serviceMatching ("IOMedia");
    OSIterator *iterator = matching ? IOService::getMatchingServices (matching) : nullptr;
    unsigned found = 0;
    char name[32] = {};
    while (system && iterator) {
      IORegistryEntry *media = OSDynamicCast (IORegistryEntry, iterator->getNextObject ());
      if (!media) break;
      OSString *uuid = OSDynamicCast (OSString, media->getProperty ("UUID"));
      OSString *bsd = OSDynamicCast (OSString, media->getProperty (kIOBSDNameKey));
      if (!uuid || !bsd || strcmp (uuid->getCStringNoCopy (), wanted)) continue;
      IORegistryIterator *parents = IORegistryIterator::iterateOver (
        media, gIOServicePlane, kIORegistryIterateRecursively | kIORegistryIterateParents);
      bool belongs = parents && ufsMediaHasAncestor (*parents, system);
      OSSafeReleaseNULL (parents);
      if (!belongs) continue;
      const char *candidate = bsd->getCStringNoCopy ();
      if (strncmp (candidate, "disk", 4) || strlen (candidate) >= sizeof (name)) continue;
      bool valid = true;
      for (unsigned j = 4; candidate[j]; ++j) {
        if ((candidate[j] < '0' || candidate[j] > '9') && candidate[j] != 's') valid = false;
      }
      if (!valid) continue;
      strlcpy (name, candidate, sizeof (name));
      ++found;
    }
    OSSafeReleaseNULL (iterator);
    OSSafeReleaseNULL (matching);
    if (found > 1) return 0;
    if (found == 1) {
      int n = snprintf (out, outSize, "/dev/%s", name);
      return n > 0 && static_cast<size_t> (n) < outSize;
    }
    IOSleep (100);
  }
  return 0;
}

"""
        text = text.replace(anchor, resolver + anchor, 1)

    source.write_text(text)

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
    parser.add_argument('component', choices=['ArmDTPlatform', 'ArmCpu', 'NativeUfs',
                                              'NativeMobileDisplay', 'NativeRamBlock',
                                              'NativeAES'])
    args = parser.parse_args()
    if args.component == 'NativeUfs' and os.environ.get('XNU_NATIVE_UFS') != '1':
        print('NativeUfs: skipped (CONFIG_XNU_NATIVE_UFS is disabled)')
        return
    out = ROOT / 'build' / {'ArmCpu': 'armcpu', 'ArmDTPlatform': 'armdt-platform',
                            'NativeUfs': 'native-ufs',
                            'NativeMobileDisplay': 'native-mobile-display',
                            'NativeRamBlock': 'native-ram-block',
                            'NativeAES': 'native-aes'}[args.component]
    out.mkdir(parents=True, exist_ok=True)
    recipe = ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json'
    record = json.loads(recipe.read_text().strip().rstrip(','))
    if args.component == 'NativeUfs':
        source = polaris_native_ufs_source(out)
    else:
        source = ROOT / 'drivers' / args.component / (args.component + '.cpp')
        if not source.is_file():
            raise SystemExit(f'{args.component} submodule is missing: {source}')
    # Drivers ship their own headers (OriginalMobileFramebufferABI.h and the
    # NativeUfs ABI shims); the recorded command only carries XNU's include path.
    source_dir = source.parent
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
                    # Kept because every kernel that has reached PID 1 on this
                    # phone was compiled with this sysroot. Dropping it changes
                    # the generated __text (34592 vs 33644 bytes for the same
                    # source), which is not a portability-only difference.
                    '-isysroot', subprocess.check_output(
                        ['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], text=True).strip(),
                    '-I', str(ROOT / '.cache/xnu-work/osfmk')])
    command.extend(['-I', str(source_dir)])
    if args.component in ('NativeUfs', 'NativeRamBlock'):
        # Both subclass IOBlockStorageDevice, whose header comes from the
        # storage ABI capture rather than the open XNU tree.
        command.extend(['-I', str(ROOT / 'build/storage-abi/include')])
    if args.component == 'NativeUfs':
        command.append('-DNATIVE_UFS_POLARIS=1')
    command.append('-mgeneral-regs-only')
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

    if args.component == 'NativeRamBlock':
        # build-xnu-4k-baseline.sh:658 force-loads build/native-ram-block/
        # libNativeRamBlock.a. The driver publishes an IOBlockStorageDevice so
        # IOStorageFamily can raise a real IOMedia carrying the seed's APFS
        # UUID; the kernel then finds it by UUID instead of by /dev/md0, which
        # APFS refuses (alioth measured ENOENT on hardware 2026-10-26).
        report = {
            'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
            'compiled_object_sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
        }
        (out / 'bindings.json').write_text(json.dumps(report, indent=2) + '\n')
        print(f'{args.component}: archive and bindings report written')

    if args.component == 'NativeMobileDisplay':
        # Second translation unit: base-class ctor/dtor plus the provider probe
        # that IOUserClient.cpp references. Built with the same flags, added to
        # both archives so the driver's weak references resolve.
        stub_source = ROOT / 'scripts/native-mobile-display-stubs/NativeMobileDisplayStubs.cpp'
        if not stub_source.is_file():
            raise SystemExit(f'display stubs are missing: {stub_source}')
        stub_object = out / 'NativeMobileDisplayStubs.o'
        stub_command = []
        replaced_source = replaced_output = False
        for argument in command:
            if argument == str(source):
                stub_command.append(str(stub_source))
                replaced_source = True
            elif argument == str(destination):
                stub_command.append(str(stub_object))
                replaced_output = True
            else:
                stub_command.append(argument)
        if not (replaced_source and replaced_output):
            raise SystemExit('could not rewrite the compile command for the stubs')
        result = subprocess.run(stub_command, cwd=record['directory'], capture_output=True, text=True)
        (out / 'compile-stubs.log').write_text(result.stdout + result.stderr)
        if result.returncode:
            raise SystemExit(result.stdout + result.stderr)
        subprocess.run(['xcrun', 'ar', 'rcs', archive, stub_object], check=True)

        # build-xnu-4k-baseline.sh force-loads libNativeMobileDisplay.late.a and
        # refuses to link unless build-report.json pins the archive bytes and
        # every source it was compiled from. That guard exists so a stale or
        # hand-edited display archive cannot silently reach the kernel.
        late = out / 'libNativeMobileDisplay.late.a'
        late.unlink(missing_ok=True)
        subprocess.run(['xcrun', 'ar', 'rcs', late, destination, stub_object], check=True)
        source_sha256 = {}
        for name in ('NativeMobileDisplay.cpp', 'OriginalMobileFramebufferABI.h'):
            path = source_dir / name
            source_sha256[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        report = {
            'archive_sha256': hashlib.sha256(late.read_bytes()).hexdigest(),
            'compiled_object_sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
            'source_sha256': source_sha256,
        }
        (out / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(f'{args.component}: late archive and build report written')

    print(f'{args.component}: ARM64/iOS 17 archive built')


if __name__ == '__main__':
    main()
