#!/usr/bin/env bash
# NickLabs Orbis — assemble the root filesystem reproducibly.
#
#   packaging/rootfs/build-rootfs.sh --snapshot 2026/10/20 --repo out/repo --out out/rootfs \
#       [--model CUH-1215A] [--internet-allowed] [--keys out/keys] [--plan]
#       [--authorized-key ~/.ssh/id_ed25519.pub] [--password-hash '$6$...']
#
# Login: the 'orbis' user (wheel) gets the SSH public key and/or the password hash given
# here (the password must be changed at first login). Without either, the account has no
# usable credentials and the build refuses (use --no-login to build such an image anyway).
#
# Runs as root inside the Arch Linux build container (packaging/Containerfile) because it
# uses pacstrap. Every Arch package comes from ONE Arch Linux Archive snapshot date, so the
# same command rebuilds the same package set. Orbis packages come from the local repository
# built by packaging/build-packages.sh, and a copy of that repository is placed inside the
# root filesystem (/var/lib/orbis/repo) so the console can reinstall/repair Orbis offline.
#
# Defaults follow the NickLabs requirement that consoles stay off the public internet:
# orbis-lan-only.service is enabled and the GAMING frontend is Kodi (Steam needs the
# internet). --internet-allowed builds the image without that policy.
#
# Outputs:
#   out/rootfs/dir/                      the root tree (input to make_usb_image.py --rootfs-dir)
#   out/rootfs/orbis-rootfs.tar.zst      the same tree as an archive
#   out/rootfs/distro.tar.xz             drop-in for the community initramfs installers
#   out/rootfs/packages.txt              exact name+version list (SBOM input)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
snapshot=""; repo=""; out=""; plan=0; model=""; lan_only=1; keys=""; authkey=""; pwhash=""; nologin=0
while [ $# -gt 0 ]; do
  case "$1" in
    --snapshot) snapshot="$2"; shift 2 ;;
    --repo) repo="$(realpath "$2")"; shift 2 ;;
    --out) out="$2"; shift 2 ;;
    --model) model="$2"; shift 2 ;;
    --internet-allowed) lan_only=0; shift ;;
    --keys) keys="$(realpath "$2")"; shift 2 ;;
    --authorized-key) authkey="$(realpath "$2")"; shift 2 ;;
    --password-hash) pwhash="$2"; shift 2 ;;
    --no-login) nologin=1; shift ;;
    --plan) plan=1; shift ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done
[ -n "$snapshot" ] && [ -n "$repo" ] && [ -n "$out" ] || { echo "need --snapshot --repo --out" >&2; exit 2; }
[ -n "$keys" ] || keys="$(dirname "$repo")/keys"
if [ -z "$authkey" ] && [ -z "$pwhash" ] && [ "$nologin" = 0 ] && [ "$plan" = 0 ]; then
  echo "no login credentials: give --authorized-key FILE and/or --password-hash HASH (or --no-login)" >&2; exit 2
fi
if [ -n "$pwhash" ]; then case "$pwhash" in '$6$'*|'$y$'*) ;; *) echo "--password-hash must be a sha512-crypt (\$6\$) or yescrypt (\$y\$) hash" >&2; exit 2 ;; esac; fi
if [ -n "$authkey" ]; then grep -qE '^(ssh-(ed25519|rsa)|ecdsa-sha2-nistp(256|384|521)) ' "$authkey" || { echo "$authkey is not an SSH public key" >&2; exit 2; }; fi
signed=0; fpr=""
if [ -f "$keys/orbis-repo-key.asc" ] && [ -f "$keys/orbis-repo-key.fingerprint" ] && [ -f "$repo/orbis.db.tar.zst.sig" ]; then
  signed=1; fpr="$(cat "$keys/orbis-repo-key.fingerprint")"
fi
siglevel="Optional TrustAll"; [ "$signed" = 1 ] && siglevel="Required"
pkgs=$(cat "$here"/lists/{base,pc,gaming,ai}.txt | sed 's/#.*//' | tr -s ' \n' '\n' | grep -v '^$' | sort -u)
conf="$(mktemp)"
cat > "$conf" <<CONF
[options]
Architecture = x86_64
SigLevel = Required DatabaseOptional
LocalFileSigLevel = Optional
ParallelDownloads = 4

[orbis]
SigLevel = $siglevel
Server = file://$repo

