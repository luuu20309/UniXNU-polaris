# UniXNU-polaris 完整交接文档(新 Agent 必读)

> 写作时间:2026-10-26 深夜。此前会话已完成:平台阶段真机验证(root panic
> 边界)、UniXNU 官方设备页(PR #97 已合并)、两个上游 bug 报告(#101/#102)、
> iOS 阶段三大件(prelinked 内核/镜像/trust cache)以及 UFS 移植的初步尝试。
> **本文件是唯一的总入口**,配套文件见 §2。读完再动手,不要跳读。

---

## 0. 一句话状态

小米 MIX 2S(polaris,SDM845)的 UniXNU 移植:**平台阶段已在真机验证到设计
终点**(内核跑完 arm_init→调度器→FIPS POST→BSD→IOKit,panic 在
`Failed to mount root device`——平台内核无根,设计如此);**iOS 阶段
(prelinked 内核 + iPadOS 17.7.11 磁盘 + trust cache)三大件已组装完毕**,
唯一没做的是把镜像刷进手机(Windows fastboot 刷 7.5GB 会崩,解法见 §8)。
同机已由 HackiOS(Luoxueowo 私有仓库,XNU 8796/iOS 16.5)达成主屏+GPU+
多点触控,两系统通过"HackiOS 占 boot 分区、UniXNU 走 fastboot boot 临时
引导"天然共存。

## 1. 手机(唯一真机)

- 小米 MIX 2S(polaris,SDM845,6GB RAM 变体),bootloader 已解锁,
  fastboot 序列号 `5fb5407a`,A-only 单槽老布局(没有 cache/system_b)。
- **进 fastboot**:长按电源 ~10 秒强制重启 → 立刻按住音量下键不放。
- **当前状态**:boot 分区 = HackiOS 4 核镜像;`darwin` 分区(sda22,
  19.1GB)= HackiOS 的 iOS 16.5 磁盘;userdata = 我们的 UniXNU 平台版 ESP
  (256MB FAT32,可引导到 root panic);其余分区原厂。
- **分区关键值**(实测):`userdata` 106.6GB(`0x198D7FB000`);
  `splash` 32MB;boot/recovery 各 64MB;darwin LBA 25374720..30037977
  (Apple APFS GUID `7c3457ef-0000-11aa-aa11-00306543ecac`)。
- 无调试串口(`serial=0` 是正确参数,`serial=3` 是无效占位)。
- 显示:1080×2160,行距 4320 字节,32bpp;ABL splash 帧缓冲 `0x9d400000`。

## 2. 所有文件的位置(本机)

| 项 | 路径 |
| --- | --- |
| **设备仓库(主线工作)** | `C:\Users\luuu\.zcode\workspace\default\UniXNU-polaris\`(git,remote=luuu20309/UniXNU-polaris) |
| UniXNU 基线(pinned `37987aea`) | `C:\Users\luuu\.zcode\workspace\default\UniXNU\` |
| Renegade UEFI 构建树 + boot 镜像 | `C:\Users\luuu\.zcode\workspace\default\edk2-msm\`(fork `luuu20309/edk2-msm`,分支 `polaris-build`);产物 `edk2-msm\out-polaris\boot-polaris.img`(6.4MB,sha256 `534ebe68…`) |
| HackiOS 构建产物 + 交接文档 | `C:\Users\luuu\.zcode\workspace\draft-cfcdb15e-1d80-4d4f-ba33-6a820d5696c2\build-artifacts\`(5.1GB,含 HANDOFF-新Agent必读.md——**HackiOS 侧的总入口,必读**) |
| HackiOS 工作树(WSL) | `/root/hackios` + `/root/hackios/Mu-Silicium` |
| **iOS 阶段成品镜像** | `C:\Users\luuu\Downloads\ios-flash\`:`userdata-with-esp.img`(7.5GB,ESP@0+iPadOS17 磁盘)、`ios-esp.img`(纯 ESP)、`userdata-sparse*.img`(sparse 版) |
| WSL 构建工作区 | `/root/ipad-build`(`userdata-with-esp.img`、sparse 版、脚本副本) |
| prelinked 内核(本地最新) | `UniXNU-polaris\out\prelinked-new\kernel.macho`(51.7MB,sha256 `243165da…`,**UFS 目标已改 darwin 分区**,见 §6) |
| 平台内核(硬件验证版) | `UniXNU-polaris\out\kernel-artifact\kernel.macho`(19.3MB,sha256 `761e2def…`) |
| trust bundle | `UniXNU-polaris\out\trust\trust.bundle`(2554 cdhash) |
| iPad 身份 ADT | `UniXNU-polaris\out\local-only\polaris-ios.adt`(unique-chip-id `0x5415145fc42c0a5d`,**不进 git**) |
| platform-tools(fastboot/adb) | `C:\Users\luuu\platform-tools\platform-tools\`(不在 PATH) |
| GitHub CLI | 已登录 `luuu20309`(repo 权限) |

## 3. 仓库与 CI 概况

- **GitHub**:[luuu20309/UniXNU-polaris](https://github.com/luuu20309/UniXNU-polaris)(public),
  62+ 提交;**11 个本地提交未推送**(UFS 移植尝试,见 §6)。
- **UniXNU 设备页已合并**:PR #97(2026-10-24,维护者 rponeawa),页面
  `docs/devices/polaris.md` + 中文版已进 UniXNU 主仓库。**注意**:合并的是
  "Platform bring-up" 版状态;"真机验证"的状态更新在合并后才推,待后续小 PR 更新。
- **上游 issue**:[#101](https://github.com/UniXNU/UniXNU/issues/101)
  (BCM2837 固定 panic 地址 0x3c0fc000 在非树莓派真机总线错误,修复
  `XNU_DT_PANIC_REGION=y`)、[#102](https://github.com/UniXNU/UniXNU/issues/102)
  (链接后修改 `__text` 使 FIPS POST HMAC 失配,必须重新 seal)。均未回复。
- **三条 CI**:①`host and EFI checks`(Ubuntu,host 测试+EFI/QEMU)——绿;
  ②`Darwin 23 platform kernel`(macOS,内核构建+QEMU native check+ESP)——绿
  (最新 run `40351334124`,产出 prelinked `243165da…`);③`ESP FAT image`
  (按需打 FAT 镜像)。另有 `disasm.yml`(按需反汇编内核符号,排查神器)。
- prelink 内核构建入口:`kernel.yml` 的 workflow_dispatch 输入
  `defconfig=polaris_prelink_defconfig`(平台内核用 `polaris_defconfig`)。

## 4. 两个内核版本,不要混淆

| | 平台内核 | prelinked 内核(iOS 版) |
| --- | --- | --- |
| defconfig | `polaris_defconfig` | `polaris_prelink_defconfig` |
| 大小 | 19.3MB | 49-51.7MB |
| 含原厂组件 | 无 | corecrypto/APFS/AMFI/CoreTrust/Image4/IOStorage/IOHID/IOSurface |
| 已验证 | **真机跑到 root panic 边界** | 仅 QEMU;**真机首测待刷** |
| 根 | 无(设计如此) | iPadOS 17.7.11(已组好镜像) |

## 5. 启动链与已验证程度

```
fastboot boot boot-polaris.img (Renegade UEFI, 临时引导)   ✅ 真机多轮验证
  → UEFI 枚举 userdata 头部 ESP(FAT32)                    ✅ 真机验证
  → UniXNU 菜单(BOOTAA64.EFI, 3 秒倒计时)                ✅ 真机验证
  → DarwinBoot(校验+交接:上bank窗 0x100000000/0x40000000,
    video 0x9d400000 1080×2160 32bpp)                      ✅ 真机验证
  → XNU:绿条(入口)→蓝条(入口探针)→紫条(MMU 前)      ✅ 真机验证
  → 修复两个 bug 后:跑到 root panic(设计终点)           ✅ 真机验证
  → iOS 版:挂载 iPadOS 17 磁盘 → launchd                 ⬅ 待刷待测
```

观察窗口就是手机屏幕:panic 会以白字显示在屏上(`--panic-screen` 探针已注入
ESP 内核副本),**这是唯一的日志通道**(无串口、早期无存储、RAM 日志掉电即失)。

## 6. iOS 阶段:三大件组装与 UFS 移植尝试

三大件(全部完成,位置见 §2):
1. **prelinked 内核**:`kernel.yml` + `defconfig=polaris_prelink_defconfig`
   自动完成(基线构建→IPSW kernelcache 提取→`kernel.prelink()` 合成)。
2. **iPadOS 17.7.11 磁盘**:IPSW 的 OS/SystemOS dmg **本身是裸 APFS 镜像**
   (头即 NXSB,无需解压!),按 SHNGROOT manifest 拼接
   (`scripts/make-userdata-linux.py`,OS@1MiB,OS 卷 sha256 与 sheng pin 一致)。
3. **trust cache**:`scripts/make-trust-bundle.py` 解 DER IM4P(trst/trcs 均
   v2 模块),loader 校验格式与 sheng 的 ValidTrustCaches 一致。
4. **iPad7,1 身份 ADT**:借 sheng 的 make-sheng-adt.py 生成(--ipad-identity
   --chip-id 207 --unique-chip-id 0x5415145fc42c0a5d)后**二进制替换**
   `xiaomi,sheng\0`→`xiaomi,polaris\0`(2 处,等长)。
5. **一体镜像**:`userdata-with-esp.img` = ESP@0..256MB + OS@0x10100000 +
   SystemOS@0x143800000(manifest 偏移平移+重哈希)。

**UFS 移植尝试(11 个未推提交,部分有效)**:目标从 userdata 改为 darwin
分区(19.1GB,Apple GUID,HackiOS 已实测可写);新增
`scripts/prepare-storage-headers.py`(IOStorageFamily 头部源)、
`retarget-polaris-ipad-adt.py`、`polaris_ipados_defconfig`(UFS 指向 darwin)。
**状态**:CI 一度绿(run 40351334124,产出 prelinked `243165da…`),但 UFS
在真机上的行为**零验证**——这是 iOS 阶段最大的技术风险。HackiOS 的
`redwood_ufs.c`(其私有仓库)是同 SoC 的实测参照,移植需其作者授权。

## 7. 真机排查方法论(复用价值最高的一节)

**屏幕是唯一输出**。工具链(全部在本仓库 scripts/):
1. `patch-xnu-entry-probe.py`(源自 sheng,本仓库改进版):向内核副本注入
   阶段横带(蓝=入口、品红=MMU 前)+ panic 上屏 + stage-4 定向探针
   (`--reset-after SYM --in FUNC`:到达该调用 8 秒后 PSCI 自动重启——
   "手机自己重启"=到达,"屏幕永远静止"=没到)。
2. `make-payload-esp.py --probe-extra "..."`:一行参数即可发起一问;
   **每问之前必须重新 seal HMAC**(`seal-source-crypto-kernel.py`,脚本已自动做)。
3. `disasm.yml` workflow:按符号反汇编内核(定位到函数内部调用序列)。
4. 二分节奏:每次一问,范围减半;配合反汇编收敛到指令级。

**案例复盘**(≈10 问 + 2 次反汇编,当天完成):平台内核静默死 → Q1-Q8
定位到 `PE_create_console` → 反汇编发现读固定地址 0x3c0fc000(树莓派
panic log)→ 真-根因 #101。修复后死因变成 FIPS HMAC(真-根因 #102)→
re-seal 修复。两次修复都是 **UniXNU 全体非树莓派真机用户的坑**。

## 8. 刷机:唯一卡点与已知解法

- **现象**:Windows fastboot(37.0.1)刷 >4GB 镜像必然 `std::bad_alloc`
  崩溃(sparse/raw、4096/128KB 块都试过;`img2simg` 同样崩)。
- **已验证可行的替代通道**:**OrangeFox recovery + adb + dd**
  (HackiOS 写 2.5GB darwin 就是这么干的:`adb push 到 /tmp(2.7GB tmpfs 上限,
  大文件分块)→ sha256 对账 → dd of=/dev/block/by-name/XXX → 回读对账`)。
  进 recovery:`fastboot reboot recovery`(手机 recovery 分区里已是 OrangeFox)。
- **注意**:OrangeFox 格式化 data 后 ADB 开关可能重置;手机侧临时文件只能
  用 /tmp,不要写 /data。
- **fastboot 依然可用**于 <250MB 的镜像(ESP 就一直这么刷的)。
- **WSL 直通方案**(usbipd 已装,卡在 WSL 内核 vhci 模块加载失败,`wsl --update`
  报错):电脑重启后先试 `wsl --update` → `usbipd attach --wsl` → WSL 内
  Linux fastboot 刷大镜像。不行就走 OrangeFox 通道。

## 9. 与 HackiOS 的关系(重要边界)

- HackiOS(XNU 8796/iOS 16.5,**私有仓库**,作者 Luoxueowo/OOSEX GANG)已在
  同一台手机:**主屏+GPU 加速+多点触控+UFS 持久写入**。其交接文档
  (`build-artifacts/HANDOFF-新Agent必读.md`)详实,必读。
- **共存方式**:HackiOS 刷在 boot(4 核) + darwin(sda22);UniXNU 走
  `fastboot boot` + userdata/ESP。互不覆盖,天然双系统。
- **ABI 不互通**:HackiOS 的 iOS 16.5 卷不能被 UniXNU(Darwin 23)启动,
  反之亦然。UniXNU 必须用 iPadOS 17.7.11(已备)。
- **授权边界**:`redwood_ufs.c` 等驱动实现在其私有仓库,**未经作者许可不得
  公开引用其源码**;实测事实(分区、常量、行为)可记录。移植驱动时先征得
  作者同意,或仅以其为"行为参照"自行实现。
- HackiOS 侧遗留待办(其文档):7 核镜像刷 boot 固化、触摸二次确认。

## 10. 下一步(建议顺序)

1. **把 11 个未推提交推上去**(§6 的 UFS 尝试 + 新脚本,都在本机)。
2. **刷 iOS 一体镜像**:推荐 OrangeFox adb+dd 通道(§8)写 userdata;
   或重启后试 usbipd 直通。刷完 `fastboot boot` 固件,观察:root mount
   不再 panic → launchd → (期望)锁屏;任何 panic 都有屏显,按 §7 方法继续。
3. **UFS 驱动**:若 UniXNU 的 NativeUfs 在 SDM845 上读不到,对照 HackiOS
   行为(它的 UFS 初始化顺序/时钟投票)修 `polaris_ipados_defconfig` 配置的
   NativeUfs 路径;征得 HackiOS 作者授权可加速。
4. **驱动移植主线**(征得授权后):UFS → 触摸(Goodix)→ 按键 → GPU/Mesa
   (周级)。
5. **文档收尾**:设备页状态更新 PR("Platform stage verified on hardware")。

## 11. 踩坑合集(全记录,每条都真金白银)

**构建/CI 类**
1. UniXNU 基线的 `prepare-xnu-source.py` 匿名调 GitHub API,CI 必撞 403
   rate limit → `scripts/overlay/` 里 git-only 替代版(clone+ls-tree,无
   REST 调用)。上游 token 方案也不可靠。
2. macOS runner 的 Xcode 必须 **26.2**(Darwin 23 的 MIG/IIG 生成器,旧版
   报 ABI 错)——CI 已固定。
3. `actions/checkout` 的 `repository/ref/path` 必须在 `with:` 下,否则
   workflow 秒失败(0 job)。
4. 设备仓库必须自带 `build-arm-driver.py`(平台归档构建器)和
   `patch-xnu-entry-probe.py` 系(探针)——漏了会在构建后段才报错。
5. bootstrap pass 必须同时关掉 `XNU_SOURCE_MOCKFS`(否则 vfs 表引用悬空
   符号,链接失败)。
6. 官方 macos.yml workflow 与两遍构建+patch overlay 机制不兼容——用自己
   的 `build-platform-kernel.py`(已支持 --defconfig)。

**内核/真机类**
7. BCM2837 机器配置的固定 panic 地址 0x3c0fc000:QEMU 宽容,真机总线错误
   静默死(#101)。修复=`XNU_DT_PANIC_REGION=y`。
8. 链接后任何 `__text` 修改(探针/fixup)都使 FIPS HMAC 失配 → 必须 re-seal
   (#102)。QEMU 用未打补丁内核所以 CI 全绿,真机才炸。
9. 心跳像素画在 DarwinBoot 绿条 band 内被淹没;移到底部后仍不显示
   (映射未生效,已知问题,不追——root panic 已证明内核活着)。
10. `serial=3` 是无效占位,本机无串口,正确是 `serial=0`。
11. ADT 二进制改写:compatible 等长替换可行(`xiaomi,sheng`→`xiaomi,polaris`
    2 处);unique-chip-id 属于设备敏感信息,不进 git。

**Linux/WSL/工具类**
12. iPadOS 17 的 dmg 是**裸 APFS 镜像**(NXSB 头),无需 dmg2img——它不认识
    这种格式会报 corrupted。(`scripts/udif-extract.py` 是备用的 UDIF/LZFSE
    解包器,含 pyliblzfse。)
13. mtools 的 `mmd` 一次只能建一级目录,且路径要 `::/EFI` 带前导斜杠。
14. Windows fastboot >4GB 必崩(#8 节);OrangeFox adb+dd 是已验证通道。
15. WSL 会莫名崩(getpwuid/E_UNEXPECTED),`--shutdown`+重启电脑可解;
    崩溃中 `wsl --update` 也会失败,先重启电脑。WSL 装在 **G 盘**
    (`G:\wsl\ubuntu`),G 盘掉线=WSL 起不来。
16. Git Bash 调 adb/fastboot:`export MSYS_NO_PATHCONV=1`(防 `/vendor/...`
    被改写);大传输走 `adb push`+手机侧 dd,不要 `adb exec-out > file`。

## 12. 关键参考链接

- [UniXNU](https://github.com/UniXNU/UniXNU)(基线 `37987aea`)·
  [文档](https://unixnu.com)·[UniXNU-sheng](https://github.com/UniXNU/UniXNU-sheng)(移植模板)
- [设备仓库](https://github.com/luuu20309/UniXNU-polaris)·
  [edk2-msm fork](https://github.com/luuu20309/edk2-msm)(polaris-build 分支)·
  [Renegade polaris 文档](https://renegade-doc.readthedocs.io/en/latest/devices/sdm845/polaris/index.html)
- [HackiOS 公开半边](https://github.com/oosexgang/hackios-xnu-userland)(用户态/APFS 构建)
- 上游 issue:[#101](https://github.com/UniXNU/UniXNU/issues/101) /
  [#102](https://github.com/UniXNU/UniXNU/issues/102);设备页 PR #97(已合并)
- IPSW:iPad_Pro_HFR_17.7.11_21H461(直链在 board.toml [ipsw],sha256 pin 齐全)

## 13. 安全与纪律(继承自旧链,继续有效)

不进仓库:Apple 镜像/固件、设备转储、序列号、unique-chip-id。真机操作
(刷写/临时引导)先向用户确认;boot/darwin 分区现状属 HackiOS,**改动前
必须问用户**。编译成功≠真机可跑,证据分级:device/CI/offline 不可互换。
