# UniXNU on Xiaomi Mi MIX 2S (polaris)

Device port in progress for SDM845 / Snapdragon 845 / Adreno 630.
This uses UniXNU's Darwin 23 architecture (XNU 10063, source-kernel platform
milestone). The retired ABL-shim chain
([ios16_on_polaris_mix2s](https://github.com/luuu20309/ios16_on_polaris_mix2s),
XNU 8796 / iOS 16) is the measured-hardware reference for this tree.

**There is no bootable polaris image, no polaris UEFI firmware and no
accelerated desktop in this repository.** The current milestone is the
platform-stage kernel and its host-verifiable checks. Nothing here has run
on the phone.

## English

### What "transfer from ABL" means here

The old chain booted XNU 8796 through Android's ABL: a boot image with a
custom shim that inherited ABL's framebuffer and UFS state, a forged iPhone X
(D22AP) identity in the ADT, and 93 patches against the SWEET machine
configuration. That chain reached the iOS 16 lock screen with touch — evidence
that belongs to that tree only.

This repository moves the port onto UniXNU's build driver and boot chain:

| Aspect | Old ABL-shim chain | This repository |
| --- | --- | --- |
| Kernel | XNU 8796, MACHINE_CONFIG SWEET, darwin-xnu-build | XNU 10063, BCM2837 4 KiB pages, `unixnu` build driver |
| Build | macOS CI wrapping blacktop/darwin-xnu-build | `scripts/build-platform-kernel.py` on UniXNU `37987aea` |
| Boot | boot.img → custom shim → kernel | UEFI firmware → DarwinBoot → payload kernel.macho (firmware not yet ported) |
| ADT | iPhone X identity, ABL framebuffer/UFS nodes | platform-stage ADT, `darwinboot-memory-range`, no Apple identity |
| Identity | forged D22AP/iPhone10,3, chip-id 0x8015 | none at this stage; prelink target undecided |
| CPU | cpus=4 proven (A55 0-3); A75 4-7 never observed | same topology carried; prefix check migrated; no SMP claim |

Carried-over board facts (measured or source-derived in the old project):
GICv3 at 0x17a00000/0x17a60000, 19.2 MHz timebase, virtual-timer PPI 19,
PSCI SMC, upper DDR bank 0x100000000 (6 GiB: 0x7cc00000, 8 GiB: 0xfcc00000),
4x A55 (MPIDR 0x000-0x300) + 4x A75 (0x400-0x700) with SDM845's Aff1 core
encoding. What is **not** inherited: the lock-screen boot evidence, the
GPIO33/34 bit-bang touch driver, the ABL framebuffer console, and every
XNU-8796 patch — they belong to the retired tree and do not apply to Darwin 23.

### Implemented

- polaris platform ADT (`scripts/make-polaris-adt.py`) with the measured GIC
  addresses, timer INTID 19, 19.2 MHz timebase, PSCI SMC, the 6/8 GiB upper
  DDR bank option and the 4+4 silver/gold CPU prefix.
- ArmCpu override that selects the timer PPI from the ADT and rejects
  malformed properties before GIC MMIO (inherited from the UniXNU-sweet
  override; both SoCs share the GICv3 layout).
- DarwinBoot override that constrains the kernel's physical allocation to the
  polaris upper DDR bank while respecting the real UEFI memory map. It
  supplies the Darwin 23 boot_args ABI.
- Fail-closed CPU prefix check (`scripts/check-cpu-prefix.py`) migrated from
  the old chain's topology validator, adapted to the platform-stage ADT.
- Host checks, QEMU EFI checks and the macOS source-kernel build, all wired
  as GitHub Actions.

Read [NOTES.md](NOTES.md) for parameter provenance and the old-to-new chain
mapping.

### Host validation

Requires Python 3.11+, Clang with ARM64/iOS support, and LLD:

```sh
python3 scripts/check-host.py
python3 scripts/make-polaris-adt.py --output out/polaris.adt
python3 scripts/check-cpu-prefix.py out/polaris.adt --cmdline "-v serial=3 cpus=1"
```

The ADT unit tests and the prefix checker alone need nothing but Python:

```sh
python3 -m unittest discover -s tests -v
```

These checks do not establish physical DMA coherence, PSCI behavior or
firmware execution on the phone.

### EFI and loader validation

With the UniXNU and Mu_Basecore revisions pinned in
[upstream.json](upstream.json) checked out, plus an AArch64 GNU cross
compiler, LLVM tools, QEMU and AAVMF:

```sh
python3 scripts/check-efi.py --base ../UniXNU
```

This builds the UniXNU EFI applications and the polaris DarwinBoot override
and runs ten QEMU cases (menu, driver handoff, ARM64 entry with Darwin 23
boot_args, constrained allocation, and refusal cases). QEMU fixtures are
emulator-only and must never be packed into a phone image.

### Device repository integration

The directory follows UniXNU's device override layout:

```sh
python3 ../UniXNU/unixnu defconfig polaris_defconfig -D . --out out/driver
python3 ../UniXNU/unixnu status -D . --out out/driver
```

`status` must select this repository's ArmCpu and DarwinBoot; the other
modules come from UniXNU at commit `37987aea410691df3c1e0e146a1bfd6127932149`.

### Source-kernel build (macOS, or the CI workflow)

The platform milestone builds with Xcode on macOS:

```sh
python3 scripts/build-platform-kernel.py --base ../UniXNU
```

The full prelink/firmware/userdata stages are intentionally **not** wired:
no polaris UEFI port exists, and the original Apple component prelink target
and trust-cache policy are undecided (the old chain's iPhone X iOS 16 userland
does not ABI-match this Darwin 23 kernel). GitHub Actions offers the same
build as the manual **Darwin 23 platform kernel** workflow.

## 简体中文

### 本仓库是什么

把小米 MIX 2S(polaris / SDM845)的 XNU 移植从旧的 **ABL 引导链**
(ios16_on_polaris_mix2s:XNU 8796 / iOS 16 / 自写 shim,曾临时启动到
iOS 16 锁屏并验证触摸)转移到 **UniXNU 构建体系**(Darwin 23 / XNU
10063 / `unixnu` 构建驱动 / UEFI+DarwinBoot 启动链)的**平台阶段**
设备仓库。旧链的锁屏与触摸证据属于旧内核树,不随基线转移;本仓库
继承的是**板级事实**:GIC 地址、timer PPI 19、19.2MHz 计数频率、PSCI
SMC、上 DDR bank(6/8GB)、4×A55+4×A75 拓扑与 SDM845 的 Aff1 编码。

### 当前状态

- 平台阶段:源码内核(XNU 10063 / BCM2837 4K 页 / 无原厂组件 prelink)
- 已实现:polaris 平台 ADT、ArmCpu/DarwinBoot override、CPU 前缀校验、
  主机/EFI(QEMU)/macOS 内核三条 CI
- 未实现且不宣称:polaris UEFI 固件、真机启动、触摸/显示/GPU、
  iPhone 身份与 prelink 目标(见 NOTES.md 路线)

### 验证与构建

```sh
# 仅需 Python
python3 -m unittest discover -s tests -v

# 需 clang/llvm
python3 scripts/check-host.py

# 设备仓库集成(UniXNU 检出至 upstream.json 的 commit)
python3 ../UniXNU/unixnu defconfig polaris_defconfig -D . --out out/driver
python3 ../UniXNU/unixnu status   -D . --out out/driver

# macOS + Xcode:源码内核
python3 scripts/build-platform-kernel.py --base ../UniXNU
```

推送本仓库到 GitHub 后,`host and EFI checks`(Ubuntu)自动运行,
`Darwin 23 platform kernel`(macOS)手动触发产出 `kernel.macho`。

### 来源与许可

- 板级测量与来源:[ios16_on_polaris_mix2s](https://github.com/luuu20309/ios16_on_polaris_mix2s)(BSD 之外的独立项目)
- 移植模板:[UniXNU-sweet](https://github.com/UniXNU/UniXNU)(设备端口模式)
- 基线:[UniXNU](https://github.com/UniXNU/UniXNU) `37987aea`,BSD-2-Clause-Patent

不包含 Apple 二进制、Qualcomm 固件、设备转储或序列号。
