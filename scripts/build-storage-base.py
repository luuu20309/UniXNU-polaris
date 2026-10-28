#!/usr/bin/env python3
"""Build the pinned IOBlockStorageDevice base class for NativeUfs.

NativeUfs derives from this class, so its methods must be present at the
source-kernel link. The source and public header are from the same pinned
IOStorageFamily revision prepared by build-platform-kernel.py.
"""
import json
from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT / 'refer/IOStorageFamily/IOBlockStorageDevice.cpp'
    if not source.is_file():
        raise SystemExit(f'pinned IOStorageFamily source is missing: {source}')
    record_path = (ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/'
                   'iokit/DEVELOPMENT/IOPlatformExpert.cpo.json')
    record = json.loads(record_path.read_text().strip().rstrip(','))
    out = ROOT / 'build/storage-base'
    out.mkdir(parents=True, exist_ok=True)
    obj = out / 'IOBlockStorageDevice.o'
    command = []
    arguments = record['arguments']
    index = 0
    while index < len(arguments):
        value = arguments[index]
        if value == record['file']:
            command.append(str(source))
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
    rename_private_class(obj)
    archive = out / 'libIOBlockStorageDevice.a'
    archive.unlink(missing_ok=True)
    subprocess.run(['xcrun', 'ar', 'rcs', archive, obj], check=True)
    print(f'IOBlockStorageDevice: ARM64 archive built at {archive}')


# IOBlockStorageDevice.cpp is one translation unit of IOStorageFamily, so it
# carries that kext's `__GLOBAL__sub_I_...` static initializer and registers the
# class from __kernel__ at boot. The prelinked com.apple.iokit.IOStorageFamily
# kext registers the same name, and OSMetaClass panics on the duplicate:
#
#   OSMetaClass: Kext com.apple.iokit.IOStorageFamily class
#   IOBlockStorageDevice is a duplicate; kext __kernel__ already has a class
#   by that name.                                  @OSMetaClass.cpp:896
#
# Dropping the initializer entirely (an earlier attempt) removes the collision
# but also leaves this copy's OSMetaClass::reserved unallocated, so the first
# addInstance() through it faults on a NULL reserved pointer. The initializer has
# to run; only the *name* has to differ, because OSMetaClass detects duplicates
# by class name.
#
# So keep the registration and rename the kernel-private copy. NativeUfs's
# subclass links against these symbols normally. build-platform-kernel.py also
# changes the prelinked IOBlockStorageDriver personality's IOProviderClass to
# this private name, or the class check would reject NativeUfsBlock before
# IOStorageFamily could publish an IOMedia.
PRIVATE_CLASS_NAME = b'IOPolarisBlockDevice'
PUBLIC_CLASS_NAME = b'IOBlockStorageDevice'

INIT_FUNC_SECTION = b'__mod_init_func'
DATA_SEGMENT = b'__DATA'
CSTRING_SECTION = b'__cstring'
TEXT_SEGMENT = b'__TEXT'


def rename_private_class(obj):
    """Rename the class string this copy registers, keeping the initializer."""
    data = bytearray(obj.read_bytes())
    ncmds, = struct.unpack_from('<I', data, 16)
    offset = 32
    cstring = None
    initializer = None
    for _ in range(ncmds):
        cmd, length = struct.unpack_from('<II', data, offset)
        if cmd == 0x19:                                  # LC_SEGMENT_64
            nsects, = struct.unpack_from('<I', data, offset + 64)
            for index in range(nsects):
                section = offset + 72 + index * 80
                sectname = data[section:section + 16].split(b'\0')[0]
                segname = data[section + 16:section + 32].split(b'\0')[0]
                size, = struct.unpack_from('<Q', data, section + 40)
                filepos, = struct.unpack_from('<I', data, section + 48)
                if sectname == CSTRING_SECTION and segname == TEXT_SEGMENT:
                    cstring = (size, filepos)
                if sectname == INIT_FUNC_SECTION and segname == DATA_SEGMENT:
                    initializer = size
        offset += length
    if cstring is None:
        raise SystemExit('IOBlockStorageDevice object has no __cstring section')
    if initializer != 8:
        raise SystemExit(f'expected one class initializer, got section size {initializer}')

    size, filepos = cstring
    blob = bytes(data[filepos:filepos + size])
    # The class-name literal is a whole NUL-terminated string, and the shortest
    # occurrence that is exactly the public name is the registration string.
    replacement = PRIVATE_CLASS_NAME + b'\0'
    if len(replacement) > len(PUBLIC_CLASS_NAME) + 1:
        raise SystemExit('private class name is longer than the public one; in-place rename impossible')
    matches = [at for at in range(len(blob))
               if blob.startswith(PUBLIC_CLASS_NAME + b'\0', at)
               and (at == 0 or blob[at - 1] == 0)]
    if len(matches) != 1:
        raise SystemExit(f'expected one complete class-name literal, found {len(matches)}')
    at = matches[0]
    padded = replacement + bytes(len(PUBLIC_CLASS_NAME) + 1 - len(replacement))
    data[filepos + at:filepos + at + len(PUBLIC_CLASS_NAME) + 1] = padded
    obj.write_bytes(data)
    print('IOBlockStorageDevice: class registered as '
          + PRIVATE_CLASS_NAME.decode() + ' (kext keeps the public name)')

if __name__ == '__main__':
    main()
