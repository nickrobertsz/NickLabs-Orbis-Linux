# Hardware Matrix

Machine-readable source: [`manifests/capability-matrix.json`](../manifests/capability-matrix.json). On a console, run `orbis capabilities` (static + runtime) or `orbis doctor`.

## Rules

1. Evidence is scoped to a **qualification group**: form factor × southbridge. A Baikal Slim result never applies to an Aeolia fat console.
2. States:
   - `VERIFIED`: an Orbis release was tested on that group, with a receipt in `tests/hardware/receipts/`. **There are none yet.**
   - `UPSTREAM_REPORTED`: another project recorded a pass on one console of the group, with a different stack.
   - `UNVERIFIED`: no evidence either way.
   - `UNSUPPORTED` / `BLOCKED`: known not to work, or waiting on a named prerequisite.

   Runtime checks add `PASSED_ON_THIS_DEVICE` / `FAILED` for the current boot only.
3. An Orbis policy can be stricter than upstream. Example: upstream installs to the internal HDD; Orbis does not.

## Identifying a console

| Signal | How Orbis reads it | What it tells you |
|---|---|---|
| Sony PCI functions `104d:908f…90a4` | sysfs PCI scan | Aeolia |
| `104d:90c8…90cf` | sysfs | Belize |
| `104d:90d7…90de` | sysfs | Baikal |
| AMD `1002:9920/9922/9923` | sysfs | Liverpool GPU (fat/slim) |
| AMD `1002:9924` | sysfs | Gladius GPU (Pro) |
| `hardware.model = "CUH-xxxx"` | `/etc/orbis/orbis.toml` | operator declaration; needed for Belize, which ships in fat, slim and Pro consoles |

| Revision family | Models | Southbridge(s) | Group(s) |
|---|---|---|---|
| Fat ("phat") | CUH-10xx, 11xx, 12xx | Aeolia (early), Belize (late 12xx) | `fat-aeolia`, `fat-belize` |
| Slim | CUH-20xx, 21xx, 22xx | Belize, Baikal | `slim-belize`, `slim-baikal` |
| Pro | CUH-70xx, 71xx, 72xx | Belize, Baikal (+1 GiB DDR3 not exposed to Linux) | `pro-belize`, `pro-baikal` |

The HDMI transmitter also differs: MN86471A on CUH-11xx and MN864729 on CUH-12xx and later (aday1). Wi-Fi/BT chips vary even within a model: Marvell 88W8797 "Torus 1", Marvell 88W8897 "Torus 2" (CUH-1215/1216), or MediaTek MT7668.

## Matrix (static evidence, 2026-10-23)

| Capability | fat-aeolia | fat-belize | slim-belize | slim-baikal | pro-belize | pro-baikal | Orbis policy |
|---|---|---|---|---|---|---|---|
| Kernel boots via loader | UPSTREAM (6.15.4 crashnt-4.7, CUH-12xx) | UPSTREAM (CUH-1215/1216, 6.15.4) | UNVERIFIED | UPSTREAM (7.2.11, one Slim B1) | UNVERIFIED | UPSTREAM (5.4 only; no 3D) | — |
| External USB SSD root | UPSTREAM | UNVERIFIED | UNVERIFIED | UPSTREAM | UNVERIFIED | UNVERIFIED | primary install target |
| Internal HDD root | UPSTREAM (slow) | UPSTREAM | UNVERIFIED | UNSUPPORTED | UNVERIFIED | UNSUPPORTED | **UNSUPPORTED** (never writes Sony partitions) |
| 1080p60 HDMI + desktop modeset | UPSTREAM (black-screen fix confirmed) | UNVERIFIED | UNVERIFIED | UPSTREAM | UNVERIFIED | UNVERIFIED | 1080p60 pinned; 120 Hz mode removed (kernel patch 0001) |
| Hotplug / EDID | UNSUPPORTED (EDID empty, hard-coded modes) | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | headless operation never depends on a monitor |
| 4K (Pro) | — | — | — | — | UPSTREAM (6.18+) | UPSTREAM (6.18+) | not in v1 kernel (6.15) |
| Hardware OpenGL | UPSTREAM (radeonsi, GL 4.6) | UNVERIFIED | UNVERIFIED | UPSTREAM (GL 4.6) | UNVERIFIED | UNVERIFIED | runtime-checked (`gpu.gl.hw`) |
| Vulkan graphics (sustained) | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED (hangs on 26.2.5 dev2/dev3; dev5 passed on one console) | UNVERIFIED | UNVERIFIED | not advertised |
| Vulkan compute correctness | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | runtime-checked (`orbis doctor --deep`) |
| Vulkan LLM inference | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | **BLOCKED** until an inference-harness gate passes |
| UVD decode / VCE encode | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED (experimental) | UNVERIFIED | UNVERIFIED | **UNSUPPORTED** in v1 (software decode) |
| HDMI audio | UNVERIFIED | UNVERIFIED | UNVERIFIED | UPSTREAM (44.1 kHz only) | UNVERIFIED | UNVERIFIED | — |
| Ethernet | UNVERIFIED | UNVERIFIED | UNVERIFIED | UPSTREAM (`psx0`, experimental) | UNVERIFIED | UNVERIFIED ("may not work") | NetworkManager manages `psx0` |
| Wi-Fi | chip-dependent | UPSTREAM (Marvell Torus 2) | UNVERIFIED | UNVERIFIED (firmware absent) | UNVERIFIED | UNVERIFIED | firmware operator-supplied |
| DS4 pairing survives reboot | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | USB always works |
| Temperature (k10temp) | UNVERIFIED | UNVERIFIED | UNVERIFIED | UPSTREAM (6.18) | UNVERIFIED | UNVERIFIED | shown only when exposed |
| Fan read/control | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | **UNSUPPORTED** (not exposed; southbridge firmware manages the fan) |
| Suspend / hibernate | — | — | — | — | — | — | **UNSUPPORTED** (kernel option off; targets masked) |
| Reboot straight back into Linux | — | — | — | — | — | — | **UNSUPPORTED** (returns to PS4 system software) |
| Change VRAM without reboot | — | — | — | — | — | — | **UNSUPPORTED** (loader carve-out) |
| Blu-ray drive | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNSUPPORTED (not detected) | UNVERIFIED | UNVERIFIED | — |

Sources: `aday1/ps4-hdmi-blackscreen@5c1c2fc`, `feeRnt/ps4-linux-12xx` README, `meerzulee/linux-psx@2138063` README/STATUS/MESA-TESTING, and `DionKill/ps4-linux-tutorial@82b5249` (setup, issues, information).

## Known unknowns that change the build target

1. **Nick's exact CUH model and southbridge.** The v1 default is a fat console with Aeolia or Belize (`aeolia-belize-6.15` kernel). Baikal uses the `baikal-7.2` target instead.
2. **The Wi-Fi/BT chip on Nick's console.** It decides which firmware group the initramfs needs.
3. **Firmware revision.** The Liverpool firmware hashes in `manifests/firmware.json` were recorded from one Baikal console. A different set is reported as a mismatch, never silently accepted.

## Test scope for a hardware qualification receipt

Run `tests/hardware/qualify.sh` on the console. It writes a JSON receipt: identity, kernel, Mesa, every `orbis doctor --deep` tier, mode round-trip timings and memory deltas, and the HDMI modeset/TTY transition result. A group moves to `VERIFIED` only with such a receipt committed for the exact Orbis release.
