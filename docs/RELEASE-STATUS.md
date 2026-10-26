# Release Status — 0.1.0 (2026-10-23)

**Bottom line:**
- Orbis has **not** been booted on a PlayStation 4.
- The platform layer, GPU probe, inference harness and kernel were built and exercised on an x86-64 build host (Ubuntu 24.04 container, 2 vCPU, no GPU).
- The package, rootfs and image recipes are written but **not executed end to end**. The build environment could not reach Arch, Fedora or Mesa mirrors.

**Legend:**
- **RUNS (host)**: executed and tested on the build host.
- **COMPILED**: builds; not executed in a meaningful way.
- **WRITTEN**: recipe or script exists and is syntax-checked; not executed.
- **PS4-UNTESTED**: no PS4 evidence from Orbis.
- **OPERATOR**: needs Nick's action or input.

## Platform layer

| Component | Status | Evidence |
|---|---|---|
| Mode engine (plan/execute, fast/reboot/noop/blocked, residue + GPU-client verification, readiness, memory deltas, transition log) | RUNS (host) · PS4-UNTESTED | 14 engine tests on an in-memory systemd model, including a PC→GAMING→AI→PC round trip; real systemd never driven |
| Boot partition adapter (vram.txt semantics mirrored from the loader, bootargs editing, kernel pair install/rollback) | RUNS (host) | 8 tests on fixture FAT directories |
| Boot-protocol / loader-contract checker | RUNS (host) | synthetic headers, plus the real feeRnt `v6.15.4__crashnt-4.7` `bzImage_GCC-11` (sha256 `d8ade1fb…7e37`): protocol 2.15, XLF 0x0023, `CONFIG_X86_PS4=y`, embedded-firmware warning (`tests/evidence/host/bzimage-check-*.json`) |
| Mode generator (systemd, POSIX sh) | RUNS (host) | precedence test with a fake root; never run as a real systemd generator |
| orbisd API v1 + orbis-web dashboard | RUNS (host) | 7 integration tests over real sockets: auth, CSRF, CSP, path traversal, switch round trip. Headless-Chromium screenshots in `docs/img/`. Measured idle PSS: orbisd 16.7 MiB, orbis-web 15.6 MiB |
| AI launcher → llama-server → dashboard chat | RUNS (host) | `tests/evidence/host/ai-launch-e2e.md` (tiny random model, CPU) |
| Capability matrix, hardware inventory, doctor | RUNS (host) | fixture PS4 sysfs/procfs; the real host is correctly "not a PS4" |
| ISA audit | RUNS (host) | flags a `-march=x86-64-v3` binary as unsafe; btver2/baseline safe; Mesa binary `safe-if-dispatched`; Orbis llama.cpp builds (CPU and Vulkan) contain **no** AVX2/FMA/BMI2 (`tests/evidence/host/isa-audit.json`) |
| Package guard (pacman hooks) | RUNS (host) as unit | evaluate + single-use override; hooks never run inside pacman |
| systemd units, logind/sleep/sysusers/tmpfiles, Xorg conf | WRITTEN | not loaded by systemd; `systemd-analyze verify` not available on host |

## Tools

| Component | Status | Evidence |
|---|---|---|
| `orbis-gpu-probe` (C) | COMPILED + RUNS (host) | GL: llvmpipe identified, classified **software**, draw test pixel-exact. Vulkan: SwiftShader, 15/15 compute tests pass and the tier stays UNVERIFIED (software device). Never run on RADV/Liverpool. |
| `orbis-infer-harness` | RUNS (host) | 6 unit tests. Self-test campaigns with fault-injecting stand-ins: pass / page fault / numeric / hang / power-loss resume. Boundaries found and classified correctly (`tests/evidence/host/harness-*`). Real llama.cpp Vulkan on SwiftShader correctly stopped at preflight with `UNSUPPORTED_DEVICE` (no 16-bit storage). **Never run on a PS4 GPU.** |
| `make_tiny_model.py` | RUNS (host) | GGUF loads in llama.cpp b11845 |
| llama.cpp b11845 with Orbis Jaguar flags | COMPILED (host), CPU build RUNS | CPU and Vulkan builds; ISA audit clean |

## Kernel

| Item | Status | Evidence |
|---|---|---|
| Orbis patch 0001 (1080p60 only) | applies (`git apply --check`) + COMPILED (`ps4_bridge.o`, no warnings) | `/home/claude/tools/logs/kernel.log` in the build session |
| Full `aeolia-belize-6.15` build via `kernel/build-kernel.sh` | **COMPILED** (no LTO) · loader-contract check ok · firmware-free | "Kernel build result" below |
| Boot on PS4 | PS4-UNTESTED | — |
| Baikal target | WRITTEN (delegated to Linux psX) | — |

