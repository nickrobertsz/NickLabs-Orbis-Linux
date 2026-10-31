#!/usr/bin/env bash
# NickLabs Orbis — build a complete, installable release on one machine (Nobara/Fedora or
# any x86-64 Linux with Podman or Docker). See docs/INSTALL-NOBARA.md.
#
#   packaging/release.sh [--profile cuh-1215a] [--kernel-tar FILE | --build-kernel]
#                        [--stage0-only] [--internet-allowed] [--sign] [--size 32G]
#                        [--engine podman|docker] [--ssh-key FILE] [--no-password]
#
# Login on the console: user 'orbis'. The release asks for an initial password (changed at
# first login) unless --no-password; --ssh-key adds your public key (default: ~/.ssh/id_ed25519.pub
# when it exists). At least one of the two is required for a full image.
#
# Steps (each prints what it does; nothing touches a disk — flashing is a separate command):
#   1  fetch + verify community references (firmware source, loader payload, recovery kernel)
#   2  fetch the pinned initramfs-ps4 source
#   3  kernel: reuse the released build (--kernel-tar, checked) or compile (--build-kernel)
#   4  initramfs for the profile (firmware verified) + the firmware-free variant
#   5  stage-0 image (hardware report, no root filesystem needed)            [--stage0-only stops here]
#   6  optional local signing key (--sign)
#   7  build container; Orbis packages into out/repo (Mesa compiles here: about 1-2 h)
#   8  Arch root filesystem from the pinned snapshot + out/repo
#   9  firmware provenance check against Arch linux-firmware
#  10  full USB image, compressed; SHA256SUMS; release manifest; SBOM; source tarball
set -euo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"
cd "$R"
profile="cuh-1215a"; kernel_tar=""; build_kernel=0; stage0_only=0; internet=0; sign=0
size="32G"; engine=""; sshkey=""; nopw=0
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) profile="$2"; shift 2 ;;
    --kernel-tar) kernel_tar="$(realpath "$2")"; shift 2 ;;
    --build-kernel) build_kernel=1; shift ;;
    --stage0-only) stage0_only=1; shift ;;
    --internet-allowed) internet=1; shift ;;
    --sign) sign=1; shift ;;
    --size) size="$2"; shift 2 ;;
    --engine) engine="$2"; shift 2 ;;
    --ssh-key) sshkey="$(realpath "$2")"; shift 2 ;;
    --no-password) nopw=1; shift ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done
VERSION="$(python3 -c "import sys; sys.path.insert(0,'platform'); import orbis; print(orbis.__version__)")"
SNAPSHOT="$(grep -v '^#' manifests/qualified-snapshots.txt | awk 'NF {print $1; exit}')"
TARGET="$(python3 -c "import json,sys; print(json.load(open('manifests/initramfs-profiles.json'))['profiles'][sys.argv[1]]['kernel_target'])" "$profile")"
model="$(python3 -c "import json,sys; print(json.load(open('manifests/initramfs-profiles.json'))['profiles'][sys.argv[1]].get('model', ''))" "$profile")"
rel="out/release"; mkdir -p "$rel"
step() { echo; echo "=== [$1] $2"; }
need() { command -v "$1" >/dev/null || { echo "missing: $1 ($2)" >&2; exit 2; }; }

