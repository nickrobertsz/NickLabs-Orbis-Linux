# Install from Nobara Linux — exact commands

For the NickLabs primary console: **CUH-1215A** (original "fat" CUH-12xx, Belize southbridge, Liverpool GPU, Marvell 88W8897 Wi-Fi/BT), system firmware **13.50**, **GoldHEN** running, LAN only.

Nobara is the **build and flashing machine**. Orbis itself is Arch-based (see DISTRO-DECISION); nothing here installs Orbis onto the Nobara laptop.

There are two stages. Do them in order:

| Stage | What it proves | Needs |
|---|---|---|
| **0: hardware report** (section 3) | Kernel boots, HDMI comes up, amdgpu loads its firmware, Wi-Fi firmware loads, USB storage works | a USB stick (≥ 1 GB); about 10 minutes |
| **1: full system** (sections 5–6) | PC · GAMING · AI personalities, dashboard, mode switching | a USB 3 SSD (≥ 32 GB); a full build (a few hours, ~60 GB disk) |

Nothing below writes to any disk unless the command names that disk **twice** (`--device` and `--yes-erase`).

---

## 1. Prepare Nobara (once)

```sh
sudo dnf install -y git python3 podman dosfstools e2fsprogs xz gnupg2 openssl
python3 --version          # must be 3.11 or newer
```

## 2. Get the Orbis source and the kernel build

Put these files from the release in one folder, e.g. `~/orbis-release/`:

