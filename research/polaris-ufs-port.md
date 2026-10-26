# Polaris iPadOS root transport: current port notes

This is the next implementation step toward the local iPadOS 17.7.11 image.
The platform-only boot remains the default; nothing in this note authorizes or
performs a phone write.

## Hardware and source facts

- The Polaris ABL-shim tree's `shim/ufs.c` uses UFSHCI at `0x01d84000` and
  labels it `sweet and polaris`. That is measured-chain evidence for the old
  ABL path, not proof that the new Renegade UEFI handoff leaves the same state.
- The Qualcomm SDM845 device tree describes `qcom,sdm845-ufshc` at
  `0x01d84000`, with a `0x2500` register window. The Qualcomm Android SDM845
  tree assigns the UFS stream ID `0x720` through `apps_smmu`.
- The pinned UniXNU NativeUfs source is currently an SM8550/sheng port. It
  matches `qcom,sm8550-ufshc`, uses stream ID `0x60`, and its optional GCC
  inspection reads SM8550 clock-controller offsets. Enabling it unchanged on
  Polaris would not be a valid port.

## Polaris implementation in this checkout

- `make-polaris-adt.py` now describes the SDM845 UFS controller as a passive
  ADT node. It can also add the iPad7,1/J120 root identity while retaining the
  Polaris board tree. This is a minimal Polaris bring-up ADT plus identity
  fields, not a copy of the full Apple iPad ADT or proof that all iPadOS
  drivers have the properties they expect. Describing the node does not access
  the controller.
- The existing iPad-identity ADT in `/root/ipad-build` was copied from the
  sheng profile: it named the SoC/UFS as SM8550 and its boot CPU as Cortex-A715.
  `retarget-polaris-ipad-adt.py` makes a separate copy with SDM845/UFS and the
  Polaris boot CPU string while preserving its iPad identity. A byte-level
  check shows that the source does not decode cleanly using this checkout's ADT
  node/property format, so equal-length string replacement is not evidence of
  a usable DarwinBoot ADT. The generated `/root/ipad-build/polaris-ios-ufs.adt`
  is quarantined as an unvalidated candidate and must not be packaged or
  flashed. The retarget tool now rejects inputs that fail a full structural
  parse. Fresh minimal candidates were generated as
  `/root/ipad-build/polaris-ios-platform-id-6g.adt` and
  `/root/ipad-build/polaris-ios-platform-id-8g.adt`; they carry only the
  `iPad7,1/J120` root identity over the generated Polaris tree. Select the
  correct RAM profile from the phone's actual RAM size, then validate
  DarwinBoot and iPadOS expectations before packaging. OrangeFox now confirms
  6 GB RAM on this specific 128 GB phone, so the 6 GB candidate is selected.
- `build-arm-driver.py NativeUfs` copies the pinned driver into the build tree
  and applies a small, drift-checked overlay: accept the SDM845 compatible,
  use stream ID `0x720`, use the SDM845 `0x2500` register window, find the
  phone's separate `darwin` root partition, and refuse the SM8550-only GCC probe. The pinned
  UniXNU submodule itself is not edited.
- OrangeFox read-only inspection identified this 128 GB phone as a 6 GB RAM
  model. `userdata` starts with the `POLARISESP` FAT boot volume; `darwin` is
  a separate 19,100,704,768-byte partition. The initial SHNGROOT image
  mistakenly embedded a compressed OS DMG with a non-sector-aligned length.
  The new `extract-udif-lzfse.py` decoded the ULFO image to a 6,077,580,800
  byte raw GPT disk with valid GPT CRCs. The rebuilt
  `/root/ipad-build/out/darwin-root.img` is 8,151,629,824 bytes, has a valid
  SHNGROOT header digest, and fits the `darwin` partition. It has not been
  flashed.
- `polaris_ipados17_defconfig` is a separate experimental configuration with
  NativeUfs enabled. The existing platform and mockfs profiles stay unchanged.

## Remaining gates

1. Several macOS/Xcode build issues have been found on the experiment branch.
   Run `40337260112` exposed an unsupported global Clang warning flag; run
   `40338619715` and `40340235465` showed the pinned UniXNU revision
   `37987aea...` has neither an `IOStorageFamily` kernel reference nor a feed
   definition. The project now defines that source at its pinned public
   revision. Runs `40342688000`–`40343863307` then showed the registration gate
   was checking the wrong key: the pinned defconfig parser strips `CONFIG_`, so
   the actual key is `XNU_NATIVE_UFS`. The gate now uses that parsed key and
   forcibly registers the pinned source. Run `40345249877` confirmed the
   storage source and headers are now fetched; NativeUfs compilation reached
   an existing signed-to-unsigned status assignment under `-Werror`. The
   overlay now makes that conversion explicit. Rerun and address any further
   compiler/linker diagnostics. Run `40348020579` then exposed an upstream
   optional verified-block timing counter used outside its feature guard; the
   Polaris overlay now guards the read timing path with the same condition.
   Run `40349077995` then compiled NativeUfs and reached the final source
   kernel link, which lacked `IOBlockStorageDevice` base-class definitions.
   A pinned-source archive builder now supplies that class to the link.
2. Validate the 6 GB minimal ADT against DarwinBoot/iPadOS and determine
   whether additional Apple tree properties are required before use.
3. Build a distinct ESP entry with `native-ufs-root=1` using the new kernel and
   validated ADT. The existing `/root/ipad-build/out/ios-esp.img` predates the
   NativeUfs port and is not that image. Then test the new image as a temporary
   boot. The first phone result must establish that UEFI left UFS powered, that
   the SDM845 SMMU stream mapping is safe, and that the SHNGROOT manifest is
   read correctly from `darwin` while `userdata` retains the ESP.
4. Only after the root windows publish should APFS/root-mount and iPadOS
   userspace failures be debugged. A successful kernel link alone is not
   evidence that the phone mounted or booted iPadOS.

The current local iPadOS images remain host-built artifacts. They have not
been flashed or hardware-tested by this port.
