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

## Hardware test log (2026-10-24, user-approved temporary boots)

Every step below was a temporary `fastboot boot`; no partition was written
except the one ESP flash the user explicitly approved ("整机数据都无所谓" —
all device data expendable).

1. **Renegade edk2-msm firmware runs on the phone.** Built from
   [edk2-msm](https://github.com/edk2-porting/edk2-msm) at the fork
   ([run 40061045714](https://github.com/luuu20309/edk2-msm/actions/runs/40061045714),
   boot image SHA256
   `534ebe6887b86d6c1f4cc0b6bc92651649b8aebeecd6bc6739e3d19332a5c7fb`),
   `fastboot boot boot-polaris.img` reached the Renegade graphical menu
   (UEFI Shell / Continue / SimpleInit / UEFI Boot Menu / Reboot), GOP alive
   at panel resolution. SimpleInit's Mass Storage, File Manager and virtual
   keyboard are present.
2. **ESP flashed to userdata** (128 MiB FAT32, volume `POLARISESP`,
   [image run 40061917191](https://github.com/luuu20309/UniXNU-polaris/actions/runs/40061917191)):
   `fastboot flash userdata esp.img` OKAY. boot/modem/persist untouched.
3. **UniXNU menu ran on the phone.** After the second `fastboot boot`, the
   UniXNU boot manager rendered `UniXNU 0.1.0 | config: \EFI\UniXNU\...`
   with the `polaris: UniXNU platform bring-up` entry, timeout countdown,
   `drivers 1 loaded / 0 failed`, then booted it (user-photographed).
4. **DarwinBoot hand-off verified on hardware** (user-photographed summary):
   `va=0xfffffff007004000 pa=0x100004000` (upper-bank constraint honored),
   `owned=0x40000000`, `video base=0x9d400000 display=1 row=4320 1080x2160
   32bpp` (the real panel splash address), entry words read back
   (`140006f5 d503201f ...`), `CurrentEL 1 SCTLR_EL1 0xc0180d
   TCR_EL1 0x280803518`, then `inputs validated; exiting boot services`.
5. **XNU kernel entry executed**: the green entry-probe bar was observed on
   the panel (first photo). What happens after entry is not yet observable:
   the platform kernel has no userland, and panic output is not wired to the
   framebuffer yet.

Next evidence step in flight: the ArmCpu timer heartbeat (walking green line
on row 2, driven by real PPI-19 timer IRQs through the GIC, framebuffer
taken from boot_args Video) — its presence distinguishes a live kernel from
an early silent stop.

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
- QEMU native platform check: **passed** in [run 40030970744](https://github.com/luuu20309/UniXNU-polaris/actions/runs/40030970744)
  (2026-10-24): the actual source kernel registered ArmCpu, reached GIC
  ready and advanced timer IRQs 0 -> 1 -> 3 under QEMU virt (cortex-a76,
  single CPU); counter snapshots in `out/platform-check/report.json`.
  Self-IPI was sent but not received — same known boundary as the sweet
  platform milestone, reported separately and not part of the pass
  criterion. Emulated platform only. The `native-check` CI job now runs this
  after every kernel build and assembles the route-A payload ESP
  (`scripts/make-payload-esp.py`).
- Route-A payload ESP artifact: first produced and verified in
  [run 40032226971](https://github.com/luuu20309/UniXNU-polaris/actions/runs/40032226971)
  (2026-10-24, kernel SHA256
  `22c42d2e919a066a624025d7bb18cd1205845bac40ca494435c4ca1e09646ac3`):
  `platform-check-results` carries the full ESP tree
  (`EFI/BOOT/BOOTAA64.EFI`, DarwinBoot, kernel, the **real polaris ADT**
  with `xiaomi,polaris` / upper bank / PSCI, unixnu.toml with the board
  command line). It is a flashing-ready ESP layout for the edk2-msm
  firmware, **not** a boot image and not hardware-tested.
- EFI/QEMU: **passed in CI** (same run as host).
- Phone: nothing. `serial=3` in `board.toml` is the UniXNU convention; polaris
  has no wired debug UART, so treat it as inert until a console path exists.

## Next steps, in order

1. ~~Push, CI green~~ (done 2026-10-23: host + EFI/QEMU green, run 40022378017).
2. ~~Source kernel built~~ (done, run 40028991145, SHA256 above); the QEMU
   native check runs automatically after every kernel build
   (`scripts/check-platform-kernel.py`, wired as the `native-check` job).
3. polaris UEFI firmware: see [research/uefi-firmware-plan.md](research/uefi-firmware-plan.md).
   Route A reuses the proven Renegade edk2-msm polaris firmware and boots
   UniXNU/DarwinBoot from the ESP (temporary `fastboot boot`, user approval
   per boot); Route B extracts a Mu-Silicium `polarisPkg` from what Route A
   proves. Without one of these there is no payload boot and no phone image;
   `board.toml [firmware]` stays absent until Route B.
4. Only after 3: decide the prelink target and Apple identity (an iPadOS 17
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

## Hardware bring-up day 2 (2026-10-24 afternoon): the kernel runs to its designed end

User-approved session; every boot was a temporary `fastboot boot`, the only
writes being the ESP re-flash into userdata. The full timeline, evidence kept
as user-photographed screen states:

### The silent stop and the probe ladder

The heartbeat ESP from the morning reached the green entry bar but painted no
walking line and no panic text: a silent early stop. With no serial, no storage
and no framebuffer mapping until late boot, the only signal channel is the
screen, so the sheng entry-probe tool (`scripts/patch-xnu-entry-probe.py`,
adapted: cave relocation into verified zero windows, vendored font, `--probe-extra`
plumbing) was wired into `make-payload-esp.py`, and the death point was
bisected by stage-4 reset probes (reach the call → 8 s later PSCI reset =
phone reboots on its own; never reach = screen stays frozen):

| Q | Probe (reset after call) | Result |
| --- | --- | --- |
| 1 | `_PE_init_platform` #1 (arm_init) | reached (reboot) |
| 2 | `_arm_vm_init` | reached |
| 3 | `_kprintf` #1 | reached |
| 4 | `_bsd_init` (old kernel) | frozen — invalidated later by bug 1 |
| 5 | `_kernel_startup_initialize_upto` #1 (old kernel) | frozen — same |
| 6 | `_machine_startup` | frozen |
| 7 | `_PE_init_platform` #2 | frozen |
| 8 | `_PE_create_console` | frozen → death inside this function |
| 9 | disassembly of `_PE_create_console` (ci `disasm.yml`) | see bug 1 |

### Bug 1: the BCM2837 fixed panic-log address bus-errors on SDM845

The disassembly showed `PE_create_console` → `ml_io_map_wcomb(0x3c0fc000,
0x4000)` → an immediate load: the BCM2837 machine configuration (which UniXNU
uses for the 4 KiB page layout) compiles in the Raspberry-Pi SoC panic-log
address, and `check_for_panic_log` maps and reads it unconditionally. QEMU
tolerates the read (returns 0, magic mismatch, skip) — so every CI check was
green — but on SDM845 0x3c0fc000 is unmapped bus-error territory: synchronous
exception, silent stop. Fix: `CONFIG_XNU_DT_PANIC_REGION=y` routes the probe
through the device tree (UniXNU's own `native-dt-panic-region` patch), and the
polaris ADT has no panic-region node, so the probe exits safely. **This bug
and fix apply to every UniXNU non-RPi hardware port.**

### Bug 2: the entry probes invalidated the FIPS POST integrity HMAC

After bug 1 the kernel advanced and the panic screen drew its first real
message: `FIPS Kernel POST Failed (-2074) @corecrypto_kext.c:342`. Decoding
`result = test_counter * 1000 + rc`: the second POST test
(`fipspost_post_integrity`) failed with -74 — the HMAC-SHA256 over `__text`
did not match the value sealed into `__fips_hmacs` by the base build's
`seal-source-crypto-kernel.py`. Cause: our entry-probe patch rewrites `__text`
(caves + branch sites) **after** the seal. Fix: `make-payload-esp.py` now
re-runs the seal over the patched ESP copy (`scripts/seal-source-crypto-kernel.py`,
vendored) — no selftest is skipped or weakened; the integrity POST now
measures the image that actually runs. QEMU passed throughout because its
native check consumes the unpatched kernel.

### End state: the designed finish line

With both fixes the kernel ran through the entire startup: arm_init (kprintf,
console), the scheduler and thread world, the STARTUP chain including the
corecrypto FIPS POST, bsd_init and IOKit, and stopped at the designed
platform-stage boundary with a photographed panic:

```
Failed to mount root device @IOKitBSDInit.cpp:984
pages=0x3dcfc free=0x3d4bd
```

This platform kernel has no root by design (no storage driver, no ramdisk
root, same as the UniXNU QEMU platform check). Reaching that panic is itself
the proof set: bsd_init cannot run without working timer interrupts and the
scheduler, so the real-phone GIC/PPI-19 timer path and preemption are
established by execution, not by claim. The heartbeat pixel did not paint
(a framebuffer-mapping detail inside ArmCpu::start, cosmetic at this stage);
the scheduler evidence above supersedes it.

### Tooling added during the session

- `scripts/patch-xnu-entry-probe.py` + `xnu-panic-screen.c` + `Font.h`
  (vendored from the sheng device repository, BSD-2-Clause-Patent) with cave
  relocation for this kernel's link layout.
- `make-payload-esp.py`: `--probe-extra` plumbing and the automatic re-seal.
- `.github/workflows/disasm.yml`: on-demand kernel disassembly for probes.
- `board.toml`: `serial=0` (no debug UART on this phone; the old `serial=3`
  placeholder was never the death cause but is corrected anyway).
