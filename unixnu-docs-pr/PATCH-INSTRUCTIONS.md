# How to apply this to UniXNU/UniXNU (PR checklist)

Based on UniXNU `main` as of 2026-10-24 (after PR #94/#95 restructured the
devices index into the official table plus a "Community ports" section with
its own sidebar group). Files to add:

| Source here | Destination in UniXNU |
| --- | --- |
| `polaris.en.md` | `docs/devices/polaris.md` |
| `polaris.zh.md` | `docs/zh/devices/polaris.md` |

Edits in UniXNU (exact diffs):

1. `docs/devices/index.md` — add a row to the **Community ports** table,
   ordered by completion. polaris sits next to sweet (platform bring-up, no
   bootable image), e.g. right after the sweet row:

```diff
 | [Galaxy Tab A7 10.4 (gta4l)](/devices/gta4l) | SM6115 | Board, ADT and firmware build; second repository builds a kernel in CI | NoNameBuilder, ilovemykedi |
 | [Redmi Note 10 Pro (sweet)](/devices/sweet) | SM7150 | Platform and loader bring-up; no bootable image | dipa-studio |
+| [Xiaomi Mi MIX 2S (polaris)](/devices/polaris) | SDM845 | Platform bring-up: ADT, loader checks and source-kernel CI wired; no firmware or hardware run | luuu20309 |
 | [OnePlus 5 (cheeseburger)](/devices/cheeseburger) | MSM8998 | XNU entry bar observed; MMU and root unverified | DinnerABC |
```

2. `docs/zh/devices/index.md` — add the matching row in the same position of
   the Chinese community-ports table (keep the zh table's wording style):

```diff
+| [小米 MIX 2S（polaris）](/zh/devices/polaris) | SDM845 | 平台移植阶段：ADT、加载器检查与源码内核 CI 已接好；无固件或真机运行 | luuu20309 |
```

3. `docs/.vitepress/config.mts` — English sidebar, inside the collapsed
   `Community ports` group (after the sweet entry):

```diff
                 { text: 'Redmi Note 10 Pro (sweet)', link: '/devices/sweet' },
+                { text: 'Xiaomi Mi MIX 2S (polaris)', link: '/devices/polaris' },
```

4. `docs/.vitepress/config.mts` — Chinese sidebar, inside the collapsed
   community group (after the sweet entry):

```diff
                 { text: '红米 Note 10 Pro（sweet）', link: '/zh/devices/sweet' },
+                { text: '小米 MIX 2S（polaris）', link: '/zh/devices/polaris' },
```

   (Verify the exact zh sweet label in the current file before applying; the
   zh sidebar entries may use the Chinese device name.)

5. Verify per `docs/contributing/documentation.md`:
   `cd docs && npm install && npm run build` (fix broken links), keep both
   locales in sync, commit as `docs: add polaris community port page`.

Suggested PR title: `docs: add polaris (Xiaomi Mi MIX 2S) community port page`

Suggested PR body:

> Adds the polaris (Xiaomi Mi MIX 2S, SDM845) community port page in both
> locales, plus its index rows and sidebar entries. The device repository is
> at the platform stage: host checks and EFI loader checks run in CI, the
> Darwin 23 source-kernel build is provided as a manual workflow, and no
> hardware run is claimed. The page links the maintainer's retired ABL-shim
> project (XNU 8796, which reached the iOS 16 lock screen with touch) as
> heritage for the measured board constants, with the full history kept in
> the device repository's docs/HISTORY.md.
