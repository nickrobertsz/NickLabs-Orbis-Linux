# Checkpoint — NickLabs PS4 Orbis Linux 0.1.0 (2026-10-25)

Use this to resume the project in a new chat. Attach this tarball (or the git bundle) and say "continue Orbis from CHECKPOINT.md".

## State

- **Source handoff complete.** All deliverables from the master brief are in the tree; the documents live in `docs/`.
- **Tests:** `make test` → 63 unit (1 skipped) + 7 integration, all passing.
- **Git:** the commit `a18bd11` sits on top of `4da8365`, and anything later is in `git log`. The bundle `orbis-0.1.0.bundle` holds the full history: `git clone orbis-0.1.0.bundle nicklabs-ps4-orbis-linux`.
- **Kernel:** `aeolia-belize-6.15` built (no LTO) and passes the loader-contract check. bzImage sha256 `cbc11420…c157`. Built separately in `orbis-kernel-aeolia-belize-6.15-build.tar.gz`. **Compiled only, never booted.**
- **Proof level:** nothing has been booted on a PS4. See `docs/RELEASE-STATUS.md` for the per-component status.

## Not done or blocked in the build environment

- Mesa 26.2.5 has not been compiled; its patches apply cleanly.
- makepkg, rootfs and the USB image were never run end to end, because the environment had no Arch mirrors or Docker.
- `mcopy` (mtools) is unavailable, so the FAT file-copy step in `make_usb_image.py` is unexercised.
- The GPU probe and the inference harness have never run on RADV/Liverpool.

## Waiting on Nick

1. The CUH model and southbridge (Aeolia/Belize/Baikal). This picks the kernel target.
2. A working `initramfs.cpio.gz` to supply the firmware, plus the Wi-Fi/BT chip if known.
3. A machine with Docker and internet for `make packages rootfs initramfs image`, or CI. After that: flash, boot, and run `tests/hardware/qualify.sh`.
4. Host the `[orbis]` repo and a signing key (replace `packages.nicklabs.invalid`).
5. Confirm the licence of the cursor patch with DionKill/centi_07.

## Next engineering steps once hardware facts arrive

- Boot the built kernel, capture `dmesg` and the HDMI mode, and update HARDWARE-MATRIX and GRAPHICS-REPORT with receipts.
- Compile Mesa in the container; run `orbis-gpu-probe` on the console to get the first real Vulkan tier.
- Run an inference-harness campaign with a 0.5B–1.5B model and `receive` on a second machine, aimed at the Carlos Georges prefill fault.
