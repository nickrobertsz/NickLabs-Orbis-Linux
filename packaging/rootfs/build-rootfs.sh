#!/usr/bin/env bash
# NickLabs Orbis — assemble the root filesystem reproducibly.
#
#   packaging/rootfs/build-rootfs.sh --snapshot 2026/10/20 --repo out/repo --out out/rootfs [--plan]
#
# Runs inside an Arch Linux build container (see packaging/Containerfile) because it uses
# pacstrap. Every Arch package comes from ONE Arch Linux Archive snapshot date, so the
# same command rebuilds the same package set. Orbis packages come from the local
# repository built by packaging/build-packages.sh.
#
# Outputs:
#   out/rootfs/orbis-rootfs.tar.zst      for packaging/image/make-usb-image.py
#   out/rootfs/distro.tar.xz             drop-in for the community initramfs installer
#                                        (install-linux-ext.sh expects this name)
#   out/rootfs/packages.txt              exact name+version list (SBOM input)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
snapshot=""; repo=""; out=""; plan=0
while [ $# -gt 0 ]; do
  case "$1" in
    --snapshot) snapshot="$2"; shift 2 ;;
    --repo) repo="$(realpath "$2")"; shift 2 ;;
    --out) out="$2"; shift 2 ;;
    --plan) plan=1; shift ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done
[ -n "$snapshot" ] && [ -n "$repo" ] && [ -n "$out" ] || { echo "need --snapshot --repo --out" >&2; exit 2; }
pkgs=$(cat "$here"/lists/{base,pc,gaming,ai}.txt | sed 's/#.*//' | tr -s ' \n' '\n' | grep -v '^$' | sort -u)
conf="$(mktemp)"
cat > "$conf" <<CONF
[options]
Architecture = x86_64
SigLevel = Required DatabaseOptional
LocalFileSigLevel = Optional
ParallelDownloads = 4

[orbis]
SigLevel = Optional TrustAll
Server = file://$repo

[core]
Server = https://archive.archlinux.org/repos/$snapshot/\$repo/os/\$arch
[extra]
Server = https://archive.archlinux.org/repos/$snapshot/\$repo/os/\$arch
[multilib]
Server = https://archive.archlinux.org/repos/$snapshot/\$repo/os/\$arch
CONF
echo "snapshot: $snapshot"; echo "orbis repo: $repo"; echo "packages: $(echo "$pkgs" | wc -l)"
if [ "$plan" = 1 ]; then echo "$pkgs"; echo "--- pacman.conf"; cat "$conf"; exit 0; fi
[ "$(id -u)" = 0 ] || { echo "must run as root inside the build container" >&2; exit 2; }
root="$(mktemp -d)"
trap 'umount -R "$root" 2>/dev/null || true' EXIT
pacstrap -C "$conf" -M -G "$root" $pkgs
# Same pins on the installed system: no surprise generic repos, no v3 repos.
install -m644 "$here/overlay/etc/pacman.conf.in" "$root/etc/pacman.conf"
sed -i "s#@SNAPSHOT@#$snapshot#g" "$root/etc/pacman.conf"
cp -a "$here/overlay/." "$root/" && rm -f "$root/etc/pacman.conf.in"
arch-chroot "$root" /bin/sh -e <<'CH'
systemd-sysusers
systemd-tmpfiles --create
systemctl enable NetworkManager.service sshd.service orbisd.service orbis-web.socket lightdm.service
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target
echo pc > /etc/orbis/default-mode
ln -sf /usr/share/zoneinfo/UTC /etc/localtime
locale-gen
useradd -m -G wheel,orbis-admin,video,render,input -s /bin/bash orbis
passwd -l orbis   # operator sets a password / SSH key on first boot (docs/BUILD-AND-DEPLOY.md)
CH
arch-chroot "$root" pacman -Q > "$root/usr/share/orbis/packages.txt"
mkdir -p "$out"
cp "$root/usr/share/orbis/packages.txt" "$out/packages.txt"
tar --sort=name --numeric-owner --xattrs --acls --mtime="@0" -C "$root" \
    --exclude=./proc/* --exclude=./sys/* --exclude=./dev/* --exclude=./run/* --exclude=./tmp/* \
    --exclude=./var/cache/pacman/pkg/* -cf - . | zstd -T0 -19 -o "$out/orbis-rootfs.tar.zst"
tar --sort=name --numeric-owner --xattrs --acls --mtime="@0" -C "$root" \
    --exclude=./proc/* --exclude=./sys/* --exclude=./dev/* --exclude=./run/* --exclude=./tmp/* \
    --exclude=./var/cache/pacman/pkg/* -cf - . | xz -T0 -6 > "$out/distro.tar.xz"
( cd "$out" && sha256sum orbis-rootfs.tar.zst distro.tar.xz packages.txt > SHA256SUMS )
echo "rootfs written to $out"
