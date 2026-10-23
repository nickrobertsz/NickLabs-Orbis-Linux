#!/usr/bin/env bash
# Install Orbis on an EXISTING Arch-based PS4 installation (e.g. the community CachyOS
# Light or Arch images) instead of flashing a new disk. Dry run unless --apply.
#
#   sudo ./orbis-install-overlay.sh --repo URL_OR_DIR [--boot-dev /dev/sdX1] [--apply]
#
# Steps (each printed before it runs):
#   1. refuse x86-64-v3/v4 repos (Jaguar SIGILL) and non-PS4 hardware
#   2. add the [orbis] repo; install orbis-platform
#   3. replace DionKill's mesa-ps4/lib32-mesa-ps4/xf86-video-amdgpu-ps4 with the Orbis builds
#      (pinned Mesa 26.2.5 + reviewed patch series); libdrm-ps4 -> stock libdrm
#   4. mount the PS4 boot FAT partition at /boot/ps4 (label ORBIS-BOOT or --boot-dev)
#   5. build an Orbis initramfs from the firmware inside the initramfs you boot today
#   6. install linux-orbis-ps4; the pacman hook checks it and stages it, keeping the
#      current kernel as bzImage.orbis-prev
# Nothing outside the Linux root and the PS4 FAT partition is touched.
set -euo pipefail
apply=0; repo=""; bootdev=""
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) apply=1; shift ;;
    --repo) repo="$2"; shift 2 ;;
    --boot-dev) bootdev="$2"; shift 2 ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done
[ -n "$repo" ] || { echo "need --repo (Orbis package repository URL or file:// dir)" >&2; exit 2; }
run() { echo "+ $*"; [ "$apply" = 1 ] && "$@" || true; }
echo "== Orbis overlay install ($([ $apply = 1 ] && echo APPLY || echo DRY RUN))"
grep -qE '^\[(.*-v[34]|.*znver[0-9].*)\]' /etc/pacman.conf && { echo "x86-64-v3/v4 repositories configured: remove them first (Jaguar cannot run them)" >&2; exit 3; }
grep -qiE 'x86_64_v[34]' /etc/pacman.conf && { echo "Architecture includes x86_64_v3/v4: fix /etc/pacman.conf first" >&2; exit 3; }
if ! grep -qE '^(0x)?104d$' /sys/bus/pci/devices/*/vendor 2>/dev/null; then
  echo "WARNING: no Sony southbridge found; this does not look like a PS4" >&2
fi
if ! grep -q '^\[orbis\]' /etc/pacman.conf; then
  echo "+ add [orbis] to /etc/pacman.conf (Server = $repo)"
  [ "$apply" = 1 ] && printf '\n[orbis]\nSigLevel = Optional TrustAll\nServer = %s\n' "$repo" >> /etc/pacman.conf
fi
run pacman -Sy --noconfirm orbis-platform
# Replace community graphics packages (pacman handles provides/conflicts/replaces).
run pacman -S --noconfirm --needed mesa-orbis-ps4 lib32-mesa-orbis-ps4 xf86-video-amdgpu-orbis-ps4
if pacman -Q libdrm-ps4 >/dev/null 2>&1; then run pacman -S --noconfirm libdrm lib32-libdrm; fi
mkdir -p /boot/ps4
if ! mountpoint -q /boot/ps4; then
  dev="${bootdev:-/dev/disk/by-label/ORBIS-BOOT}"
  if [ ! -e "$dev" ]; then
    echo "!! PS4 boot partition not found ($dev). Pass --boot-dev /dev/sdXN (the FAT partition with bzImage)." >&2
    exit 4
  fi
  run mount -t vfat -o rw,noatime,flush "$dev" /boot/ps4
  line="$dev /boot/ps4 vfat rw,noatime,flush,nofail,x-systemd.device-timeout=10 0 0"
  echo "+ add to /etc/fstab: $line"
  if [ "$apply" = 1 ] && ! grep -q ' /boot/ps4 ' /etc/fstab; then echo "$line" >> /etc/fstab; fi
fi
[ -f /boot/ps4/initramfs.cpio.gz ] || { echo "!! no initramfs.cpio.gz on the boot partition to take firmware from" >&2; exit 4; }
src=/usr/share/orbis/initramfs-ps4-src
run mkdir -p /usr/lib/orbis/initramfs
echo "+ build Orbis initramfs (firmware taken from your current /boot/ps4/initramfs.cpio.gz, verified by hash)"
[ "$apply" = 1 ] && python3 /usr/lib/orbis/build_initramfs.py --src "$src" --firmware-from /boot/ps4/initramfs.cpio.gz \
    --manifest /usr/share/orbis/firmware.json -o /usr/lib/orbis/initramfs/initramfs.cpio.gz
run pacman -S --noconfirm linux-orbis-ps4
echo "== done. Next: 'orbis doctor', then relaunch the payload. Rollback: 'orbis boot rollback-kernel'."
