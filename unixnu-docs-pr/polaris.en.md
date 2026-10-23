# Xiaomi Mi MIX 2S (polaris)

polaris is the Xiaomi Mi MIX 2S with the SDM845 SoC, Snapdragon 845 and Adreno 630. The port below is an independent community project. The UniXNU organization does not maintain it.

| Repository | Maintainer | State |
| --- | --- | --- |
| [UniXNU-polaris](https://github.com/luuu20309/UniXNU-polaris) | [luuu20309](https://github.com/luuu20309) | Platform bring-up |

## State

The port is at the platform stage of the Darwin 23 source kernel. It has a polaris platform ADT (GICv3 at 0x17a00000/0x17a60000, virtual-timer PPI 19, 19.2 MHz timebase, PSCI SMC, the 6/8 GiB upper DDR bank option, and a 4x Cortex-A55 + 4x Cortex-A75 prefix with SDM845's Aff1 core encoding), an ArmCpu override that reads and validates the timer PPI from the ADT, a DarwinBoot override that constrains the kernel's physical allocation to the upper bank, and a fail-closed CPU prefix check. Host checks and EFI loader checks pass in CI; the source-kernel platform build is offered as a manual workflow. There is no polaris UEFI firmware, no bootable image, and no touch, display or GPU on this tree.

### Heritage from the retired ABL-shim chain

The same maintainer's earlier project
([ios16_on_polaris_mix2s](https://github.com/luuu20309/ios16_on_polaris_mix2s),
a fork of dipa-studio/ios-sweet on XNU 8796 / iOS 16) temporarily booted to
the iOS 16 lock screen with user-confirmed one- and two-finger touch: a
Synaptics S3330 on GPIO33/34 bit-bang I²C with GPIO99 reset release, bounded
contiguous F12 frame reads and a conservative state machine, plus PSCI
CPU_ON/SGI proven for CPU1-3 with `cpus=4`. That evidence belongs to the
retired tree and does not transfer with the kernel baseline; the measured
board constants are what this port started from. The complete iteration
history — every touch diagnostic stage, the withdrawn GPU command-db map and
the evidence classes of each result — is preserved in the device
repository's
[HISTORY.md](https://github.com/luuu20309/UniXNU-polaris/blob/main/docs/HISTORY.md).

## Build

Host checks (Python 3.11+, clang, lld):

```sh
python3 scripts/check-host.py
python3 scripts/make-polaris-adt.py --output out/polaris.adt
python3 scripts/check-cpu-prefix.py out/polaris.adt --cmdline "-v serial=3 cpus=1"
```

Device repository integration:

```sh
python3 ../UniXNU/unixnu defconfig polaris_defconfig -D . --out out/driver
python3 ../UniXNU/unixnu status -D . --out out/driver
```

The source-kernel platform milestone builds on macOS with Xcode via
`scripts/build-platform-kernel.py`, or through the repository's
*Darwin 23 platform kernel* workflow. The full prelink, firmware, payload
and userdata stages are intentionally not wired: no polaris UEFI port exists
and the original-component prelink target is undecided.
