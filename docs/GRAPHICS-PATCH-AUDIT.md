# Graphics Patch Audit

Each community patch was checked against the exact upstream Orbis pins. For each, the audit asks: does it still apply? Does it duplicate an upstream fix? Does it target our GPU revision? Is it still needed? Machine-readable: `graphics/patches/*/provenance.json`.

## Kernel (`aeolia-belize-6.15` = feeRnt `108ef97`)

| Item | Status in pinned tree | Orbis action |
|---|---|---|
| `ps4_bridge` in-queue ICC delays (650 ms) strand the VMUTE clear, so the first userspace modeset blacks out the panel (aday1 root cause) | **Fixed upstream in this tree**: `cq_delay()` is `#if 0` and the delays are commented out | keep; verified by reading `drivers/gpu/drm/amd/amdgpu/ps4_bridge.c` |
| 1080p60 marked `DRM_MODE_TYPE_PREFERRED` | present | keep |
| 1080p120 (VIC 63) still advertised, so DMs pick 120 Hz on 60 Hz panels | **present** | **Orbis patch `0001-drm-amdgpu-ps4_bridge-advertise-only-1080p60.patch`**. Ported from linux-psx 0011, which does not apply to 6.15.4. `git apply --check` passes on the pinned tree; compile result is in RELEASE-STATUS. |
| `ps4_bridge_pre_enable` writes `HDCPEN_ENC_DIS` into `TSRST` (aday1: "almost certainly meant for HDCPEN") | present (line 388) | **not changed**: no hardware evidence either way. Recorded in BUG-LEDGER (DISP-004) as a hypothesis. |
| `CONFIG_EXTRA_FIRMWARE` embeds MediaTek MT7668 blobs (no recorded licence) despite the "no-built-in-fw" tag name | present | **config fragment sets `CONFIG_EXTRA_FIRMWARE=""`**. Firmware is operator-supplied. |
| aday1 `0003-ps4_bridge-unstrand-the-unmute.patch` (runtime knobs) | written against 6.12.18; author marks it untested | **not imported**. The upstream fix already covers the symptom. Its knobs (`ps4_bridge_icc_delays` etc.) are a good debugging aid if a black screen reappears. |
| linux-psx 6.18/7.2 Liverpool series (36 patches: DCE PLL, DP training, diagnostics) | Baikal-oriented; many are diagnostic | not imported into the Aeolia/Belize kernel; the Baikal target uses linux-psx directly |

## Mesa (pinned `mesa-26.2.5`, `ddaf85e3`)

| Patch | Origin | Applies? | Needed? | Orbis |
|---|---|---|---|---|
| `ps4-mesa.patch`: family IDs/raster/GS/LLVM target | DionKill@57e0f8e (author: froyo) | targets git HEAD | yes | **superseded by linux-psx 0001**, the 26.2.5 port of the same change. The DionKill version places `CHIP_LIVERPOOL/GLADIUS` **before** `CHIP_BONAIRE` in `enum radeon_family`. In 26.2.5 the only ordered comparison (`ac_get_gfx_level`, `family >= CHIP_BONAIRE`) is compensated by extra checks, so it is harmless today but fragile. The linux-psx port places them after BONAIRE. |
| same patch, `clc_helpers.cpp` hunk | DionKill | — | not PS4-specific (LLVM 21 build fix) | dropped |
| same patch, `radeon_surface.c` enum hunk | DionKill | — | unused (radeon winsys not used) | dropped |
| `0001-ps4-graphics-families.patch` | linux-psx@a191018 | yes (`patch -F0`) | yes | **imported** |
| `0002-ps4-uvd-avc-capabilities.patch` | linux-psx | yes | only with the experimental UVD kernel runtime | **excluded** (decode stays software) |
| `0003-addrlib-selected-fmask-entry.patch` | Omarchy psX | yes | yes: browser MSAA allocation abort on Liverpool | **imported** |
| `0004-radv-liverpool-disable-htile.patch` | linux-psx | — | inactive upstream since dev5 | excluded |
| `0005-radv-bound-meta-fill-offset.patch` | linux-psx | yes | yes: keeps the RADV fill shader on MUBUF instead of FLAT on GFX7; linux-psx saw hangs on the FLAT path | **imported**. Directly relevant to the inference investigation (GRAPHICS-REPORT §5). |

All three imported patches were applied **in sequence** to the 26.2.5 sources (`src/amd`, sparse checkout of the `chaotic-cx/mesa-mirror` tag, whose tag object matches linux-psx's record `f9ce5e81`). Diffstat: 6 files, +43/−5. **Mesa itself has not been compiled by Orbis yet.**

## libdrm

| Patch | Content | Orbis |
|---|---|---|
| `ps4-libdrm.patch` / `ps4-lib32-libdrm.patch` | adds `9920/9922/9923 AMD Liverpool` and `9924 AMD Gladius` lines to `data/amdgpu.ids` (marketing names only; space-separated where the file uses tabs) | **dropped**: cosmetic only. Orbis uses Arch's snapshot-pinned libdrm, which removes one patched package to maintain. |

## xf86-video-amdgpu

| Patch | Content | Orbis |
|---|---|---|
| `ps4-xf86-video-amdgpu.patch` | CI-family cursor size 128 → 64 | **imported** onto the pinned tag `xf86-video-amdgpu-25.0.0`. The patch licence is unstated (trivial change to MIT code); confirm with the author before release. |

## Binary packages in DionKill's repo (reference measurements)

`mesa-ps4-26.0.8.218186.60e95b78785` was built with makepkg 7.1.0 against `llvm-libs 23.1.1`. The packager is "Unknown Packager" and the packages are unsigned. `orbis isa-audit` result for `libvulkan_radeon.so`: AVX2/AVX-512 instructions confined to a CPUID-dispatched region with no BMI2 density, so verdict `safe-if-dispatched`. Ubuntu's generic `libgallium` shows the same pattern. The community binaries are therefore not v3 builds; they are simply unpinned.
