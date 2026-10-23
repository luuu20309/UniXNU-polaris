# UniXNU-polaris handoff notes

## 2026-10-24: platform stage created, nothing hardware-tested

This repository transfers the polaris (Xiaomi Mi MIX 2S, SDM845) port from the
retired ABL-shim chain onto the UniXNU build driver, at the same platform
milestone the sweet port uses. Everything here is host-verifiable only:
the ADT generator, its binary-ABI tests, the fail-closed CPU prefix check,
the ArmCpu/DarwinBoot overrides (inherited from UniXNU-sweet), the QEMU EFI
checks and the macOS source-kernel build. **No CI run has executed yet and no
polaris hardware has run this tree.** Push to GitHub first, let the checks
workflow pass, then dispatch the kernel workflow.

## Board constants and their provenance

| Constant | Value | Source |
| --- | --- | --- |
| GICD / GICR | 0x17a00000 / 0x17a60000 (+0x10000 / +0x100000, stride 0x20000) | SDM845 GICv3; same layout verified on SM7150 by UniXNU-sweet |
| Timebase | 19.2 MHz | SDM845 generic timer; observed by both chains |
| Virtual-timer INTID | PPI 19 | old-chain ADT; UniXNU ArmCpu reads it from the ADT (omission keeps 27) |
| PSCI | SMC conduit | old chain: PSCI CPU_ON/SGI proven for CPU1-3 with `cpus=4` on XNU 8796 |
| Upper DDR bank | base 0x100000000; 6 GiB variant size 0x7cc00000, 8 GiB 0xfcc00000 | old-chain `tools/mkadt.py --polaris`; kernel-managed RAM above every Qualcomm XPU carve-out. 6 GiB default is safe on both installed sizes |
| CPU topology | 4x Cortex-A55 (reg 0x000-0x300) + 4x Cortex-A75 (0x400-0x700), reg = core<<8 | SDM845 encodes the core in Aff1; upstream Xiaomi polaris DTS |
| Core clocks (descriptive) | A55 1804.8 MHz, A75 2803.2 MHz | old-chain ADT; no UniXNU driver reads them at this stage |

## Old-chain to new-chain mapping

| Old ABL-shim artifact | Disposition in this repository |
| --- | --- |
| 93 XNU-8796/SWEET patches (0001-0093) | not portable to Darwin 23; the equivalent platform support (DT platform expert, arch timer, PAN, source corecrypto/pthread) comes from UniXNU common patches selected by `configs/polaris_defconfig` |
| `tools/mkadt.py --polaris` (iPhone X identity, chosen properties, fbcon, UFS node) | replaced by `scripts/make-polaris-adt.py` (platform-stage ADT: memory range for DarwinBoot, NVRAM proxy, PSCI, GIC node). The iPhone X identity is **deliberately absent**: it exists to satisfy iOS 16 userland, which cannot run on this kernel; the prelink/identity stage is a separate future decision |
| `tools/polaris_cpu_topology.py` + `mkpayload --check-polaris-cpus` | migrated as `scripts/check-cpu-prefix.py` (same fail-closed semantics: exactly one `cpus=N`, no `cpumask`, contiguous MPIDR prefix, per-core cluster/state check, count cannot exceed described CPUs). ADT state values follow UniXNU (`running`/`waiting`) instead of the old `stopped` |
| ABL framebuffer console (`sweet-fbcon`, splash at 0x9d400000, 1080x2160) | not carried; UniXNU kernel console goes through the loader/firmware path. Restore as a diagnostic only after a polaris UEFI port exists |
| shim UFS handoff (`sweet-ufs` @ 0x1d84000) | not carried; storage bring-up restarts from UniXNU's NativeUfs when that stage is reached |
| Touch GPIO33/34 bit-bang I²C + GPIO99 reset (0086-0090) | not carried; must be redesigned as a board-gated driver (or NativeHidInput integration) against Darwin 23. The old evidence (S3330 at 0x20, F01/F12 PDT, bounded F12 frame reads, one/two-finger confirmation) stays valid as hardware knowledge |
| GPU firmware metadata diagnostics (0091-0093, A630v2) | not carried; the A618 legacy HFI kit approach in UniXNU-sweet shows the shape of a future A630 kit. No GPU work is claimed |
| Payload size bound (ABL ramdisk-to-DTB gap 14,589,952 B) | obsolete: the UEFI boot chain has no ABL DTB gap; payload sizing becomes `board.toml [payload] size_mib` |

## Verification status

- Host: `python -m unittest discover -s tests` — 6 ADT ABI tests pass
  (GIC translation, NVRAM proxy, banks, CPU prefix boundary, rejections).
  `check-cpu-prefix.py` exercised on 1-CPU and 8-CPU ADTs, including refusal
  cases. CI adds the clang-compiled timer/window tests. CI **passed** on
  2026-10-23 (run 40022378017: host and EFI/QEMU both green after one
  network-flaked retry).
- Integration: `unixnu defconfig polaris_defconfig` and `unixnu status`
  resolve ArmCpu and DarwinBoot from this repository, everything else from
  UniXNU `37987aea` (verified locally with the base checkout at that commit).
- macOS kernel: **built successfully** in [CI run 40028991145](https://github.com/luuu20309/UniXNU-polaris/actions/runs/40028991145)
  (2026-10-23): `kernel.macho` 19,299,712 bytes, SHA256
  `761e2def7fd86cffafef39892d172ac4c5bfd6e7d6c142d044fa721259a258e7`,
  plain ARM64 Mach-O (`feedfacf` / `0100000c`), final configured link passed,
  no original Apple components prelinked, no hardware or userland boot. The
  first two attempts hit the GitHub contents-API rate limit in the pinned
  base's `prepare-xnu-source.py`; the `scripts/overlay/` git-only replacement
  (restores the same headers through the git protocol) fixed it. The third
  attempt exposed the missing device archive builder, now added as
  `scripts/build-arm-driver.py`.
- EFI/QEMU: **passed in CI** (same run as host).
- Phone: nothing. `serial=3` in `board.toml` is the UniXNU convention; polaris
  has no wired debug UART, so treat it as inert until a console path exists.

## Next steps, in order

1. Push this repository; let `host and EFI checks` go green; dispatch
   `Darwin 23 platform kernel` and record the artifact SHA256 here.
2. Port sweet's `check-platform-kernel.py` QEMU native check (CPU registration
   + advancing timer IRQs before panic) to consume the polaris kernel.macho;
   single-CPU first, self-IPI reported separately.
3. polaris UEFI firmware: a Mu-Silicium/edk2 SDM845 platform package
   (Renegade-Project has SDM845 UEFI work to reference). Without it there is
   no `unixnu build firmware`, no payload boot and no phone image. The
   `board.toml [firmware]` section is intentionally absent until then.
4. Only after 2-3: decide the prelink target and Apple identity (an iPadOS 17
   kernelcache like sheng, or keep a source-only kernel), then userdata,
   trust-cache policy, and only then re-plan touch/display/GPU as board-gated
   Darwin 23 drivers.
5. SMP: try `cpus=5` on an approved temporary boot once a UEFI chain exists
   (A75 cluster never observed under XNU; old chain's plan applies unchanged).

## Safety rules carried over from the old chain

No Apple images, device dumps, serials or Qualcomm blobs in this repository.
Temporary boots and any partition writes need explicit prior approval;
preserve bootloader, recovery, modem, persist and backups. Compilation
success is never presented as hardware execution.
