# Changelog

## 0.2.0 — 2026-10-26 — installable release pipeline (not hardware-qualified)

Target hardware stated by Nick: CUH-1215A (fat, Belize, Liverpool, Marvell 88W8897), FW 13.50, GoldHEN, LAN only.

### Added
- **Hardware profiles for the initramfs** (`manifests/initramfs-profiles.json`): `cuh-1215a`, `aeolia-belize-generic`, `firmware-free`.
  - Required groups refuse on any missing or mismatching file, and there is no `--allow-missing`.
  - Optional groups (EDID, 88W8797) are included only when present and verified.
  - Every build writes a manifest with hashes, provenance and a `redistributable` flag.
- **Firmware source.** The X-F1REBALL-X v1.0 community initramfs is fetched and verified by SHA-256 (`manifests/references.json`, `make references`). All 10 Liverpool hashes match. Marvell 88W8897/8797 hashes are recorded, corroborated by feeRnt/ps4-linux-initramfs.
- **Orbis patches to initramfs-ps4@c21a720:**
  - an explicit `root=` wins over psxitarch/internal discovery;
  - the init waits 30 s for the root device and runs fsck;
  - the PS4 internal drive is opened only with `orbis.ps4hdd=1`.

  A self-check gates every build.
- **Stage-0 images** (`--stage0`, `make stage0`). The initramfs writes a hardware report to `ORBIS-BOOT/orbis-logs/` and stops in its rescue shell. No root filesystem is needed.
- **Built-in FAT32 writer** (`packaging/image/fat32.py`): long file names, deterministic, read-back, `fsck.fat`. mtools is now optional. Images are bit-reproducible.
- **Local package repository inside the image** (`file:///var/lib/orbis/repo`):
  - optional local signing key (`orbis-repo-key.sh`);
  - LAN serving (`orbis-serve-repo.sh`);
  - `orbis-update --orbis-only --from URL` and `--import-key`.
- **LAN-only network policy** (`orbis-lan-only.service`, nftables) with the doctor check `network.lan_only`. LAN NTP drop-in. Kodi is the offline GAMING frontend on LAN-only images.
- **One-command release** (`packaging/release.sh`, `make release`, `make release-stage0`) for Nobara/Podman. The kernel package reuses the released kernel build (`ORBIS_KERNEL_OUT`).
- **Console login at first boot.** Builds require an SSH key and/or a password hash, the password must be changed at first login, and `%wheel` gets sudo. Without this, images had no usable login.
- New tools: `send_payload.py` (GoldHEN BinLoader :9090, LAN addresses only), `verify_firmware_provenance.py`, and `orbis-qualify` installed on the console.
- New docs: INSTALL-NOBARA (exact commands) and HARDWARE-QUALIFICATION (the remaining physical checks).

### Fixed
- `orbis-update --orbis-only` options extraction (found by the dry-run test before release).
- Relative patch paths in the initramfs builder.

### Known limitations
- Nothing has been booted on a PS4 by Orbis yet.
- Packages, rootfs and the full image have not been built in the Orbis build environment, which cannot reach Arch mirrors or Docker Hub. They build on Nobara with `release.sh`.
- The loader payload was not built from source here; the recipe is `make loader-payload`.


## 0.1.0 — 2026-10-23 — first source handoff (not a hardware-qualified release)

### Added
- Platform layer (`platform/orbis`): mode engine, orbisd (Unix-socket API v1), orbis-web LAN dashboard, `orbis` CLI, doctor, capability matrix, hardware inventory, boot-partition adapter for the PS4 loader (`vram.txt`/`bootargs.txt`, kernel pair install/rollback, boot-protocol checker), memory budgeting and residue detection, AI launch planner (GGUF metadata, KV/compute estimates, gate-based Vulkan admission), Jaguar ISA audit, pacman guard.
- systemd targets/slices/services for PC, GAMING and AI; an early-boot mode generator; sleep masking; Xorg 1080p60 CEA pin.
- `orbis-gpu-probe` (C): measured GL renderer + draw test; Vulkan device/heaps/queues; 15-test compute correctness suite with bounded waits.
- `orbis-infer-harness`: CPU-vs-Vulkan llama.cpp campaigns (KLD, tensors, greedy server outputs), boundary bisection, crash-resumable journal, live kmsg/UDP mirror, gate receipts.
- Kernel target `aeolia-belize-6.15` (feeRnt crashnt-4.7, firmware-free) with patch 0001 (1080p60 only) and a reproducible build script.
- Graphics recipes: Mesa 26.2.5 + reviewed linux-psx patch series (64/32-bit); xf86-video-amdgpu 25.0.0 + cursor fix.
- Packaging: Arch PKGBUILDs, build container, rootfs builder pinned to an Arch Linux Archive snapshot, reproducible initramfs builder with operator-supplied firmware, USB image builder and guarded writer, overlay install/update/uninstall scripts, SBOM and deterministic source tarball.
- Documentation: distro decision, hardware matrix, graphics report and patch audit, bug ledger, mode resource budget, architecture, build and deploy, release status, credits.

### Known limitations
- Nothing has been booted on a PS4 by Orbis yet. See docs/RELEASE-STATUS.md.
