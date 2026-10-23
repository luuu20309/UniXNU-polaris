# How to apply this to UniXNU/UniXNU (PR checklist)

Files to add in the UniXNU repository (source files in this directory):

| Source here | Destination in UniXNU |
| --- | --- |
| `polaris.en.md` | `docs/devices/polaris.md` |
| `polaris.zh.md` | `docs/zh/devices/polaris.md` |

Edits in UniXNU (exact diffs):

1. `docs/devices/index.md` — add a row to the supported-devices table:

```diff
 | Device | SoC | Support | Device repository | Firmware |
 | --- | --- | --- | --- | --- |
 | Xiaomi Pad 6S Pro (sheng) | SM8550 | Display, Touch, UFS, GPU | [UniXNU-sheng](https://github.com/UniXNU/UniXNU-sheng) | [uefi-sheng](https://github.com/UniXNU/uefi-sheng) |
+| Xiaomi Mi MIX 2S (polaris) | SDM845 | Platform bring-up | [UniXNU-polaris](https://github.com/luuu20309/UniXNU-polaris) | — |
```

2. `docs/zh/devices/index.md` — add the matching row:

```diff
 | 设备 | SoC | 支持功能 | 设备仓库 | 固件 |
 | --- | --- | --- | --- | --- |
 | 小米平板 6S Pro（sheng） | SM8550 | 显示、触控、UFS、GPU | [UniXNU-sheng](https://github.com/UniXNU/UniXNU-sheng) | [uefi-sheng](https://github.com/UniXNU/uefi-sheng) |
+| 小米 MIX 2S（polaris） | SDM845 | 平台移植阶段 | [UniXNU-polaris](https://github.com/luuu20309/UniXNU-polaris) | — |
```

3. `docs/.vitepress/config.mts` — English sidebar (after the sheng entries
   in the `/devices/` section):

```diff
             { text: 'Supported devices', link: '/devices/' },
             { text: 'Xiaomi Pad 6S Pro (sheng)', link: '/devices/sheng' },
+            { text: 'Xiaomi Mi MIX 2S (polaris)', link: '/devices/polaris' },
             { text: 'Unattended test cycle', link: '/devices/sheng-unattended-test' },
```

4. `docs/.vitepress/config.mts` — Chinese sidebar (after the sheng entries
   in the `/zh/devices/` section):

```diff
                 { text: '支持的设备', link: '/zh/devices/' },
                 { text: '小米平板 6S Pro（sheng）', link: '/zh/devices/sheng' },
+                { text: '小米 MIX 2S（polaris）', link: '/zh/devices/polaris' },
                 { text: '无人值守测试', link: '/zh/devices/sheng-unattended-test' },
```

5. Verify per `docs/contributing/documentation.md`:
   `cd docs && npm install && npm run build` (fix any broken links), keep
   both locales in sync, commit as `docs: add polaris device page`.

Suggested PR title: `docs: add polaris (Xiaomi Mi MIX 2S) community port page`

Suggested PR body: point to the device repository, state the evidence level
(platform stage, host+EFI checks green, no hardware boot on this tree), and
note the heritage link to the retired ABL-shim project.
