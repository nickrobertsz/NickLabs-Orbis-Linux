# Build and Deploy

**Short path:** `docs/INSTALL-NOBARA.md` has the exact commands for the NickLabs CUH-1215A, from a Nobara laptop to a booted console. `packaging/release.sh` (`make release`) runs every step below in order. This document explains the steps one by one.

Every command works from a clean checkout. The inputs Orbis does **not** ship are named explicitly:

1. **Firmware.** Liverpool GPU microcode (redistribution rights unknown) and the Wi-Fi/BT blob for your chip. Orbis takes them from a community `initramfs.cpio.gz`: by default the verified X-F1REBALL-X v1.0 file (`make references`), or one you already boot. Each file is checked against `manifests/firmware.json` for the hardware profile you build.
2. **The jailbreak and payload.** GoldHEN on the console, and the `ps4-linux/ps4-linux-loader` payload. Orbis records the hash of a prebuilt payload (`manifests/references.json`) and has a recipe to build it from the pinned commit (`make loader-payload`). It never modifies either.

## 0. Requirements

| Step | Host | Tools |
|---|---|---|
| tests, initramfs, stage-0 image | any x86-64 Linux | `python3 ≥ 3.11`, `git`, `make`; image: `dosfstools ≥ 4.2`, `e2fsprogs ≥ 1.43` (mtools optional) |
| kernel (only if you don't reuse the released build) | any x86-64 Linux, 8 GB RAM | `clang lld llvm bc flex bison libelf pahole perl` |
| packages, rootfs, full image | Podman or Docker (run as root) | `packaging/Containerfile` (Arch Linux, generic x86_64) |

## 1. Verify the tree

```sh
make test            # 90 unit + 7 integration tests (FAT/image tests skip without dosfstools)
make probe VK_INCLUDE=/usr/include      # tools/gpu-probe/orbis-gpu-probe
```

## 2. Kernel

Reuse the released build. `release.sh --kernel-tar` and the kernel package (`ORBIS_KERNEL_OUT=`) verify its `SHA256SUMS` and the PS4 loader contract:

```sh
packaging/release.sh --stage0-only --kernel-tar orbis-kernel-aeolia-belize-6.15-build.tar.gz
```

Or compile it (about 30 minutes on 2 cores; 8 GB RAM is enough because Orbis sets `CONFIG_LTO_NONE=y`, after the upstream full-LTO link was OOM-killed on an 8 GB machine):

```sh
make kernel TARGET=aeolia-belize-6.15
# → out/kernel/aeolia-belize-6.15/{bzImage,config,modules-<rel>.tar,build-manifest.json,bzimage-check.json,SHA256SUMS}
```

`kernel/build-kernel.sh` does the following:

1. Clones the pinned tag and verifies the commit (`108ef97…`).
2. Applies `kernel/patches/aeolia-belize-6.15/series`.
3. Merges `kernel/config/*.fragment` (firmware-free, IKCONFIG on, no LTO).
4. Asserts that `CONFIG_X86_PS4=y`, `CONFIG_EXTRA_FIRMWARE=""` and `CONFIG_LTO_NONE=y` survived `olddefconfig`.
5. Builds with `LLVM=1 KCFLAGS=-march=btver2`.
6. Runs the PS4 loader check on the bzImage.

The kernel has amdgpu, mwifiex/btmrvl SDIO, MT76, vfat, USB storage/UAS and nftables built in.

## 3. Initramfs (firmware per hardware profile)

```sh
make references                              # community inputs → out/references (SHA-256 verified)
make initramfs PROFILE=cuh-1215a             # → out/initramfs/initramfs-cuh-1215a.cpio.gz + .manifest.json
make initramfs PROFILE=firmware-free         # redistributable variant, no firmware at all
```

`packaging/initramfs/build_initramfs.py` builds the initramfs as follows:

- **Source.** It starts from `GayCorp/initramfs-ps4` at the pinned commit `c21a720`. GayCorp has moved development to git.gay; the GitHub copy is frozen and keeps this commit. The commit is verified, from git or from `.orbis-commit` in packaged trees.
- **Patches.** It applies `packaging/initramfs/patches/` to a private copy:
  - an explicit `root=` always wins over the upstream discovery (`LABEL=psxitarch`, then the internal `linux.img`);
  - the init waits up to 30 s for that device and runs fsck;
  - with `root=`, the PS4 internal drive is opened only on request (`orbis.ps4hdd=1`).

  Without `root=`, the upstream behaviour is unchanged.
- **Self-check.** It runs `packaging/initramfs/tests/root-selection.sh` on the patched tree and stops if any decision is wrong.
- **Overlay.** It adds the Orbis files from `packaging/initramfs/overlay/` (the stage-0 report), and refuses any overlay file that would replace an upstream one.
- **Firmware rules,** per profile in `manifests/initramfs-profiles.json`:
  - Required groups: every file must be present and match its recorded hash. There is no override for a missing file.
  - Optional groups: included only when present **and** verified, otherwise skipped and recorded. A partial, mismatching or unhashed optional group is refused.
  - `--allow-hash-mismatch` accepts a different firmware revision and records it as `mismatch-accepted`.
- **Output.** The build is deterministic and writes a manifest: source commit, patch hashes, self-check result, firmware source hash, every firmware file with group, hash, status and licence, skipped groups, and `redistributable`.

| Profile | Required | Optional |
|---|---|---|
| `cuh-1215a` | `liverpool-gpu` (10 files), `marvell-88w8897` | `marvell-88w8797`, `edid` |
| `aeolia-belize-generic` | `liverpool-gpu` | `marvell-88w8897`, `marvell-88w8797`, `edid` |
| `firmware-free` | none | none |

**What the loader adds.** At kexec time, ps4-linux-loader puts its own cpio **in front of** the initramfs. That cpio holds the console's HDD key, the TV's EDID (`edid/my_edid.bin`) and 8 GPU microcode files extracted from the running console. Files with the same path in the Orbis initramfs replace those 8. UVD/VCE (video decode/encode) are never extracted. So `firmware-free` still gives a picture on current loaders, but no hardware video decode and no Wi-Fi/BT.

**EDID.** The generic `edid/1920x1080.bin` is optional. The Orbis 6.15 kernel fixes 1080p60 in `ps4_bridge` and does not pass `drm.edid_firmware=`.

## 4. Packages, local repository and root filesystem

```sh
make repo-key                          # optional: local signing key in out/keys (back it up; never commit it)
make container
sudo podman run --rm --privileged -v "$PWD":/src:z -w /src orbis-build:0.2.0 \
  env ORBIS_KERNEL_OUT=/src/out/kernel/aeolia-belize-6.15 packaging/build-packages.sh
#   → out/repo/*.pkg.tar.zst (+ .sig when signed), orbis.db, SHA256SUMS
sudo podman run --rm --privileged -v "$PWD":/src:z -w /src orbis-build:0.2.0 \
  packaging/rootfs/build-rootfs.sh --snapshot 2026/10/20 --repo out/repo --out out/rootfs \
  --model CUH-1215A --authorized-key out/keys/console-authorized-key.pub
#   → out/rootfs/dir/ (tree), orbis-rootfs.tar.zst, distro.tar.xz, packages.txt, SHA256SUMS
```

- **Arch packages** all come from one Arch Linux Archive date. **Orbis packages** come from `out/repo`, and a copy of that repository goes into the image at `/var/lib/orbis/repo` (`[orbis] Server = file:///var/lib/orbis/repo`). No public package host is needed.
- **Signing.** When signed, the public key is trusted in the image's pacman keyring and `[orbis]` uses `SigLevel = Required`.
- **LAN-only by default.** `orbis-lan-only.service` (nftables) is enabled and the GAMING frontend is Kodi, because Steam needs the internet to sign in. `--internet-allowed` builds without that policy.
- **Login.** User `orbis` gets your SSH public key (`--authorized-key`) and/or a password hash (`--password-hash`, which must be changed at first login). The build refuses to produce an image with no way to log in. `%wheel` gets sudo.
- `packaging/rootfs/lists/*.txt` is the auditable package list. `distro.tar.xz` also works with the community installers.

## 5. USB images

```sh
make stage0 PROFILE=cuh-1215a          # hardware-report image: kernel + initramfs, empty root (1 GB)
# full image, as root (inside the container, so the rootfs ownership is preserved):
sudo podman run --rm --privileged -v "$PWD":/src:z -w /src orbis-build:0.2.0 \
  python3 packaging/image/make_usb_image.py image --rootfs-dir out/rootfs/dir \
  --kernel out/kernel/aeolia-belize-6.15/bzImage --initramfs out/initramfs/initramfs-cuh-1215a.cpio.gz \
  --size 32G -o out/release/orbis-0.2.0-cuh-1215a.img
```

The image has an MBR with two partitions: FAT32 `ORBIS-BOOT` (1 GiB; 512 MiB for stage 0) and ext4 `ORBIS-ROOT`.

- **FAT partition contents:** `bzImage`, `initramfs.cpio.gz`, `bootargs.txt` (one line), `vram.txt`, `README-ORBIS.txt` and `orbis-boot.json` (what was built, with hashes and redistributability).
- **Writing the files:** the built-in FAT32 writer (`packaging/image/fat32.py`) needs no mtools or loop device and keeps long names. `--fat-writer mtools` uses mcopy instead. Either way, every file is read back byte for byte, and `fsck.fat` checks the partition when installed.
- **Determinism:** identical inputs give an identical image (fixed volume IDs, UUIDs and timestamps).
- **Stage 0:** `--stage0` adds `orbis.stage0=1`. The initramfs writes `orbis-logs/stage0-NNN.txt` and `-dmesg.txt` to the FAT partition, then stops in its rescue shell (`resume-boot` continues).

**Writing to a disk:**

```sh
python3 packaging/image/make_usb_image.py write --image IMG --device /dev/sdX                       # dry run
sudo python3 packaging/image/make_usb_image.py write --image IMG --device /dev/sdX --yes-erase /dev/sdX
```

The writer refuses partitions, mounted devices, the running root, and non-USB/non-removable disks (unless `--allow-fixed-disk`). It writes nothing unless the device path is given twice. Use a **UAS-capable** USB-SATA/NVMe adapter, plugged **directly** into the console.

## 6. Booting

1. In PS4 settings, set 1080p output and turn off Deep Colour and HDR.
2. Start GoldHEN with BinLoader enabled. Send the payload from the build machine: `python3 packaging/scripts/send_payload.py <PS4-IP> out/references/linux-1024mb.elf` (port 9090, private addresses only).
3. The loader reads each of `bzImage`, `initramfs.cpio.gz`, `bootargs.txt` and `vram.txt` **separately**, from `/mnt/usb0`, `/mnt/usb1`, `/data/linux/boot/` or `/user/system/boot/` (first hit wins). Keep all four on the Orbis disk, so nothing mixes with leftovers on the console.
4. First boot, log in as `orbis` (SSH key or the initial password), then:
   ```sh
   sudo orbis doctor                 # includes network.lan_only and the GPU tiers
   sudo cat /etc/orbis/web-token     # dashboard: http://<ps4-ip>:8420/
   sudo orbis-qualify --model CUH-1215A
   ```
5. Switch personalities from the dashboard, the desktop shortcuts, or `orbis mode pc|gaming|ai`. When a reboot is required, Orbis explains why and stages `vram.txt`/`bootargs.txt`. The console returns to the PS4 menu; relaunch the payload.

**Recovery without a screen:** on any PC, edit `bootargs.txt` on the FAT partition:

- `orbis.mode=ai` gives a headless boot;
- adding ` orbis.stage0=1` gives a fresh hardware report.

To roll back a kernel, rename `bzImage.orbis-prev` and `initramfs.cpio.gz.orbis-prev` back to `bzImage` and `initramfs.cpio.gz`; this also works over GoldHEN FTP (port 2121). On a running system, `orbis boot rollback-kernel` does the same.

## 7. Existing community install instead of a new disk

```sh
sudo packaging/scripts/orbis-install-overlay.sh --repo file:///path/to/out/repo --profile cuh-1215a          # dry run
sudo packaging/scripts/orbis-install-overlay.sh --repo file:///path/to/out/repo --profile cuh-1215a --apply
```

## 8. AI mode

```sh
sudo cp qwen2.5-1.5b-instruct-q4_k_m.gguf /var/lib/orbis/models/   # 0.5B–4B, Q4_K_M/Q5_K_M recommended
orbis ai set model qwen2.5-1.5b-instruct-q4_k_m.gguf
orbis ai plan                         # ctx/batch/threads computed from measured memory; shows the budget
orbis mode ai
curl http://127.0.0.1:8081/health
```

Vulkan offload stays off until a gate receipt exists for this exact stack. To produce one:

```sh
orbis-infer-harness run --config campaign.json --out /var/lib/orbis/campaigns/$(date +%F)
#   on another LAN machine, to keep kernel messages if the console hangs:
python3 orbis_infer_harness.py receive --port 47999
orbis-infer-harness gate --out /var/lib/orbis/campaigns/<date> --write-receipt /var/lib/orbis/gpu-gate.json
orbis ai set backend vulkan     # admitted only if the receipt matches kernel+Mesa+llama.cpp+model
```

## 9. Updates

On a LAN-only console (the default), only Orbis packages update. They come from the in-image repository or from Nobara:

```sh
make serve-repo                                                          # on the build machine (port 8780)
sudo orbis-update --orbis-only --from http://<build-machine>:8780/        # on the console, dry run
sudo orbis-update --orbis-only --from http://<build-machine>:8780/ --apply
sudo orbis-update --import-key orbis-repo-key.asc --apply                 # after rotating the signing key
```

With internet allowed, full updates work as before:

```sh
sudo orbis-update --apply
sudo orbis-update --to-snapshot 2026/11/15 --apply   # only dates listed as qualified
```

Generic Mesa, generic kernels and the unpinned community `*-ps4` packages are refused by pacman hooks. `sudo orbis pkg allow-graphics-change --reason "…"` is a one-shot, logged exception.

## 10. Remove Orbis from an existing install

```sh
sudo packaging/scripts/orbis-uninstall-overlay.sh            # dry run
sudo packaging/scripts/orbis-uninstall-overlay.sh --apply
```

## 11. Release artefacts

```sh
make release KERNEL_TAR=…            # everything; out/release/ with SHA256SUMS and release-manifest.json
make sbom                            # out/sbom.spdx.json (firmware listed as NOT DISTRIBUTED)
make dist                            # out/NICKLABS-PS4-ORBIS-LINUX-SOURCE.tar.gz (deterministic, no firmware)
```

Images built from a firmware-carrying profile are marked `redistributable: false` in `orbis-boot.json` and `release-manifest.json`. Keep them private. `release.sh` also writes `firmware-provenance.json`, which compares the Marvell blobs with Arch's `linux-firmware-marvell`. If they are identical, their linux-firmware licence (LICENCE.Marvell) applies, and that comparison is how their redistribution status can be settled.
