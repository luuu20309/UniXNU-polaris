#!/usr/bin/env python3
"""Turn probabilistic guard-zalloc (PGZ) off at load time, without touching __text.

Why this and not a boot argument
--------------------------------
Round 12's panic is a NULL dereference in pgz_protect():

    ffffff0072f090c  ldxr x22, [x9]         ; x9 = &pgz_slot_head
    ffffff0072f0910  ldr  x12, [x22, #0x8] ; FAR 0x8 -> x22 == NULL
                     esr 0x96000005, far 0x0000000000000008

`pgz_slot_head` is assigned in exactly one place, pgz_init(), which runs at
STARTUP(EARLY_BOOT, STARTUP_RANK_MIDDLE). To reach pgz_protect() at all, three
things must already be true, and they pin the crash to the phase where
pgz_slot_avail > 0 but pgz_slot_head is still NULL:

  * zone->z_pgz_tracked  — set by pgz_zone_init() from zone_create()
  * pgz_slot_avail > 0   — set by pgz_get_size(), reached from the KMEM phase
  * pgz_slot_head == 0   — i.e. pgz_init() has not run yet

`pgz=0` cannot fix this. The tunables are parsed at
kernel_startup_initialize_upto(STARTUP_SUB_KMEM) inside vm_mem_bootstrap(),
which is *after* vm_page_bootstrap() has already called zone_create_ext() for
the "vm pages" zone (vm_resident.c:1637). By the time pgz=0 is honoured,
pgz_all has already been read and pgz_uses is already non-zero, so pgz_init()
takes its real path. pgz_all also lives in __DATA_CONST, so nothing can clear
it at runtime either.

What actually works: pgz_all is a SECURITY_READ_ONLY_LATE bool in __DATA_CONST
whose *initial value* is 1 in a DEVELOPMENT/DEBUG build. Clearing that byte in
the file makes every pgz_zone_init() take its `if (pgz_all)` early-return, so
no zone is ever tracked and zalloc_ext() never reaches pgz_protect(). pgz_init()
then returns immediately on `if (!pgz_uses)`.

__DATA_CONST is read-only at runtime but is plain file-backed data, and this
byte is not covered by the corecrypto FIPS HMAC (that covers __text), so no
reseal is needed and no instruction is modified.

The polaris UFS root transport polls its own completion with interrupts masked
(NativeUfs.cpp), and nothing in the boot path depends on PGZ, so turning it off
costs no bring-up capability. It does remove a security mitigation from a
development kernel used only for this port.
"""
import struct
import sys
from pathlib import Path

PGZ_ALL_VA = 0xFFFFFFF007167A74
SEGMENT_COMMAND = 0x19
MACHO64_MAGIC = 0xFEEDFACF


def main():
    if len(sys.argv) != 3:
        raise SystemExit(f'usage: {sys.argv[0]} INPUT.macho OUTPUT.macho')
    source, output = Path(sys.argv[1]), Path(sys.argv[2])
    data = bytearray(source.read_bytes())

    magic, _, _, _, ncmds, _, _, _ = struct.unpack_from('<IiiIIIII', data, 0)
    if magic != MACHO64_MAGIC:
        raise SystemExit(f'{source} is not a 64-bit Mach-O')

    offset = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, offset)
        if cmd == SEGMENT_COMMAND:
            name, vmaddr, vmsize, fileoff, filesize, maxprot, initprot, _, _ = \
                struct.unpack_from('<16sQQQQiiII', data, offset + 8)
            span = min(vmsize, filesize)
            if filesize and vmaddr <= PGZ_ALL_VA < vmaddr + span:
                at = fileoff + PGZ_ALL_VA - vmaddr
                segment = name.rstrip(b'\0').decode()
                if data[at] != 1:
                    raise SystemExit(f'pgz_all is {data[at]:#04x}, expected 1 (true)')
                if not initprot & 1:
                    raise SystemExit(f'pgz_all is not in a readable segment ({segment})')
                data[at] = 0
                output.write_bytes(data)
                print(f'{output}: pgz_all at {PGZ_ALL_VA:#x} in {segment} '
                      f'(initprot {initprot:#x}), file offset {at:#x}: 1 -> 0')
                print(f'  {source.name} {len(data)} bytes was sha256 {source_sha(source)}')
                print(f'  {output.name} written')
                return
        offset += size
    raise SystemExit(f'{source} does not map {PGZ_ALL_VA:#x}')


def source_sha(path):
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == '__main__':
    main()