step 0 "preflight"
need python3 "sudo dnf install python3"; need git "sudo dnf install git"; need xz "sudo dnf install xz"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || { echo "python3 >= 3.11 required" >&2; exit 2; }
[ "$(uname -m)" = x86_64 ] || { echo "build on an x86_64 machine" >&2; exit 2; }
if [ "$stage0_only" = 0 ]; then
  if [ -z "$engine" ]; then
    if command -v podman >/dev/null; then engine=podman; elif command -v docker >/dev/null; then engine=docker; fi
  fi
  [ -n "$engine" ] || { echo "need podman or docker (sudo dnf install podman)" >&2; exit 2; }
  free_gb=$(df -Pk "$R" | awk 'NR==2 {print int($4/1048576)}')
  [ "$free_gb" -ge 60 ] || echo "WARNING: ${free_gb} GB free here; a full release needs about 60 GB" >&2
  SUDO=""; [ "$(id -u)" = 0 ] || SUDO="sudo"
  [ -n "$sshkey" ] || { [ -f "$HOME/.ssh/id_ed25519.pub" ] && sshkey="$HOME/.ssh/id_ed25519.pub"; } || true
  pwhash=""
  if [ "$nopw" = 0 ]; then
    need openssl "sudo dnf install openssl"
    read -r -s -p "Initial password for user 'orbis' on the console (changed at first login): " pw1; echo
    read -r -s -p "Again: " pw2; echo
    [ -n "$pw1" ] && [ "$pw1" = "$pw2" ] || { echo "passwords empty or different" >&2; exit 2; }
    pwhash="$(printf '%s' "$pw1" | openssl passwd -6 -stdin)"; unset pw1 pw2
  fi
  [ -n "$sshkey" ] || [ -n "$pwhash" ] || { echo "need --ssh-key or a password for the console login" >&2; exit 2; }
fi
echo "profile=$profile target=$TARGET model=${model:-?} snapshot=$SNAPSHOT version=$VERSION engine=${engine:-none}"

step 1 "community references (verified by SHA-256)"
python3 packaging/scripts/fetch_references.py --out out/references

step 2 "initramfs-ps4 source at the pinned commit"
ic="$(python3 -c "import json; print(json.load(open('manifests/sources.json'))['initramfs']['commit'])")"
[ -d out/src/initramfs-ps4/.git ] || git clone -q https://github.com/GayCorp/initramfs-ps4 out/src/initramfs-ps4
git -C out/src/initramfs-ps4 checkout -q "$ic"
[ "$(git -C out/src/initramfs-ps4 rev-parse HEAD)" = "$ic" ]

step 3 "kernel ($TARGET)"
kout="out/kernel/$TARGET"
if [ -n "$kernel_tar" ]; then
  rm -rf "$kout" && mkdir -p "$kout"
  tar -C "$kout" --strip-components=1 -xzf "$kernel_tar"
  rm -rf "$kout/modroot"
  ( cd "$kout" && sha256sum -c SHA256SUMS )
elif [ "$build_kernel" = 1 ]; then
  kernel/build-kernel.sh "$TARGET" --out "$kout"
elif [ ! -f "$kout/bzImage" ]; then
  echo "no kernel: pass --kernel-tar orbis-kernel-aeolia-belize-6.15-build.tar.gz or --build-kernel" >&2; exit 2
fi
python3 -I -c "import sys; sys.path.insert(0, 'platform'); from orbis import bzimage; r = bzimage.inspect(sys.argv[1]); print('loader check:', 'ok' if r.ok else r.errors); sys.exit(0 if r.ok else 1)" "$kout/bzImage"
# Preflight the exact PS4 kernel capability before expensive package builds.
# Wine -> ntsync-autoload must not pull in generic Arch linux.
python3 - "$kout" <<'PY_ORBIS_NTSYNC'
import json, pathlib, re, sys, tarfile
out = pathlib.Path(sys.argv[1]); cfg = (out / 'config').read_text()
m = re.search(r'^CONFIG_NTSYNC=(y|m)$', cfg, re.M)
if not m:
    sys.exit('PS4 kernel lacks CONFIG_NTSYNC. Rebuild with updated Orbis fragment CONFIG_NTSYNC=m; old kernel archive is incompatible with full gaming support.')
if m.group(1) == 'm':
    rel = json.loads((out / 'build-manifest.json').read_text())['kernel_release']
    with tarfile.open(out / f'modules-{rel}.tar') as tf:
        if not any(re.search(r'(^|/)ntsync\.ko(\.(gz|xz|zst))?$', n) for n in tf.getnames()):
            sys.exit('CONFIG_NTSYNC=m but compiled ntsync.ko is absent')
print('==> PS4 NTSYNC kernel capability verified:', m.group(1))
PY_ORBIS_NTSYNC

