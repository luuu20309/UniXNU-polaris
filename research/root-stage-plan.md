# Root stage plan (research note, 2026-10-24)

With the platform stage verified on hardware, the next milestone is a kernel
that mounts a root instead of panicking at the designed boundary. This note
surveys the options; nothing here is built yet.

## Where we stop today

`Failed to mount root device @IOKitBSDInit.cpp:984` — the platform kernel has
no root provider, by design (same as the UniXNU QEMU platform check).

## Option A (recommended first step): the XNU mockfs ramdisk root

XNU ships `bsd/miscfs/mockfs`, a minimal executable-root filesystem used by
Apple for recovery-style boots, and the base repository already builds it
(`scripts/build-mockfs-fixture.py`, compiled against the recorded
`vfs_conf.cpo.json` flags, plus the `mockfs-*` patches in `patches/xnu/`).
Steps:

1. Add the mockfs archive to `[kernel] archives` and the matching defconfig
   entries (mirror how the sheng board wires it).
2. Provide a ramdisk through DarwinBoot (packed ramdisk support already exists
   in the loader: `PackedRamdisk.h` in the DarwinBoot override) and the ADT
   `memory-map` entry.
3. Boot argument to select the ramdisk root; expectation: the root-mount panic
   disappears and the kernel reaches its idle loop with a mounted root — the
   on-screen evidence is simply that the boot stops panicking and the panel
   keeps its bands.
4. From there, a tiny static first user task is a possible follow-up, but no
   iOS userland exists on this tree (the prelink/identity decision is a
   separate, larger stage).

## Option B (the real destination): a storage root and the iOS userland chain

`native-ufs-root` (UniXNU's NativeUfs driver) plus an iPadOS kernelcache
prelink like sheng — that is the full system path, gated on the prelink
target and trust-cache decisions documented in NOTES.md. Weeks of work; do
not start before Option A proves the root plumbing on hardware.

## Why not the old chain's ramdisk tricks

The old ABL chain booted the iOS 16 userland from a ramdisk with a large
family of XNU-8796 patches (mockfs-ramdisk-root, unsealed-root, read-write-root
…). None of that transfers: Darwin 23 has the mockfs source in-tree and the
UniXNU patches already cover the ABI side, so Option A reuses maintained
components instead of porting 8796-era hacks.

## Risks

- The mockfs fixture is offline-test oriented; its on-hardware behavior on
  this board is untested anywhere (it is QEMU-qualified upstream only).
- The packed ramdisk path in DarwinBoot was written for the sheng payload;
  the polaris ADT may need the matching `memory-map` node added.
- Root mounts interact with the code-signing path; expect the first attempts
  to surface CS/AMFI panics — which the on-screen panic funnel now renders,
  so diagnosis continues the same way it did today.