[core]
Server = https://archive.archlinux.org/repos/$snapshot/\$repo/os/\$arch
[extra]
Server = https://archive.archlinux.org/repos/$snapshot/\$repo/os/\$arch
[multilib]
Server = https://archive.archlinux.org/repos/$snapshot/\$repo/os/\$arch
CONF
echo "snapshot: $snapshot"; echo "orbis repo: $repo (signed: $signed${fpr:+, $fpr})"
echo "model: ${model:-undeclared}  lan-only: $lan_only"; echo "packages: $(echo "$pkgs" | wc -l)"
if [ "$plan" = 1 ]; then echo "$pkgs"; echo "--- pacman.conf"; cat "$conf"; exit 0; fi
[ "$(id -u)" = 0 ] || { echo "must run as root inside the build container" >&2; exit 2; }
if [ "$signed" = 1 ]; then
  pacman-key --add "$keys/orbis-repo-key.asc" && pacman-key --lsign-key "$fpr"
fi
out="$(realpath -m "$out")"
root="$out/dir"
if [ -e "$root" ]; then rm -rf --one-file-system "${root:?}"; fi
mkdir -p "$root"
trap 'umount -R "$root" 2>/dev/null || true' EXIT
pacstrap -C "$conf" -M -G "$root" $pkgs
# Same pins on the installed system: no surprise generic repos, no v3 repos.
install -m644 "$here/overlay/etc/pacman.conf.in" "$root/etc/pacman.conf"
sed -i "s#@SNAPSHOT@#$snapshot#g; s#@ORBIS_SIGLEVEL@#$siglevel#g" "$root/etc/pacman.conf"
cp -a "$here/overlay/." "$root/" && rm -f "$root/etc/pacman.conf.in"
# The local [orbis] repository travels inside the system.
install -d "$root/var/lib/orbis/repo"
cp -a "$repo"/*.pkg.tar.zst "$repo"/orbis.db* "$repo"/orbis.files* "$root/var/lib/orbis/repo/"
cp -a "$repo"/*.sig "$root/var/lib/orbis/repo/" 2>/dev/null || true
[ -f "$repo/SHA256SUMS" ] && cp "$repo/SHA256SUMS" "$root/var/lib/orbis/repo/"
if [ "$signed" = 1 ]; then
  install -Dm644 "$keys/orbis-repo-key.asc" "$root/usr/share/orbis/orbis-repo-key.asc"
  echo "Orbis packages are signed with the local NickLabs key $fpr (trusted on this system)." \
    > "$root/usr/share/orbis/repo-signing.txt"
else
  echo "This image was built from an UNSIGNED local Orbis repository (SigLevel Optional TrustAll for [orbis] only)." \
    > "$root/usr/share/orbis/repo-signing.txt"
fi
# Per-console settings in the operator config (the packaged defaults stay untouched).
if [ -n "$model" ]; then
  sed -i "s/^# model = .*/model = \"$model\"/" "$root/etc/orbis/orbis.toml"
fi
if [ "$lan_only" = 1 ]; then
  sed -i 's/^frontend = "steam-gamepadui"/frontend = "kodi"/' "$root/etc/orbis/orbis.toml"
else
  sed -i 's/^lan_only = true/lan_only = false/' "$root/etc/orbis/orbis.toml"
  install -d "$root/etc/orbis" && touch "$root/etc/orbis/allow-internet"
fi
arch-chroot "$root" /bin/sh -e <<CH
systemd-sysusers
systemd-tmpfiles --create
systemctl enable NetworkManager.service sshd.service orbisd.service orbis-web.socket lightdm.service
systemctl enable orbis-lan-only.service
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target
echo pc > /etc/orbis/default-mode
ln -sf /usr/share/zoneinfo/UTC /etc/localtime
locale-gen
useradd -m -G wheel,orbis-admin,video,render,input -s /bin/bash orbis
usermod -p '*' orbis          # no password login until one is set below or by the operator
echo '%wheel ALL=(ALL:ALL) ALL' > /etc/sudoers.d/10-orbis-wheel
echo 'orbis ALL=(root) NOPASSWD: /usr/bin/passwd orbis' > /etc/sudoers.d/11-orbis-set-password
chmod 440 /etc/sudoers.d/10-orbis-wheel /etc/sudoers.d/11-orbis-set-password
if [ "$signed" = 1 ]; then
  pacman-key --init
  pacman-key --populate archlinux
  pacman-key --add /usr/share/orbis/orbis-repo-key.asc
  pacman-key --lsign-key "$fpr"
fi
CH
if [ -n "$authkey" ]; then
  install -d -m700 -o 1000 -g 1000 "$root/home/orbis/.ssh"
  install -m600 -o 1000 -g 1000 "$authkey" "$root/home/orbis/.ssh/authorized_keys"
  arch-chroot "$root" chown -R orbis:orbis /home/orbis/.ssh
fi
if [ -n "$pwhash" ]; then
  arch-chroot "$root" usermod -p "$pwhash" orbis
  arch-chroot "$root" chage -d 0 orbis   # must change it at first login
fi
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
echo "rootfs written to $out (tree in $root)"