## Graphics

| Item | Status |
|---|---|
| Mesa 26.2.5 + patches 0001/0003/0005 | patches **apply in sequence** to the 26.2.5 AMD sources; **Mesa not compiled**; PKGBUILD meson options validated against 26.2.5 `meson.options` |
| lib32 Mesa, xf86-video-amdgpu | WRITTEN |
| Display fixes on hardware | PS4-UNTESTED |

## Packaging

| Item | Status | Evidence |
|---|---|---|
| PKGBUILDs (orbis-platform, llama-cpp-orbis, linux-orbis-ps4, Mesa ×2, xf86) | WRITTEN | not run through makepkg |
| Build container, `build-packages.sh`, rootfs builder | WRITTEN (`bash -n`) | Arch mirrors and Docker Hub unreachable from the build environment |
| Initramfs builder | RUNS (host) | deterministic round-trip identical; refuses missing or mismatching firmware; never fed real Liverpool firmware |
| USB image builder | partially RUNS | MBR + FAT32 (`mkfs.fat --offset`) + ext4 with files (`mke2fs -d`) verified. **FAT file copy (`mcopy`) not exercised**: mtools unavailable on the host. The device writer correctly refused `/dev/vda` (mounted, not removable). |
| Overlay install/update/uninstall scripts | WRITTEN (`bash -n`) | — |
| SBOM, deterministic source tarball | RUNS (host) | — |

## Operator actions (Nick)

1. **Tell me the CUH model and southbridge.** This decides the kernel target and the qualification group.
2. **Supply the firmware:** your current working `initramfs.cpio.gz`, from which Orbis extracts the Liverpool firmware. Also say which Wi-Fi/BT chip you have, if known.
3. Run `make packages rootfs initramfs image` on a machine with Docker and internet, or let CI do it. Then flash, boot, and run `tests/hardware/qualify.sh` → receipts.
4. Host the `[orbis]` package repository: GitHub Pages works, as DionKill does. Replace `packages.nicklabs.invalid` in `packaging/rootfs/overlay/etc/pacman.conf.in` and set up a signing key.
5. Confirm the licence for Orbis code (MIT is in place) and contact DionKill/centi_07 about the cursor patch's licence.
6. For AI Vulkan: run an inference campaign on the console with a 0.5B–1.5B model, and a second machine running `orbis-infer-harness receive` to keep kernel logs through hangs.

## Kernel build result

**COMPILED · PS4-UNTESTED.** Full `kernel/build-kernel.sh --target aeolia-belize-6.15` finished with exit 0 on 2026-10-23 (1813 s, 2 vCPU, Ubuntu clang 18.1.3).

| | |
|---|---|
| Base | feeRnt ps4-linux-12xx `v6.15.4_crashnt-4.7__no-built-in-fw`, commit `108ef9764583` |
| Orbis patches | `0001-drm-amdgpu-ps4_bridge-advertise-only-1080p60.patch` |
| Release | `6.15.4-orbis-ps4-g108ef9764583-dirty`. "-dirty" only means the Orbis patch is applied on top of the tagged tree. |
| Loader-contract check | **ok**: protocol 2.15, xloadflags 0x23 (`XLF_KERNEL_64`, can-be-loaded-above-4G, EFI handover), relocatable, xz payload, cmdline_size 2047. No errors or warnings. |
| Embedded config | `CONFIG_X86_PS4=y`, `CONFIG_EXTRA_FIRMWARE=""` (**no firmware built in**), `CONFIG_LOCALVERSION="-orbis-ps4"` |
| `bzImage` | 12,456,960 bytes, sha256 `cbc11420cba2ed839a242380361dffe9640b63aea530cb71aacffba46c0ec157` |
| modules tar | sha256 `31f2843abccf334bf84804c17780ed5d5b088f562fae6e2b5e6a815946cd8401` (3 `.ko`). `depmod` was missing on the host, so `modules.dep` is generated at package install time (the PKGBUILD runs depmod). |
| Compiler warnings | 116 lines matching `warning:`, all in upstream code; none in `ps4_bridge.c` |

**LTO:** the first attempt used the upstream config's full Clang LTO and was OOM-killed at the `vmlinux.o` link (exit 137, ~8 GB RAM). Orbis now forces `CONFIG_LTO_NONE=y` in `orbis-common.fragment`, and `build-kernel.sh` verifies it. This costs a little runtime performance but removes a 32 GB-RAM build requirement. The effect on PS4 behaviour is not measured.

**What this does not prove:** that the kernel boots on a PS4, that HDMI comes up at 1080p60, or that amdgpu initialises on Liverpool. All of these wait on `tests/hardware/qualify.sh` on a console.
