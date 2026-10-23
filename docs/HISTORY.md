# Port history: from the ios-sweet fork to the UniXNU build driver

This record keeps the full development history of the polaris (Xiaomi Mi MIX
2S, SDM845) port. The upstream UniXNU device listing summarizes it in a few
lines; nothing here is meant to be weakened or dropped. Evidence classes are
kept apart throughout: **device** = a temporary boot or user observation,
**CI** = compile/link only, **offline** = host tests. They are not
interchangeable.

The retired repository is
[ios16_on_polaris_mix2s](https://github.com/luuu20309/ios16_on_polaris_mix2s);
its per-iteration log is `NOTES.md` there. This file condenses that history
and explains what moved into this repository.

## Origin

The port began as a fork of [dipa-studio/ios-sweet](https://github.com/dipa-studio/ios-sweet),
the XNU 8796 / iPhone X (iPhone10,3, D22AP) iOS 16 infrastructure for the
Redmi Note 10 Pro (sweet, SM7150). polaris shares neither sweet's touch
power tree, its SE7 I²C controller nor its flash parameters: every board
specific value had to be re-derived from the upstream Xiaomi polaris kernel
sources (MiCode `polaris-o-oss`) and measured on the phone.

Key board facts established along the way (all carried into this repository's
ADT and checks):

- Touch: Synaptics S3330 at I²C 0x20 on QUPv3 SE14 (0xa98000, 400 kHz),
  GPIO33/34 SDA/SCL, GPIO99 reset, GPIO23 VIO enable, IRQ125 active-low
  (MiCode `polaris-p0.dtsi` lines 442-495; vendor driver
  `synaptics_dsx_force`). F01 data base observed 0x06; F12 Query8 length 1,
  which does **not** prove the Data1 offset.
- TLMM: SDM845 tiles are not flat — NORTH +0x500000 (GPIO 33/34/99), SOUTH
  +0x900000 (23), EAST +0x100000 (IRQ 125). The first touch probe ACKed with
  wrong addresses because SDA stayed low; fixed against the upstream
  `pinctrl-sdm845.c` (old-chain commit `b549c17`).
- SE14 clocks (GCC): S6 vote bit28 @0x18734, wrapper1 M-AHB bit20 @0x1800c,
  S-AHB bit21 @0x18010, RCG CMD @0x18738. The XNU 0084 probe measured all
  local vote bits absent and RCG `0x80000000` (ROOT_OFF) — clocks not
  confirmed, SE14 MMIO withheld. No clock votes or PMIC writes were ever made
  from XNU.
- CPU: 4x A55 (MPIDR 0x000-0x300) + 4x A75 (0x400-0x700). The tested payload
  used `cpus=4` and logged `requested=4 selected=4 (limit=8)`; PSCI CPU_ON
  and SGI passed for CPU1-3. No A75 has ever been observed starting under
  XNU on this phone.
- GPU: A630v2, 257-710 MHz (Recovery Linux, read-only). No XNU GPU power,
  GMU/HFI, SMMU or rendering was ever performed.
- ABL chain bound: the ramdisk-to-DTB gap limits the payload to
  14,589,952 bytes; the shipped 0090 RELEASE payload was 11,725,6xx bytes.

## Touch bring-up iteration by iteration (old chain, XNU 8796)

| Patch | What it did | Evidence |
| --- | --- | --- |
| 0084 `polaris-touch-diagnostic` | Board-gated fail-closed gates for sweet's SE7 touch/PMIC keys; opt-in read-only GCC/TLMM probe; SE14 MMIO withheld unless clocks confirmed | **device**: user-transcribed console — sweet drivers correctly refused ("SM7150 not confirmed"), `POLARIS TOUCH:` probe printed gcc/s6/ahb/rcg and GPIO states, "SE14 clocks not confirmed; MMIO withheld"; boot then panicked at launchd (`CS_KILLED` 0xe, wrong-platform gate) |
| 0085 `release-ios-platform-optin` | RELEASE build honors `unentitled_ios_sim_launch=1` only when the ADT identifies polaris | **CI** 39361283431 passed; **device**: authorized temporary boot progressed past launchd for the first time |
| 0086 `gpio-rmi4-raw-prototype` | Bounded GPIO33/34 open-drain bit-bang I²C: S3330 F01/F12 PDT read, raw F12 query; no HID, GPIOs restored on exit | **CI** 39363674395; **device**: first iOS **lock screen** reached on polaris (no touch response, by design) |
| 0087 `experimental-one-finger-hid` | Opt-in one-finger HID decoder over a hypothesized 8-byte F12 frame, strict PDT identity, release-on-error, 4-minute bound | **CI** 39365528406; **device**: first temporary boot darkened the Apple logo; a later authorized permanent flash showed the lock screen with no touch. Layout unproven — no touch claimed |
| 0088 `pdt-stage-diagnostic` | Separate PDT stage: first I²C address write failed while GPIO99 (reset) was held low | **CI** 39374820891; no phone action for this artifact |
| 0089 `reset-and-multislot-experiment` | Release GPIO99 first: F01/F12 PDT became readable (`2c230c06` / `49001208`), but eight consecutive frames came back invalid — lock screen touch still dead | **device**: shim-level touchprobe v5/v6 had already proven the released bus, Synaptics ACK at 0x20 and a readable RMI4 PDT (23AF=3280, F01 e3, F12 dd) |
| 0090 `contiguous-f12-frame-and-state` | Replace the eight-slot read with one bounded contiguous read of up to 80 bytes of F12 frame data plus proper state handling | **device (user-confirmed)**: one- and two-finger touch working on the iOS 16 lock screen; released as `polaris-kernel-0090` (kernel-only; full ring never recovered) |

Touch result in one sentence: **the old chain had user-confirmed one- and
two-finger touch on the lock screen, built from a GPIO33/34 bit-bang I²C
transport with GPIO99 reset release, a bounded contiguous F12 frame read and
a conservative state machine — with SE14 hardware I²C, ten-contact support
and long-term stability unproven.**

## CPU and GPU work on the old chain

- Eight-core ADT and an XNU limit of eight were present throughout; the
  upstream comparison (Luoxueowo/ios-polaris 0065/0078) contributed no
  missing path, and its skip-failed-CPU patch was rejected as unsafe (XNU
  discards `PE_cpu_start` errors; a subtracted count cannot represent CPU-ID
  holes). A fail-closed topology preflight (`polaris_cpu_topology.py`,
  `mkpayload --check-polaris-cpus`) was added instead — the direct ancestor
  of this repository's `scripts/check-cpu-prefix.py`.
- GPU: read-only firmware-metadata diagnostics compiled in CI
  (0091, run 39739222780; 26 host tests passed); a fixed-PA ARC command-db
  map (0092) was **withdrawn** because XNU ownership was unproven; 0093 is a
  default-off gate that refuses at its first missing backend. No GPU
  acceleration ever existed.

## Why the transfer, and what transferred

The ABL chain works but is a dead end: a monolithic shim inherits ABL's
framebuffer and UFS state, the SWEET machine configuration drags a second
board's identity through every patch, and the iOS 16 userland ABI locks the
kernel to XNU 8796. UniXNU offers the maintained build driver, the Darwin 23
4 KiB-page kernel, a UEFI/DarwinBoot boot chain with a real memory map, and
per-device override repositories.

Transferred into this repository: every measured board constant above, the
CPU-prefix preflight, the platform ADT shape (GIC, timer, PSCI, memory), and
the bring-up methodology (fail-closed gates, evidence classes, bounded
probes).

Not transferred — deliberately: the 93 XNU-8796 patches (wrong base), the
forged iPhone X identity (an iOS 16 userland demand), the ABL framebuffer
console and UFS handoff (replaced by the UEFI chain), and the touch driver
itself, which must be redesigned as a board-gated Darwin 23 driver before
any touch can be claimed here. **The lock-screen and touch evidence belongs
to the retired tree; this repository starts again from the platform stage.**

## Current status of this repository

See [NOTES.md](../NOTES.md) for the live handoff state and the ordered next
steps (CI green → QEMU native kernel check → polaris UEFI firmware → prelink
and identity decision → touch/display/GPU as Darwin 23 drivers).
