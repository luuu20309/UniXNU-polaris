# polaris UEFI firmware plan (research note, 2026-10-24)

What is missing from this port before any phone boot: the UEFI firmware that
UniXNU's boot chain expects (`boot.img → BootShim → firmware volume →
UniXNU/DarwinBoot → payload kernel.macho`). This note inventories what
already exists for polaris and lays out two concrete routes. Nothing here has
been built or flashed.

## What UniXNU requires from the firmware

From `docs/porting/uefi-firmware.md` of the base repository:

| Service | Needed for | polaris prospect |
| --- | --- | --- |
| `EFI_GRAPHICS_OUTPUT_PROTOCOL` | UniXNU menu, DarwinBoot framebuffer handoff, XNU console | edk2-msm renders 1080x2160 (Windows installs) — present |
| `EFI_SIMPLE_TEXT_IN_PROTOCOL` | menu input | ButtonsDxe ships for polaris — present |
| `EFI_SIMPLE_FILE_SYSTEM_PROTOCOL` | ESP: config, payloads, kernel | edk2-msm mounts ESP partitions — present |
| `EFI_BLOCK_IO_PROTOCOL` | userdata/block slices | UFS support in edk2-msm — present |
| `EFI_SERIAL_IO_PROTOCOL` | logs where a UART exists | SDM845 phone has no wired debug UART — absent, same as today |
| `EFI_FIRMWARE_VOLUME2_PROTOCOL` | UniXNUDxe boot option registration | only needed for the embedded (firmware-volume) layout |

BootShim conventions: `Resources/Configs/<device>.toml` with `uefi_fd.base`,
`uevi_fd.size`, boot image parameters; the volume is gzipped behind an
Android boot image.

## Existing polaris firmware assets (measured from the trees)

**Renegade / edk2-msm** (`edk2-porting/edk2-msm`, cloned and inspected):

- `Platform/Xiaomi/sdm845/polaris.dsc` + `polaris.fdf.inc`, per-device
  `configs/devices/polaris.conf` (`SOC_PLATFORM=SDM845`).
- `configs/sdm845.conf`: `FD_BASE=0xCE000000`, `FD_SIZE=0x00700000` — the
  firmware lives in the **low** DDR bank, so it cannot collide with this
  port's upper-bank kernel window (`0x100000000 + 0x7cc00000`).
- Display 1080x2160 (`PcdMipiFrameBufferWidth/Height`), ButtonsDxe binary,
  full ACPI set (DSDT/MADT/FADT/GTDT/IORT/PPTT/DBG2), mainline DTB embedded.
- Boot image: Android header v1, packaged by its own `build.sh` +
  `tools/mkbootimg`; booted by `fastboot boot` or flashed to the boot
  partition. Windows ARM64 installs on polaris through it — GOP, UFS, USB
  and buttons are proven on hardware by a large community.
- License mix: GPL drivers plus Qualcomm binary DXEs — usable as a flashing
  candidate, **not** copyable wholesale into a BSD tree.

**uefi-sheng** (UniXNU org, SM8550) is the Mu-Silicium-style reference: a
device package (`Platforms/Xiaomi/shengPkg`) inside the Mu-Silicium tree,
built and packed by the unixnu driver with BootShim. That is the shape
`board.toml [firmware]` eventually expects.

## Route A (recommended first): edk2-msm firmware + ESP boot of UniXNU

Keep the proven edk2-msm polaris firmware as the UEFI provider; run UniXNU
from the ESP instead of embedding it in the firmware volume (the base
documentation explicitly allows the ESP layout).

Steps, each independently verifiable before the next:

1. Build edk2-msm polaris (their CI artifacts exist) — **host only**.
2. Assemble a test ESP: `EFI/BOOT/BOOTAA64.EFI` = UniXNU.efi, plus
   `EFI/UniXNU/Payloads/{kernel.macho, kernel.macho.adt}` with the
   platform-stage ADT (QEMU variant first, then the polaris ADT with
   `darwinboot-memory-range`).
3. Validate that chain in QEMU first with AAVMF — this is exactly what
   `scripts/check-platform-kernel.py` already does; reuse its ESP recipe.
4. On-phone: **temporary `fastboot boot` of the edk2-msm boot.img with the
   ESP reachable** (their multiboot menu can point at `EFI/BOOT/...` on an
   ESP partition). Every flash/boot needs explicit user approval, per the
   safety rules.
5. Watch DarwinBoot's refusal diagnostics first (`boot refused: ...` lines
   are designed for exactly this): memory-map run vs the upper-bank
   constraint is the first unknown. If the largest conventional run is in
   the low bank, widen `darwinboot-memory-range` deliberately — the ADT
   tool already takes the bank as data.

Risks: memory map shape under ABL-inherited UEFI; GOP format differences
(framebuffer stride/RGB vs BGRA) reaching XNU's console; no SerialIO, so all
diagnosis goes through the panel (DarwinBoot draws) — the same constraint
the old ABL chain had.

## Route B (long term): a Mu-Silicium `polarisPkg`

Port the sdm845 platform into the Mu-Silicium layout the way uefi-sheng did
SM8550: device DSC/FDF under `Platforms/Xiaomi/polarisPkg`, config toml with
`uefi_fd` values, GPL + binary DXEs referenced, not copied. This is what
makes `unixnu build firmware` / `unixnu bootimg` work end-to-end and removes
the edk2-msm dependency, but it is weeks of work and cannot be validated
without the same phone boots Route A needs first. Do Route A, then extract
`polarisPkg` from what it proves.

## What this means for board.toml

`[firmware]` stays intentionally absent until Route B exists; Route A does
not go through the unixnu firmware step at all — it uses the edk2-msm boot
image plus an ESP payload, driven by the payload-side tooling.

## Safety

No flashing without explicit approval; keep the ABL/recovery/modem/persist
partitions and the existing boot-partition backup. Renegade images replace
the boot-partition payload when flashed — the same partition the old ABL
chain already used for its experimental kernels.
