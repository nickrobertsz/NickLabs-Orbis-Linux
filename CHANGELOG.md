# Changelog

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
