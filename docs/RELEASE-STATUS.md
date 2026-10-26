# Release Status — 0.2.0 (2026-10-26)

**Bottom line:**

- **Not booted on a PS4.** Orbis has never been run on the target console (CUH-1215A, FW 13.50, GoldHEN). Every hardware-dependent capability is **UNVERIFIED**.
- **Ready to flash now:** a **stage-0 image** for the CUH-1215A. It contains the Orbis kernel, the Orbis initramfs and the verified firmware. It was built and checked on the Orbis build host (deterministic, fsck clean, byte-for-byte read-back).
- **Full image: built on Nobara.** The installable PC · GAMING · AI image comes from **one command on Nobara** (`packaging/release.sh`). Its package, rootfs and full-image steps are written and syntax-checked, but not executed here: this build environment still cannot reach Arch mirrors, Docker Hub or freedesktop.org. Those steps are the first thing to run on Nobara.

**Legend:**

| Label | Meaning |
|---|---|
| **RUNS (host)** | executed and tested on the x86-64 build host (Ubuntu 24.04 container, 2 vCPU, no GPU) |
| **BUILT** | the artifact was produced here and checked |
| **COMPILED** | builds; not executed meaningfully |
| **WRITTEN** | syntax-checked; not executed |
| **PS4-UNTESTED** | no Orbis evidence from a console |

## What changed since 0.1.0

| Area | Status | Evidence |
|---|---|---|
| Community references (X-F1REBALL-X v1.0: initramfs, 1024 MB payload, Belize 7.1.7 kernel) | **RUNS (host)**: downloaded; SHA-256 equal to Nick's values | `manifests/references.json`, `tests/evidence/host/references-and-loader-2026-10-26.md` |
| Firmware in that initramfs | **verified**: 10/10 Liverpool hashes equal `manifests/firmware.json`; Marvell 8897/8797 recorded, and equal to feeRnt/ps4-linux-initramfs; no EDID | same |
| initramfs builder: profiles, required/optional groups, provenance manifest, no `--allow-missing` | **RUNS (host)** · 17 tests | `tests/unit/test_initramfs.py` |
| Orbis patches to initramfs-ps4@c21a720 (explicit `root=` wins, root wait + fsck, internal drive opt-in) | **RUNS (host)**: self-check of the real patched `init`/`functions.sh` with stubbed devices, 5/5 cases; gates every build | build manifest `self_check` |
| Stage-0 report overlay (busybox sh) | **RUNS (host)** with stubs (report sections, no-partition path) · PS4-UNTESTED | — |
| CUH-1215A initramfs | **BUILT**: 73 entries, sha256 `f2c15884…0960`. Identical from a fresh clone and from a `.git`-less packaged tree. **Private** (firmware) | `tests/evidence/host/initramfs-cuh-1215a.manifest.json` |
| Firmware-free initramfs | **BUILT**: sha256 `a614e9c7…3119`, redistributable | `initramfs-firmware-free.manifest.json` |
| Built-in FAT32 writer (replaces the mtools requirement) | **RUNS (host)** · 6 tests: long names, root-directory growth, duplicates, full disk, determinism; `fsck.fat` clean | `tests/unit/test_image.py` |
| Stage-0 USB image, CUH-1215A | **BUILT**: 1 GiB, xz 22.5 MB; FAT read-back OK; `fsck.fat` clean; `e2fsck` clean; bit-identical on rebuild. **Private** | `tests/evidence/host/stage0-image-cuh-1215a.json` |
| `release.sh --stage0-only` end to end (fetch → verify → clone pinned source → kernel tar check → initramfs ×2 → image) | **RUNS (host)**: 44 s | — |
| Kernel package reusing the released build (`ORBIS_KERNEL_OUT`): SHA256SUMS + loader check | checks **RUN (host)** on the released tarball; makepkg not run | — |
| LAN-only nftables policy + `orbis-lan-only.service` + doctor `network.lan_only` | policy logic **RUNS (host)** · 2 tests; ruleset never loaded into a kernel here | `tests/unit/test_platform.py` |
| Local `[orbis]` repo in the image, signing, LAN serving, `orbis-update --orbis-only` | dry-run path **RUNS (host)** against a sample `pacman.conf`; signing and pacman never run | — |
| First-boot login (SSH key / password hash, forced change, wheel sudo) | build-time refusals **RUN (host)**; rootfs never built | — |
| `send_payload.py` (GoldHEN BinLoader :9090, private addresses only) | **RUNS (host)** against a local listener: 320936/320936 bytes, hash equal | — |
| `verify_firmware_provenance.py` | **RUNS (host)** on a mock rootfs (`.xz` Arch-style file detected as identical) | — |
| ps4-linux-loader payload from source (`make loader-payload`) | **WRITTEN**: building the external loader was blocked by this session's permission policy; the prebuilt payload is hash-verified instead | — |
| Packages, rootfs, full image, SBOM via `release.sh` | **WRITTEN** (`bash -n`); needs Nobara + Podman + internet | — |

