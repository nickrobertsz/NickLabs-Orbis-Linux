# Build and Deploy

Every command below works from a clean checkout of this repository. Nothing assumes pre-existing private files. The two inputs Orbis **cannot** ship are named explicitly:

1. **Liverpool GPU firmware** (and Wi-Fi/BT firmware for your chip). The redistribution licence is unknown, so you supply it from the `initramfs.cpio.gz` you already boot. Orbis checks it against `manifests/firmware.json`.
2. **A jailbroken PS4** (GoldHEN) and the Linux payload (`ps4-linux/ps4-linux-loader`, e.g. via WebKitty). Orbis never ships or modifies these.

## 0. Requirements

| Step | Host | Tools |
|---|---|---|
| tests, probe, kernel, initramfs, image | any x86-64 Linux | `python3 ≥ 3.11`, `gcc`/`clang`, `make`, `git`; kernel: `clang lld llvm bc flex bison libelf pahole perl`; image: `dosfstools ≥ 4.2`, `mtools`, `e2fsprogs ≥ 1.43` |
| packages, rootfs | Docker or Podman | `packaging/Containerfile` (Arch Linux, generic x86_64) |

## 1. Verify the tree

```sh
make test            # 63 unit + 7 integration tests: engine, boot files, generator, harness, web/API
make probe VK_INCLUDE=/usr/include      # tools/gpu-probe/orbis-gpu-probe
tools/gpu-probe/orbis-gpu-probe --json --all     # on any machine; classifies software renderers honestly
```

## 2. Kernel

```sh
make kernel TARGET=aeolia-belize-6.15
# → out/kernel/aeolia-belize-6.15/{bzImage,config,modules-<rel>.tar,build-manifest.json,bzimage-check.json,SHA256SUMS}
```

`kernel/build-kernel.sh`:
1. clones the pinned tag and **verifies the commit** (`108ef97…`);
2. applies `kernel/patches/aeolia-belize-6.15/series`;
3. merges `kernel/config/*.fragment` (firmware-free; IKCONFIG on);
4. asserts `CONFIG_X86_PS4=y` and `CONFIG_EXTRA_FIRMWARE=""` survived `olddefconfig`;
5. builds with `LLVM=1 KCFLAGS=-march=btver2`;
6. runs the PS4 loader check on the bzImage (protocol, 64-bit entry, embedded config).

Baikal consoles use Linux psX: `kernel/targets/baikal-7.2.json` gives the exact commands. Check the result with `python3 -m orbis.bzimage out/.../bzImage` (run from `platform/`).

## 3. Packages and root filesystem

```sh
make packages                         # Arch container → out/repo/*.pkg.tar.zst + orbis.db + SHA256SUMS
make rootfs SNAPSHOT=2026/10/20       # pacstrap from ONE Arch Linux Archive date + out/repo
# → out/rootfs/{orbis-rootfs.tar.zst, distro.tar.xz, packages.txt, SHA256SUMS}
```

`packaging/rootfs/lists/*.txt` is the auditable package list.
- `base.txt` is shared by every mode.
- `pc.txt`, `gaming.txt` and `ai.txt` are personality packages: installed, but inert outside their target.

`distro.tar.xz` is laid out for the community installer (`install-linux-ext.sh`), so it can also be installed the traditional way.

## 4. Initramfs (with *your* firmware)

```sh
make initramfs FIRMWARE_FROM=/path/to/your/working/initramfs.cpio.gz
# → out/initramfs.cpio.gz (deterministic; refuses on missing/mismatching firmware unless told otherwise)
```

If your firmware hashes differ from the recorded set (another console or firmware revision), the build stops and lists them. Re-run with `--allow-hash-mismatch` (via `packaging/initramfs/build_initramfs.py`) only if that initramfs actually gives you a picture today.

## 5. USB disk