step 4 "initramfs: $profile (+ firmware-free)"
python3 packaging/initramfs/build_initramfs.py --src out/src/initramfs-ps4 --profile "$profile" \
  --firmware-from out/references/initramfs.cpio.gz -o "out/initramfs/initramfs-$profile.cpio.gz"
python3 packaging/initramfs/build_initramfs.py --src out/src/initramfs-ps4 --profile firmware-free \
  -o out/initramfs/initramfs-firmware-free.cpio.gz

step 5 "stage-0 image"
s0="$rel/orbis-$VERSION-stage0-$profile.img"
if command -v mkfs.fat >/dev/null && command -v mke2fs >/dev/null; then
  python3 packaging/image/make_usb_image.py image --stage0 --kernel "$kout/bzImage" \
    --initramfs "out/initramfs/initramfs-$profile.cpio.gz" --size 1G -o "$s0" > "$s0.json"
  xz -T0 -6 -kf "$s0"
else
  echo "dosfstools/e2fsprogs missing on the host (sudo dnf install dosfstools e2fsprogs); skipping stage-0 image" >&2
fi
if [ "$stage0_only" = 1 ]; then
  ( cd "$rel" && sha256sum ./*.img.xz ./*.json 2>/dev/null > SHA256SUMS-stage0 || true )
  echo; echo "stage-0 ready: $s0 (.xz). Flash: docs/INSTALL-NOBARA.md, section 3."
  exit 0
fi

step 6 "package signing key"
if [ "$sign" = 1 ]; then packaging/scripts/orbis-repo-key.sh; else echo "unsigned local repository (pass --sign to sign it)"; fi

step 7 "build container + Orbis packages (Mesa, llama.cpp, platform, kernel package)"
core_ctr="orbis-build:$VERSION-fullbase"
$SUDO "$engine" build --build-arg SNAPSHOT="$SNAPSHOT" -t "$core_ctr" -f packaging/Containerfile packaging
container_id="$($SUDO "$engine" image inspect --format '{{.Id}}' "$core_ctr")"
[[ -n "$container_id" ]] || { echo 'Missing pinned build container identity' >&2; exit 6; }
run_c() { $SUDO "$engine" run --rm --privileged -e ORBIS_DISPOSABLE_BUILD=1 \
  -e ORBIS_CONTAINER_ID="$container_id" -e ORBIS_SNAPSHOT="$SNAPSHOT" \
  -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
  -v "$R":/src:z -w /src "$core_ctr" "$@"; }
# The container is disposable.  On an incremental build, packages left in
# out/repo already have detached Orbis signatures from an earlier pass.
# Import and trust our *existing* public key before makepkg's local Mesa handoff
# calls pacman -U, and before repo-add opens signed cached packages.  Keep this
# preflight outside build-packages.sh: that file is an input to every package
# cache fingerprint, so changing it would recompile all previously built code.
run_c env ORBIS_KERNEL_OUT="/src/$kout" bash -c '
  set -Eeuo pipefail
  if [[ -s out/keys/orbis-repo-key.asc && -s out/keys/orbis-repo-key.fingerprint ]]; then
    fpr="$(cat out/keys/orbis-repo-key.fingerprint)"
    echo "==> Initializing package-builder trust for local Orbis key $fpr"
    pacman-key --init
    pacman-key --populate archlinux
    pacman-key --add out/keys/orbis-repo-key.asc
    pacman-key --lsign-key "$fpr"
    pacman-key --finger "$fpr" >/dev/null
    echo "==> Orbis package signing key trusted inside disposable build container"
  fi
  exec packaging/build-packages.sh
'
# Refuse a stale cached PS4 kernel package before rootfs assembly.
ps4_pkgver="$(sed -n 's/^pkgver=//p' packaging/archlinux/linux-orbis-ps4/PKGBUILD)"
ps4_pkgrel="$(sed -n 's/^pkgrel=//p' packaging/archlinux/linux-orbis-ps4/PKGBUILD)"
ps4_pkg="out/repo/linux-orbis-ps4-${ps4_pkgver}-${ps4_pkgrel}-x86_64.pkg.tar.zst"
[ -s "$ps4_pkg" ] || { echo "missing rebuilt PS4 kernel package $ps4_pkg" >&2; exit 4; }
run_c bash -c 'bsdtar -xOf "$1" .PKGINFO | grep -Fxq "provides = NTSYNC-MODULE"' _ "/src/$ps4_pkg" || {
  echo 'PS4 kernel package lacks NTSYNC-MODULE; refusing stale cache' >&2; exit 4;
}

step 8 "root filesystem (Arch snapshot $SNAPSHOT + local [orbis] repo)"
args=(--snapshot "$SNAPSHOT" --repo out/repo --out out/rootfs --keys out/keys
      --kernel-out "$kout" --ps4-initramfs "out/initramfs/initramfs-$profile.cpio.gz"
      --expected-profile "$profile")
[ -n "$model" ] && args+=(--model "$model")
[ "$internet" = 1 ] && args+=(--internet-allowed)
if [ -n "$sshkey" ]; then mkdir -p out/keys && cp "$sshkey" out/keys/console-authorized-key.pub && args+=(--authorized-key out/keys/console-authorized-key.pub); fi
[ -n "$pwhash" ] && args+=(--password-hash "$pwhash")
run_c packaging/rootfs/build-rootfs.sh "${args[@]}"

step 9 "firmware provenance vs Arch linux-firmware"
run_c python3 packaging/scripts/verify_firmware_provenance.py --rootfs out/rootfs/dir \
  --initramfs-manifest "out/initramfs/initramfs-$profile.cpio.gz.manifest.json" -o "$rel/firmware-provenance.json" >/dev/null

step 10 "USB image, checksums, manifest, SBOM, source"
img="$rel/orbis-$VERSION-$profile.img"
run_c python3 packaging/image/make_usb_image.py image --rootfs-dir out/rootfs/dir --kernel "$kout/bzImage" \
  --initramfs "out/initramfs/initramfs-$profile.cpio.gz" --size "$size" -o "$img" > "$img.json"
run_c python3 packaging/scripts/verify_final_image.py --image "$img" \
  --kernel "$kout/bzImage" --kernel-manifest "$kout/build-manifest.json" \
  --initramfs "out/initramfs/initramfs-$profile.cpio.gz" --profile "$profile" \
  > "$rel/final-image-validation.json"
xz -T0 -6 -kf "$img"
python3 packaging/sbom.py > "$rel/sbom.spdx.json"
python3 packaging/dist.py --version "$VERSION"
cp out/NICKLABS-PS4-ORBIS-LINUX-SOURCE.tar.gz "$rel/"
$SUDO chown -R "$(id -u):$(id -g)" out/release out/repo out/initramfs 2>/dev/null || true
python3 - "$rel" "$profile" "$VERSION" "$SNAPSHOT" "$TARGET" "$container_id" <<'PY'
import hashlib, json, sys
from pathlib import Path
rel = Path(sys.argv[1])
profile, version, snapshot, target, container_id = sys.argv[2:]
def h(p):
    d = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""): d.update(c)
    return d.hexdigest()
img = json.loads((rel / f"orbis-{version}-{profile}.img.json").read_text())
(rel / "release-manifest.json").write_text(json.dumps({
    "schema": "orbis-core-release/2", "version": version, "profile": profile,
    "target": target, "snapshot": snapshot, "build_container_id": container_id,
    "redistributable": img["boot"]["redistributable"],
    "kernel": img["boot"]["kernel"], "initramfs": img["boot"]["initramfs"],
    "hardware_qualified": False,
    "note": "PS4-UNTESTED until console receipts exist (tests/hardware/qualify.sh)"}, indent=1) + "\n")
files = sorted(p for p in rel.iterdir() if p.is_file() and not p.name.startswith("SHA256SUMS"))
(rel / "SHA256SUMS").write_text("".join(f"{h(p)}  {p.name}\n" for p in files))
PY
( cd "$rel" && sha256sum -c --status SHA256SUMS )
echo; echo "release ready in $rel:"; ls -la "$rel"
echo "Flash with: python3 packaging/image/make_usb_image.py write --image $img --device /dev/sdX (dry run first)"