## Carried over from 0.1.0 (unchanged)

| Component | Status |
|---|---|
| Mode engine, orbisd API, dashboard, CLI, doctor, capabilities, ISA audit, package guard | RUNS (host) · PS4-UNTESTED |
| `orbis-gpu-probe`, `orbis-infer-harness` | RUNS (host) on llvmpipe/SwiftShader · never on RADV/Liverpool |
| llama.cpp b11845 (btver2 flags) | COMPILED; CPU build runs; ISA audit clean |
| Mesa 26.2.5 + patches 0001/0003/0005 | patches apply; **not compiled** (needs Nobara build) |
| Kernel `aeolia-belize-6.15` | COMPILED (below) · PS4-UNTESTED |

## Kernel build result (unchanged since 0.1.0)

**COMPILED · PS4-UNTESTED.**

| | |
|---|---|
| Base | feeRnt ps4-linux-12xx `v6.15.4_crashnt-4.7__no-built-in-fw`, commit `108ef9764583` + Orbis patch 0001 |
| Release | `6.15.4-orbis-ps4-g108ef9764583-dirty` ("-dirty" = Orbis patch on top of the tag) |
| Loader contract | **ok**: protocol 2.15, xloadflags 0x23, relocatable, xz, cmdline_size 2047 |
| Config | `CONFIG_X86_PS4=y`, `CONFIG_EXTRA_FIRMWARE=""`, `CONFIG_LTO_NONE=y`. Built in: amdgpu, `MWIFIEX_SDIO`, `BT_MRVL_SDIO`, MT76, vfat + NLS 437/8859-1, USB storage/UAS, nf_tables (inet, ct, reject, log, limit) |
| `bzImage` | 12,456,960 bytes, sha256 `cbc11420cba2ed839a242380361dffe9640b63aea530cb71aacffba46c0ec157` |
| modules tar | sha256 `31f2843abccf334bf84804c17780ed5d5b088f562fae6e2b5e6a815946cd8401` |

Full Clang LTO OOM-killed the link on an 8 GB host, so Orbis forces `CONFIG_LTO_NONE=y`. The runtime effect on the PS4 is not measured.

## Findings this release acted on

- **BOOT-007 (S1).** initramfs-ps4 discovers a `psxitarch`/internal install **before** honouring `root=`, so an older community install could boot instead of Orbis. Fixed in the Orbis initramfs.
- **BOOT-008.** The upstream init opens the PS4 internal drive on every boot. Orbis makes that opt-in.
- **BOOT-009.** The loader looks up each boot file separately (USB, then internal). Orbis images always carry all four files.
- **FW-001.** The loader prepends a cpio with the HDD key, the TV EDID and 8 GPU microcode files from the console. Orbis files of the same name override it; UVD/VCE come only from the initramfs.
- **TOOL-003 (S1).** Images had no usable first-boot login. Fixed.
- **LIC-003.** Marvell blobs are not yet proven identical to linux-firmware. `release.sh` checks them against Arch `linux-firmware-marvell` on Nobara.

## Firmware and redistribution

| Artifact | Contains firmware | May be redistributed |
|---|---|---|
| Source tarball, git bundle | no | yes (MIT/GPL per component) |
| `initramfs-firmware-free`, images built from it | no | yes |
| `initramfs-cuh-1215a`, the CUH-1215A stage-0 and full images | Liverpool (rights unknown), Marvell (rights pending LIC-003) | **no**: keep private (`redistributable: false` in their manifests) |
| Kernel build tarball | no (`CONFIG_EXTRA_FIRMWARE=""`) | yes, with the GPL source (pinned commit + patch, in the source tarball) |

## Remaining physical-console qualification

See **[HARDWARE-QUALIFICATION.md](HARDWARE-QUALIFICATION.md)**:

- **Q0:** stage 0 (payload, kernel, HDMI, amdgpu firmware, Wi-Fi chip);
- **Q1:** full boot, login, doctor, GL/Vulkan, LAN-only, Wi-Fi, time;
- **Q2:** personalities and switching (`orbis-qualify`), HDMI after each switch, audio, controller, AI on CPU;
- **Q3:** the GPU inference campaign;
- **Q4:** recovery paths.

## Operator actions (Nick)

1. **Flash and boot the stage-0 image** (INSTALL-NOBARA §3) and send back `orbis-logs/stage0-001.txt` and `-dmesg.txt`. This confirms the stated hardware and the kernel/firmware path.
2. **On Nobara, run the full build:** `packaging/release.sh --profile cuh-1215a --kernel-tar … --ssh-key ~/.ssh/id_ed25519.pub --sign`. Send back its output, or the failing step.
3. **Flash the full image** to a USB 3 SSD, boot it, run `sudo orbis-qualify --model CUH-1215A`, and send back the receipt.
4. Keep `out/keys/gnupg` backed up if you sign. Never share the CUH-1215A images (firmware).
5. Still open from 0.1.0: confirm the Orbis code licence (MIT is in place), and ask DionKill/centi_07 about the cursor patch licence.
