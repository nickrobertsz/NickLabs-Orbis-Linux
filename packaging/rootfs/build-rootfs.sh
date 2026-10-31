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
kernel_out=""; ps4_initramfs=""; expected_profile=""
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
    --kernel-out) kernel_out="$(realpath "$2")"; shift 2 ;;
    --ps4-initramfs) ps4_initramfs="$(realpath "$2")"; shift 2 ;;
    --expected-profile) expected_profile="$2"; shift 2 ;;
    --no-login) nologin=1; shift ;;
    --plan) plan=1; shift ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done
[ -n "$snapshot" ] && [ -n "$repo" ] && [ -n "$out" ] || { echo "need --snapshot --repo --out" >&2; exit 2; }
[ -n "$keys" ] || keys="$(dirname "$repo")/keys"
if [ "$plan" = 0 ]; then
  [ -f "$kernel_out/bzImage" ] && [ -f "$kernel_out/build-manifest.json" ] &&
    [ -f "$ps4_initramfs" ] && [ -f "$ps4_initramfs.manifest.json" ] || {
    echo 'rootfs needs --kernel-out BUILD-DIRECTORY and --ps4-initramfs FILE with its manifest' >&2; exit 2;
  }
fi
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
  echo '==> Initializing build-container pacman keyring...'
  pacman-key --init
  pacman_gpgdir="$(pacman-conf GPGDir)"
  [ -n "$pacman_gpgdir" ] && [ -d "$pacman_gpgdir" ] || {
    echo 'pacman-key --init did not create a usable build-container keyring' >&2; exit 3;
  }
  if ! gpg --batch --homedir "$pacman_gpgdir" --with-colons --list-secret-keys 2>/dev/null | grep -q '^sec:'; then
    echo 'pacman-key --init did not create a secret master key; cannot locally sign Orbis trust' >&2
    exit 3
  fi
  pacman-key --populate archlinux
  pacman-key --add "$keys/orbis-repo-key.asc"
  pacman-key --lsign-key "$fpr"
  gpg --batch --homedir "$pacman_gpgdir" --verify \
    "$repo/orbis.db.tar.zst.sig" "$repo/orbis.db.tar.zst" || {
      echo 'Orbis repository database signature is not valid in the initialized pacman keyring' >&2
      exit 3
    }
  echo "==> Verified local Orbis repository signing key: $fpr"
