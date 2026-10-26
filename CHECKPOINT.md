# Checkpoint — NickLabs PS4 Orbis Linux 0.2.0 (2026-10-26)

To resume in a new chat, attach the source tarball or `orbis-0.2.0.bundle` and say "continue Orbis from CHECKPOINT.md".

## State

- **Release pipeline complete in source.**
  - `packaging/release.sh` builds everything on Nobara with Podman: references → initramfs → stage-0 image → packages → rootfs → full image → checksums.
  - `docs/INSTALL-NOBARA.md` has the exact commands; `docs/HARDWARE-QUALIFICATION.md` lists what still needs the console.
- **Target console** (stated by Nick 2026-10-26, not yet confirmed from logs): CUH-1215A, fat CUH-12xx, Belize, Liverpool, Marvell 88W8897 (Torus 2), FW 13.50, GoldHEN; LAN only. Secondary CUH-2215B (Baikal) is deferred.
- **Built and checked here:**
  - CUH-1215A initramfs: sha256 `f2c15884…0960`; private, because it contains firmware.
  - Firmware-free initramfs: `a614e9c7…3119`.
  - Stage-0 image: deterministic, `fsck.fat` and `e2fsck` clean.
  - Kernel: compiled 0.1.0 build reused; bzImage `cbc11420…c157`.
- **Tests:** `make test` → 90 unit (1 skipped) + 7 integration, all passing. FAT/image tests need dosfstools on PATH, otherwise they skip.
- **Git:** see `git log`; the bundle holds the full history.

## Key decisions in 0.2.0

- **Firmware source.** Firmware comes from the X-F1REBALL-X v1.0 community initramfs, sha256 `3f373bd6…169b`, fetched and verified by `make references`. Its `init` is never used: it hard-wires `LABEL=psxitarch`. The Orbis initramfs is built from GayCorp/initramfs-ps4@c21a720 plus Orbis patches 0001/0002 (explicit `root=` wins; internal drive opt-in).
- **Profiles.** `cuh-1215a` requires liverpool-gpu (10 files) and marvell-88w8897; optional are 88w8797 and edid. EDID is not needed by the 6.15 kernel.
- **Loader findings** (ps4-linux-loader@d0f4d4c):
  - it prepends a cpio with the HDD key, the TV EDID and 8 GPU microcode files from the console;
  - Orbis' same-path files override them;
  - UVD/VCE come only from the initramfs;
  - each boot file is looked up separately (usb0, usb1, /data/linux/boot, /user/system/boot).
- **Network and packages.** LAN-only nftables policy by default; Kodi is the GAMING frontend on LAN-only images; the `[orbis]` repo lives in the image (`file:///var/lib/orbis/repo`), with updates via `orbis-update --orbis-only --from http://NOBARA:8780/`.
- **Version** bumped to 0.2.0.

## Blocked or not done in this build environment

- **No external mirrors here.** Arch mirrors, Docker Hub and freedesktop.org are unreachable, so packages, rootfs, the full image and the Mesa compile were never run. They run on Nobara.
- **Loader payload not built from source.** Building ps4-linux-loader was blocked by the session's permission policy. The prebuilt payload is hash-verified; the recipe is `make loader-payload`.
- **Marvell provenance open.** The Marvell blobs are not yet proven identical to linux-firmware (kernel.org unreachable). `release.sh` checks them against Arch `linux-firmware-marvell` and writes `firmware-provenance.json`.

## Waiting on Nick (in order)

1. Boot the stage-0 image and send `orbis-logs/stage0-001.txt` + `-dmesg.txt` (HARDWARE-QUALIFICATION Q0).
2. Run `packaging/release.sh --profile cuh-1215a --kernel-tar … --ssh-key … --sign` on Nobara; send the output or the failing step.
3. Flash the full image, boot it, run `sudo orbis-qualify --model CUH-1215A`, and send the receipt.

## Next engineering steps

- Fix whatever the stage-0 report or the first Nobara build turns up.
- After Q1/Q2: move CUH-1215A rows in HARDWARE-MATRIX from UNVERIFIED with receipts, and run the GPU inference campaign (Q3).
- Then add the CUH-2215B: the `baikal-7.2` kernel target, a `cuh-2215b` profile, and its Wi-Fi firmware group.
