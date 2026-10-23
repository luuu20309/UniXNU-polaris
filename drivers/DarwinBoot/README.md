# DarwinBoot for polaris

Based on UniXNU/DarwinBoot `12bc9e23fb5f9a7d2715a9e30c021a883db0a310`
(inherited from the sweet device port of the same override).
The complete Darwin 23 boot_args ABI comes from upstream.

The added optional `/chosen/darwinboot-memory-range` is a little-endian
pair of u64 values `{physical_base, bytes}`. It constrains allocation to
the intersection of that range with a real UEFI conventional-memory run.
Page alignment, nonempty size, addition overflow, property size, placement
and duplicates are checked. An allocation failure never falls back outside
the range. An omitted property retains the upstream unrestricted policy.

polaris supplies the upper DDR bank, by default `0x100000000 + 0x7cc00000`
(the 6 GiB variant, safe on both installed sizes; the 8 GiB variant uses
`0xfcc00000`), above the low-bank Qualcomm XPU carve-outs. This replaces the
old ABL-shim chain's fixed kernel placement. The constraint does not
establish that a polaris UEFI port has a correct memory map, nor that its
largest free run can hold the requested kernel window. No polaris UEFI
firmware exists yet; physical validation is pending.

The standalone DSC builds only the loader driver. Upstream's separate
Validation application is not included in this device override.