fi
out="$(realpath -m "$out")"
root="$out/dir"
[ ! -L "$root" ] || { echo "unsafe rootfs destination symlink: $root" >&2; exit 4; }
mkdir -p "$out"
# A rootfs cannot be built on a mount that emulates Unix ownership (some NTFS,
# exFAT and FUSE configurations). Test *inside this container's UID mapping*.
ownership_probe="$(mktemp -d "$out/.orbis-owner-probe.XXXXXXXX")"
chown 0:0 "$ownership_probe" || { echo "filesystem does not permit root ownership: $out" >&2; exit 4; }
[ "$(stat -c '%u:%g' "$ownership_probe")" = 0:0 ] || {
  echo "root ownership is not representable in $out; use a POSIX filesystem/compatible Podman mapping" >&2; exit 4;
}
rmdir "$ownership_probe"
echo "==> Rootfs filesystem: $(stat -f -c %T "$out") ($(findmnt -T "$out" -no SOURCE,FSTYPE,OPTIONS 2>/dev/null || true))"
if [ -e "$root" ]; then rm -rf --one-file-system "${root:?}"; fi
mkdir -p "$root"
trap 'umount -R "$root" 2>/dev/null || true' EXIT
# The kernel-sync ALPM hook must wait until the final PS4 boot partition
# exists. The marker applies ONLY to this disposable image-assembly root.
install -m644 /dev/null "$root/.orbis-image-assembly"
rootfs_security_check() {
  local phase="$1" p got mode
  # Only platform trust anchors must be root-owned. Never recursively chown
  # package files, service-owned data or actual user home directories.
  for p in / /etc /usr /var /boot /run /tmp; do
    [ -d "$root$p" ] || continue
    [ ! -L "$root$p" ] || { echo "[$phase] forbidden directory symlink: $p" >&2; return 4; }
    got="$(stat -c '%u:%g' "$root$p")"
    [ "$got" = 0:0 ] || { echo "[$phase] $p owned $got (expected root:root)" >&2; return 4; }
    mode="$(stat -c %a "$root$p")"
    if [ "$p" = /tmp ]; then
      [ "$(stat -c %a "$root$p")" = 1777 ] || {
        echo "[$phase] /tmp must be mode 1777 (found $mode)" >&2; return 4;
      }
    elif [ -n "$(find "$root$p" -maxdepth 0 -perm /022 -print)" ]; then
      echo "[$phase] writable privileged directory $p ($mode)" >&2; return 4
    fi
  done
  echo "==> Rootfs directory security passed ($phase)"
}
pacstrap -C "$conf" -M -G "$root" $pkgs
rootfs_security_check after-pacstrap
# Same pins on the installed system: no surprise generic repos, no v3 repos.
install -m644 "$here/overlay/etc/pacman.conf.in" "$root/etc/pacman.conf"
sed -i "s#@SNAPSHOT@#$snapshot#g; s#@ORBIS_SIGLEVEL@#$siglevel#g" "$root/etc/pacman.conf"
# GNU cp -a normally applies the host overlay's UID and GID to existing
# rootfs directories, including `/` and `/etc`. Never inherit host ownership;
# preserve the overlay's legitimate mode, symlink and security attributes.
# Directory-security checks immediately below reject unsafe permission changes.
cp -a --no-preserve=ownership "$here/overlay/." "$root/"
rm -f "$root/etc/pacman.conf.in"
rootfs_security_check after-overlay
# The local [orbis] repository travels inside the system.
install -d "$root/var/lib/orbis/repo"
cp -a --no-preserve=ownership "$repo"/*.pkg.tar.zst "$repo"/orbis.db* "$repo"/orbis.files* "$root/var/lib/orbis/repo/"
cp -a --no-preserve=ownership "$repo"/*.sig "$root/var/lib/orbis/repo/" 2>/dev/null || true
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
# The boot partition of the final image uses the upstream PS4 initramfs, not
# mkinitcpio's host-discovery image. Install that exact artifact in the rootfs
# for later kernel updates and verify the kernel package against the build.
install -Dm644 "$ps4_initramfs" "$root/usr/lib/orbis/initramfs/initramfs.cpio.gz"
install -Dm644 "$ps4_initramfs.manifest.json" "$root/usr/lib/orbis/initramfs/initramfs.cpio.gz.manifest.json"
python3 - "$kernel_out" "$ps4_initramfs" "$root" "$expected_profile" <<'PY'
import hashlib, json, pathlib, sys
kout, initrd, root = map(pathlib.Path, sys.argv[1:4])
expected = sys.argv[4]
build = json.loads((kout / 'build-manifest.json').read_text())
ram = json.loads(pathlib.Path(str(initrd) + '.manifest.json').read_text())
rel = build.get('kernel_release')
if not rel or 'orbis-ps4' not in rel:
    raise SystemExit(f'PS4 kernel manifest has invalid kernel release: {rel!r}')
installed = root / 'usr/lib/modules' / rel / 'vmlinuz'
if not installed.is_file() or installed.read_bytes() != (kout / 'bzImage').read_bytes():
    raise SystemExit(f'installed linux-orbis-ps4 kernel does not match verified build {rel}')
if ram.get('output', {}).get('sha256') != hashlib.sha256(initrd.read_bytes()).hexdigest():
    raise SystemExit('PS4 initramfs does not match its signed-off build manifest hash')
if expected and ram.get('profile', {}).get('name') != expected:
    raise SystemExit('PS4 initramfs profile differs from release profile')
if not (root / 'usr/lib/modules' / rel / 'config').is_file():
    raise SystemExit('PS4 kernel package lacks its build configuration')
print('==> Verified installed PS4 kernel and external boot initramfs:', rel, ram['profile']['name'])
PY
rootfs_security_check before-chroot
# arch-chroot expects a mountpoint so that its /proc, /sys, /dev and /run
# mounts are correctly scoped to this build root. `pacstrap -M` skips this.
if ! mountpoint -q "$root"; then
  mount --bind "$root" "$root" || {
    echo "cannot bind-mount rootfs for arch-chroot: $root (Podman needs mount privileges)" >&2; exit 4;
  }
fi
# Prevent generic Arch kernel from reaching the PS4 image. Dependency
# selection must be correct at pacstrap time; late removal cannot fix it.
if arch-chroot "$root" pacman -Qq linux >/dev/null 2>&1; then
  arch-chroot "$root" pacman -Qi linux || true
  echo 'generic Arch linux selected; linux-orbis-ps4 must satisfy verified NTSYNC-MODULE' >&2
  exit 4
fi
arch-chroot "$root" /bin/sh -e <<CH
echo '==> Running systemd-sysusers in the verified rootfs'
systemd-sysusers
echo '==> Running systemd-tmpfiles --create in the verified rootfs'
systemd-tmpfiles --create
systemctl enable NetworkManager.service sshd.service orbisd.service orbis-web.socket lightdm.service
if [ "$lan_only" = 1 ]; then systemctl enable orbis-lan-only.service; fi
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
rm -f "$root/.orbis-image-assembly"
rootfs_security_check after-chroot
if [ -n "$authkey" ]; then
  account_uid="$(arch-chroot "$root" id -u orbis)"
  account_gid="$(arch-chroot "$root" id -g orbis)"
  install -d -m700 -o "$account_uid" -g "$account_gid" "$root/home/orbis/.ssh"
  install -m600 -o "$account_uid" -g "$account_gid" "$authkey" "$root/home/orbis/.ssh/authorized_keys"
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
