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

## Matrix (static evidence, 2026-10-26)

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
| Wi-Fi | chip-dependent | UPSTREAM (Marvell Torus 2) | UNVERIFIED | UNVERIFIED (firmware absent) | UNVERIFIED | UNVERIFIED | firmware from a verified source in the initramfs (profile); LAN only by default |
| DS4 pairing survives reboot | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | USB always works |
| Temperature (k10temp) | UNVERIFIED | UNVERIFIED | UNVERIFIED | UPSTREAM (6.18) | UNVERIFIED | UNVERIFIED | shown only when exposed |
| Fan read/control | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | **UNSUPPORTED** (not exposed; southbridge firmware manages the fan) |
| Suspend / hibernate | — | — | — | — | — | — | **UNSUPPORTED** (kernel option off; targets masked) |
| Reboot straight back into Linux | — | — | — | — | — | — | **UNSUPPORTED** (returns to PS4 system software) |
| Change VRAM without reboot | — | — | — | — | — | — | **UNSUPPORTED** (loader carve-out) |
| Blu-ray drive | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNSUPPORTED (not detected) | UNVERIFIED | UNVERIFIED | — |

Sources: `aday1/ps4-hdmi-blackscreen@5c1c2fc`, `feeRnt/ps4-linux-12xx` README, `meerzulee/linux-psx@2138063` README/STATUS/MESA-TESTING, and `DionKill/ps4-linux-tutorial@82b5249` (setup, issues, information).

## NickLabs consoles (stated by Nick, 2026-10-26)

| | Primary | Secondary |
|---|---|---|
| Model | **CUH-1215A** (original "fat", CUH-12xx) | CUH-2215B (Slim) |
| Southbridge / GPU | Belize / Liverpool | Baikal / Liverpool |
| Wi-Fi/BT | Marvell 88W8897 "Torus 2" | not stated |
| System firmware | 13.50, GoldHEN (BinLoader, FTP) | 13.50 |
| Group | `fat-belize` | `slim-baikal` |
| Orbis target | kernel `aeolia-belize-6.15`, initramfs profile `cuh-1215a` | `baikal-7.2` (not built yet; qualify the fat first) |
| Network rule | LAN only, no public internet | same |
| State | **UNVERIFIED**: stage-0 and full images built, never booted | UNVERIFIED |

These facts are Nick's statements. The stage-0 report (`orbis.stage0=1`) confirms them from the console itself: southbridge line, PCI IDs, the `mwifiex_sdio` firmware request and the HDMI connector. Until then this matrix says "stated", not "verified".

Firmware for the CUH-1215A image: the 10 Liverpool files (hashes unchanged, now also seen byte-identical in the X-F1REBALL-X v1.0 initramfs) and `mrvl/sd8897_uapsta.bin` (b04fee35…, required by the profile). `mrvl/sd8797_uapsta.bin` is included as optional, in case the chip differs. On current loaders, the 8 core GPU microcode files and the EDID are also extracted from the console at boot (see GRAPHICS-REPORT §1).

## Known unknowns that change the build target

1. **Confirmation of the stated hardware** from the stage-0 report, above all the Wi-Fi chip: CUH-12xx consoles exist with 88W8797 and 88W8897.
2. **Firmware revision.** A different Liverpool set is reported as a mismatch and is never silently accepted.
3. **Whether GoldHEN's BinLoader accepts the community `.elf` payload** on this console, or needs the `.bin` built from source (`make loader-payload`).

## Test scope for a hardware qualification receipt

Boot the stage-0 image first (`orbis-logs/` on the USB disk), then run `sudo orbis-qualify` (= `tests/hardware/qualify.sh`) on the full system. It writes a JSON receipt: identity, kernel, Mesa, every `orbis doctor --deep` tier, mode round-trip timings and memory deltas, and the HDMI modeset/TTY transition result. A group moves to `VERIFIED` only with such a receipt committed for the exact Orbis release.