```sh
mkdir -p out/rootfs/dir && sudo tar --numeric-owner -xpf out/rootfs/orbis-rootfs.tar.zst -C out/rootfs/dir
sudo make image                                     # → out/orbis-<ver>.img (MBR: FAT32 ORBIS-BOOT 1 GiB + ext4 ORBIS-ROOT)
python3 packaging/image/make_usb_image.py write --image out/orbis-0.1.0.img --device /dev/sdX          # dry run
sudo python3 packaging/image/make_usb_image.py write --image out/orbis-0.1.0.img --device /dev/sdX --yes-erase /dev/sdX
```

The writer refuses:
- partitions;
- mounted devices;
- the running root;
- non-USB/non-removable disks (unless `--allow-fixed-disk`).

It writes nothing unless the device path is given twice. Use a **UAS-capable** USB-SATA/NVMe adapter, plugged **directly** into the console (no hub).

## 6. First boot

1. In PS4 settings, set 1080p output and turn off wide colour gamut ("Deep colour" / HDR). Have keyboard or controller plus Ethernet ready.
2. Plug in the USB disk and launch the Linux payload. `vram.txt` (1024 MB) on the disk overrides the payload's VRAM size.
3. Orbis boots into **PC**. Log in on the console or over SSH:
   ```sh
   sudo passwd orbis                    # account is locked until you set a password or add an SSH key
   sudo cat /etc/orbis/web-token        # dashboard: http://<ps4-ip>:8420/
   sudo nano /etc/orbis/orbis.toml      # set hardware.model = "CUH-xxxx"
   orbis doctor --deep                  # full report incl. Vulkan compute correctness
   ```
4. Switch personalities from the dashboard, the desktop shortcuts, or `orbis mode pc|gaming|ai`. When a reboot is required, Orbis explains why and stages `vram.txt`/`bootargs.txt`. The console returns to the PS4 menu; relaunch the payload.

**Recovery without a screen:** on any PC, edit `bootargs.txt` on the FAT partition and change `orbis.mode=` (e.g. to `ai`, which is headless). To go back to the previous kernel, rename `bzImage.orbis-prev` → `bzImage` and `initramfs.cpio.gz.orbis-prev` → `initramfs.cpio.gz`; this also works over GoldHEN FTP. On a running system, `orbis boot rollback-kernel` does the same.

## 7. Existing community install instead of a new disk

```sh
sudo packaging/scripts/orbis-install-overlay.sh --repo file:///path/to/out/repo          # dry run
sudo packaging/scripts/orbis-install-overlay.sh --repo file:///path/to/out/repo --apply
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
#   on another machine, to keep kernel messages if the console hangs:
python3 orbis_infer_harness.py receive --port 47999
orbis-infer-harness gate --out /var/lib/orbis/campaigns/<date> --write-receipt /var/lib/orbis/gpu-gate.json
orbis ai set backend vulkan     # admitted only if the receipt matches kernel+Mesa+llama.cpp+model
```

An example campaign is `tests/evidence/host/` (fixtures) with the schema in `tools/inference-harness/orbis_infer_harness.py`. Point `llama_cpu_bin_dir` at `/usr/bin` and `llama_vk_bin_dir` at `/usr/lib/orbis/llama/vulkan`.

## 9. Updates

```sh
sudo orbis-update                         # preview
sudo orbis-update --apply                 # checkpoint + pacman -Syu (guarded) + doctor
sudo orbis-update --to-snapshot 2026/11/15 --apply   # only dates listed as qualified
```

Generic Mesa, generic kernels and the unpinned community `*-ps4` packages are refused by pacman hooks. For a deliberate exception:

```sh
sudo orbis pkg allow-graphics-change --reason "testing Mesa 26.3 rc1"
```

The override is one-shot and logged.

## 10. Remove Orbis from an existing install

```sh
sudo packaging/scripts/orbis-uninstall-overlay.sh            # dry run
sudo packaging/scripts/orbis-uninstall-overlay.sh --apply
```

## 11. Release artefacts

```sh
make sbom                  # out/sbom.spdx.json (firmware listed as NOT DISTRIBUTED)
make dist                  # out/NICKLABS-PS4-ORBIS-LINUX-SOURCE.tar.gz (deterministic)
```