- `NICKLABS-PS4-ORBIS-LINUX-SOURCE.tar.gz`, or `orbis-0.2.0.bundle` (the git history);
- `orbis-kernel-aeolia-belize-6.15-build.tar.gz` (the compiled Orbis kernel, so you don't have to recompile it);
- optionally `orbis-0.2.0-stage0-cuh-1215a.img.xz` (a ready-made stage-0 image).

```sh
cd ~/orbis-release
sha256sum -c SHA256SUMS --ignore-missing          # every file you have must say OK
tar -xzf NICKLABS-PS4-ORBIS-LINUX-SOURCE.tar.gz   # or: git clone orbis-0.2.0.bundle nicklabs-ps4-orbis-linux
cd NICKLABS-PS4-ORBIS-LINUX-0.2.0                 # or: cd nicklabs-ps4-orbis-linux
make test                                         # about 20 s; must end with OK
```

## 3. Stage 0: the hardware report

### 3a. Get the stage-0 image

Either use the ready-made image:

```sh
xz -dk ~/orbis-release/orbis-0.2.0-stage0-cuh-1215a.img.xz
IMG=~/orbis-release/orbis-0.2.0-stage0-cuh-1215a.img
```

or build it yourself (about 1 minute; it downloads the verified community firmware source):

```sh
make release-stage0 KERNEL_TAR=~/orbis-release/orbis-kernel-aeolia-belize-6.15-build.tar.gz
IMG=out/release/orbis-0.2.0-stage0-cuh-1215a.img
```

The image contains Liverpool and Marvell firmware. **Keep it private**; don't upload or share it.

### 3b. Write it to the USB stick

```sh
lsblk -o NAME,SIZE,MODEL,TRAN,RM,MOUNTPOINTS      # find the stick: TRAN=usb, RM=1, the right SIZE
```

Below, `/dev/sdX` stands for that disk (whole disk, not `sdX1`). **Everything on it will be erased.**

```sh
for p in /dev/sdX?*; do sudo umount "$p" 2>/dev/null; done      # Nobara auto-mounts sticks
python3 packaging/image/make_usb_image.py write --image "$IMG" --device /dev/sdX
#   ^ dry run: prints the plan and any problem (mounted, not removable, ...). Nothing is written.
sudo python3 packaging/image/make_usb_image.py write --image "$IMG" --device /dev/sdX --yes-erase /dev/sdX
sync
```

### 3c. Boot it on the PS4

1. On the PS4: **Settings → Sound and Screen → Video Output Settings**. Set Resolution to 1080p and Deep Colour Output (and HDR) to Off.
2. Keep the console **on the LAN only**. Start GoldHEN, and enable **BinLoader** in the GoldHEN settings. Note the console's IP address (**Settings → Network → View Connection Status**).
3. Plug the stick **directly** into a front USB port (no hub).
4. From Nobara, send the Linux payload:
   ```sh
   make references                     # downloads and verifies linux-1024mb.elf (once)
   python3 packaging/scripts/send_payload.py <PS4-IP> out/references/linux-1024mb.elf
   ```
   This uses GoldHEN's BinLoader on port 9090. If your GoldHEN version refuses the `.elf`, build the payload from source with `make loader-payload` and send `out/loader/linux-1024mb.bin` instead.
5. The screen goes black, the kernel starts, and the initramfs prints "Orbis stage 0: collecting a hardware report". It then shows a rescue-shell prompt; that is the expected end of stage 0. Wait 10 seconds, then hold the console's power button until it turns off.

If the screen stays black for more than 2 minutes, that is a result too. Still check the stick (step 3d): when the kernel got far enough to see USB storage, the report is there.

### 3d. Read the report

Plug the stick back into Nobara. It mounts as **ORBIS-BOOT**:

```sh
ls /run/media/$USER/ORBIS-BOOT/orbis-logs/
#   stage0-001.txt          summary: kernel, cmdline, southbridge, PCI, DRM connectors + modes, firmware, Wi-Fi driver
#   stage0-001-dmesg.txt    full kernel log
```

Send both files back to Mr. Jaleco. A good stage 0 looks like this:

- `amdgpu` lines with no "failed to load firmware" errors;
- `card0-HDMI-A-1: status=connected` with a `1920x1080` mode;
- `mwifiex_sdio` loading `mrvl/sd8897_uapsta.bin`;
- the USB stick listed under block devices.

The report never copies the PS4 HDD key the loader injects, and records no MAC addresses.

## 4. Optional: prepare package signing and your login key

```sh
ls ~/.ssh/id_ed25519.pub || ssh-keygen -t ed25519              # your SSH key for logging into the console
packaging/scripts/orbis-repo-key.sh                            # local package-signing key (in out/keys; back it up)
```

## 5. Stage 1: build the full release

```sh
packaging/release.sh --profile cuh-1215a \
  --kernel-tar ~/orbis-release/orbis-kernel-aeolia-belize-6.15-build.tar.gz \
  --ssh-key ~/.ssh/id_ed25519.pub --sign
```

- The script asks for an **initial password** for the console user `orbis`; you must change it at first login. Pass `--no-password` to use only the SSH key.
- Podman steps run with `sudo` (the root filesystem needs real root ownership). Expect a few hours; Mesa is the long part.
- The image is LAN-only by default. `--internet-allowed` builds one without that policy.

Results land in `out/release/`:

| File | What it is |
|---|---|
| `orbis-0.2.0-cuh-1215a.img` and `.img.xz` | flashable disk image: ORBIS-BOOT (FAT32) + ORBIS-ROOT (ext4) |
| `orbis-0.2.0-stage0-cuh-1215a.img.xz` | stage-0 image (section 3) |
| `release-manifest.json`, `*.img.json` | kernel/initramfs hashes and profile; `redistributable: false` because of firmware |
| `firmware-provenance.json` | whether the Marvell blobs are byte-identical to Arch's linux-firmware |
| `sbom.spdx.json`, `NICKLABS-PS4-ORBIS-LINUX-SOURCE.tar.gz`, `SHA256SUMS` | SBOM, source, checksums |

Flash it to the SSD exactly as in 3b, with `IMG=out/release/orbis-0.2.0-cuh-1215a.img`. A USB 3 SSD with UAS, connected directly, is strongly preferred over a stick.

## 6. First boot of the full system

Send the payload as in 3c step 4. Orbis boots into **PC** (XFCE). Then, from Nobara:

```sh
ssh orbis@<PS4-IP>                 # first password login forces a new password
```

On the console:

```sh
orbis status                          # current personality, boot partition, next VRAM
sudo orbis doctor                     # all checks; includes network.lan_only and gpu tiers
sudo orbis doctor --deep              # + Vulkan compute correctness suite + binary ISA audit
sudo cat /etc/orbis/web-token         # dashboard: http://<PS4-IP>:8420/  (LAN only)
curl -m5 https://example.com || echo "blocked, as intended (LAN-only)"
orbis mode ai                         # headless llama.cpp (CPU); may stage vram.txt for the next boot
orbis mode gaming                     # Kodi on LAN-only images
orbis mode pc
sudo orbis-qualify --model CUH-1215A  # receipts in /var/lib/orbis/receipts
```

Copy the receipts back for the hardware matrix:

```sh
scp 'orbis@<PS4-IP>:/var/lib/orbis/receipts/*.json' ~/orbis-release/receipts/   # run on Nobara
```

**Time on a LAN-only console.** Public NTP is blocked. To serve time from Nobara:

```sh
# on Nobara
echo "allow 192.168.0.0/16" | sudo tee -a /etc/chrony.conf && sudo systemctl restart chronyd
sudo firewall-cmd --add-service=ntp
# on the console
sudo mkdir -p /etc/systemd/timesyncd.conf.d
printf '[Time]\nNTP=<NOBARA-IP>\n' | sudo tee /etc/systemd/timesyncd.conf.d/95-lan.conf   # 95 sorts after Orbis' 90
sudo systemctl restart systemd-timesyncd && timedatectl timesync-status
```

(The `firewall-cmd` change lasts until Nobara reboots; add `--permanent` if you want it kept.)

## 7. Updates without the internet

On Nobara, after changing Orbis or pulling a new version:

```sh
sudo podman run --rm --privileged -v "$PWD":/src:z -w /src orbis-build:0.2.0 \
  env ORBIS_KERNEL_OUT=/src/out/kernel/aeolia-belize-6.15 packaging/build-packages.sh
make serve-repo                       # serves out/repo on http://<NOBARA-IP>:8780/ ; Ctrl-C when done
```

On the console:

```sh
sudo orbis-update --orbis-only --from http://<NOBARA-IP>:8780/            # dry run
sudo orbis-update --orbis-only --from http://<NOBARA-IP>:8780/ --apply
```

Arch packages stay at the image's snapshot while the console is LAN-only. They move with the next image.

## 8. Recovery and rollback

| Problem | Fix (from Nobara, with the stick/SSD plugged in) |
|---|---|
| No picture / want headless | edit `ORBIS-BOOT/bootargs.txt` (one line) and change `orbis.mode=pc` to `orbis.mode=ai` |
| Want a fresh hardware report | add ` orbis.stage0=1` to the end of that line |
| New kernel misbehaves | rename `bzImage.orbis-prev` to `bzImage` and `initramfs.cpio.gz.orbis-prev` to `initramfs.cpio.gz`. On a running system: `sudo orbis boot rollback-kernel` |
| An Orbis package update broke something | `ls /var/lib/orbis/checkpoints` (package lists before each update); the previous repo copy is `/var/lib/orbis/repo.prev`; `sudo pacman -U /var/lib/orbis/repo.prev/<pkg>.pkg.tar.zst` |
| Old community Linux on the internal drive boots instead | can't happen with Orbis: an explicit `root=` always wins. The internal drive is only opened with `orbis.ps4hdd=1` |
| Everything | re-flash the image (section 5 output). Your data on the SSD is lost; back up `/home` and `/var/lib/orbis/models` first |

The loader reads each of `bzImage`, `initramfs.cpio.gz`, `bootargs.txt` and `vram.txt` separately: first USB, then the console's internal `/data/linux/boot`. Keep all four on the Orbis disk, so nothing is mixed with leftover files on the console.

## 9. The secondary console (CUH-2215B, Baikal)

Don't boot this image there. Baikal needs the `baikal-7.2` kernel target and its own profile, both still unqualified (HARDWARE-MATRIX). Qualify the CUH-1215A first, as agreed.
