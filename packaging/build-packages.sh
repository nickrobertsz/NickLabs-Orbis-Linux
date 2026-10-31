#!/usr/bin/env bash
# Build every Orbis package into out/repo (run inside packaging/Containerfile).
#   packaging/build-packages.sh [pkg ...]
# When out/keys/gnupg holds the local Orbis signing key (packaging/scripts/orbis-repo-key.sh),
# every package and the repository database are signed with it.
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
out="$repo_root/out/repo"; mkdir -p "$out"
[[ "${ORBIS_DISPOSABLE_BUILD:-}" == 1 ]] || {
  echo 'Refusing package install operations outside an explicitly disposable build container' >&2; exit 10;
}
[[ -f /run/.containerenv || -f /.dockerenv ]] || {
  echo 'Refusing to run package build on host (container marker missing)' >&2; exit 10;
}
pkgs=("$@"); [ ${#pkgs[@]} -gt 0 ] || pkgs=(orbis-platform llama-cpp-orbis linux-orbis-ps4 mesa-orbis-ps4 lib32-mesa-orbis-ps4 xf86-video-amdgpu-orbis-ps4)
chown -R builder "$out" 2>/dev/null || true
source "$repo_root/packaging/mesa-handoff.sh"
toolstamp="core-cache-v1 container=${ORBIS_CONTAINER_ID:-unknown} snapshot=${ORBIS_SNAPSHOT:-unknown} pacman=$(pacman -Q | sha256sum | cut -d' ' -f1)"
cache_fp() {
  python3 - "$repo_root" "$1" "$2" "$toolstamp" <<'PY'
import hashlib, os, sys
root, name, recipe, tool = sys.argv[1:]
h = hashlib.sha256(b'orbis-core-package-cache-v1\0' + tool.encode())
parts = [recipe, os.path.join(root, 'packaging', 'Containerfile'),
         os.path.join(root, 'packaging', 'build-packages.sh'),
         os.path.join(root, 'packaging', 'mesa-handoff.sh'),
         os.path.join(root, 'manifests')]
extra = {'orbis-platform': ['platform', 'tools/gpu-probe', 'tools/inference-harness',
                            'packaging/initramfs', 'packaging/scripts', 'ui', 'modes',
                            'docs', 'tests/hardware', 'LICENSE'],
         'linux-orbis-ps4': ['kernel', 'platform/orbis/bzimage.py'],
         'llama-cpp-orbis': [], 'mesa-orbis-ps4': ['graphics/mesa-orbis-ps4'],
         'lib32-mesa-orbis-ps4': ['graphics/mesa-orbis-ps4', 'graphics/lib32-mesa-orbis-ps4'],
         'xf86-video-amdgpu-orbis-ps4': ['graphics/mesa-orbis-ps4']}
parts.extend(os.path.join(root, x) for x in extra.get(name, []))
if name == 'linux-orbis-ps4' and os.environ.get('ORBIS_KERNEL_OUT'):
    kernel_root = os.environ['ORBIS_KERNEL_OUT']
    parts.extend(os.path.join(kernel_root, f) for f in ('SHA256SUMS', 'build-manifest.json'))
seen = set()
for part in parts:
    if os.path.isfile(part): files = [part]
    elif os.path.isdir(part):
        files = []
        for base, dirs, filenames in os.walk(part):
            rel = os.path.relpath(base, root).replace(os.sep, '/')
            generated_recipe = (os.path.normpath(base) == os.path.normpath(recipe) or
                                rel in ('graphics/mesa-orbis-ps4',
                                       'graphics/lib32-mesa-orbis-ps4',
                                       'graphics/xf86-video-amdgpu-orbis-ps4'))
            dirs[:] = sorted(x for x in dirs if x not in ('.git', 'out', '__pycache__', 'target')
                             and not (generated_recipe and x in ('pkg', 'src')))
            files.extend(os.path.join(base, f) for f in filenames
                         if not f.endswith(('.pkg.tar.zst', '.log', '.pyc', '.tar.xz')))
    else: files = []
    for f in sorted(files):
        if f in seen: continue
        if os.path.relpath(f, root).replace(os.sep, '/') == 'tools/gpu-probe/orbis-gpu-probe':
            # Built executable, not a build input; the source and Makefile are hashed.
            continue
        seen.add(f)
        h.update(os.path.relpath(f, root).encode() + b'\0')
        if os.path.islink(f):
            # Hash source symlink identity without following a broken generated
            # debug-symbol link, or escaping the source tree.
            h.update(b'symlink\0' + os.readlink(f).encode() + b'\0')
            continue
        with open(f, 'rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''): h.update(block)
print(h.hexdigest())
PY
}
build_one() {
  local p="$1" d="$2" fp cache f base
  fp="$(cache_fp "$p" "$d")"
  cache="$repo_root/out/pkgcache-core/$p/$fp"
  if [[ -s "$cache/RECEIPT" && -s "$cache/SHA256SUMS" ]] &&
      grep -Fxq "fingerprint=$fp" "$cache/RECEIPT" &&
      (cd "$cache" && sha256sum -c --status SHA256SUMS); then
    echo "== Verified cached Core package: $p ($fp)"
    for f in "$cache"/*.pkg.tar.zst; do
      pacman -Qp -- "$f" >/dev/null
      cp -- "$f" "$out/"
    done
    return 0
  fi
  echo "== Build Core package: $p (cache $fp)"
  ( cd "$d" && sudo -u builder env ORBIS_KERNEL_OUT="${ORBIS_KERNEL_OUT:-}" PKGDEST="$out" \
      makepkg --syncdeps --noconfirm --cleanbuild --force )
  local outputs=()
  mapfile -t outputs < <(cd "$d" && sudo -u builder env ORBIS_KERNEL_OUT="${ORBIS_KERNEL_OUT:-}" PKGDEST="$out" \
      makepkg --packagelist)
  [[ ${#outputs[@]} -gt 0 ]] || { echo "Package $p produced no outputs" >&2; return 1; }
  local fresh="${cache}.new.$$"
  mkdir -p "$fresh"
  for f in "${outputs[@]}"; do
    [[ -s "$f" ]] || { echo "Expected package missing: $f" >&2; return 1; }
    pacman -Qp -- "$f" >/dev/null
    cp -- "$f" "$fresh/"
  done
  (cd "$fresh" && sha256sum -- *.pkg.tar.zst > SHA256SUMS)
  printf 'fingerprint=%s\ntoolstamp=%s\n' "$fp" "$toolstamp" > "$fresh/RECEIPT"
  mkdir -p "$(dirname "$cache")"
  if [[ -d "$cache" ]]; then mv -- "$cache" "${cache}.previous.$(date +%s).$$"; fi
  mv -- "$fresh" "$cache"
}
for p in "${pkgs[@]}"; do
  if [ -d "$repo_root/packaging/archlinux/$p" ]; then d="$repo_root/packaging/archlinux/$p"; else d="$repo_root/graphics/$p"; fi
  echo "== $p ($d)"
  build_one "$p" "$d"
  # ORBIS_REBUILD_INSTALL_LOCAL_MESA
  # lib32-mesa depends on the locally built 64-bit Mesa package.
  # Install that package INSIDE the disposable Podman build environment.
  if [ "$p" = mesa-orbis-ps4 ]; then
    orbis_mesa_handoff "$out/mesa-orbis-ps4-26.2.5-1-x86_64.pkg.tar.zst" 'mesa-orbis-ps4 26.2.5-1'
  fi
done
sign=()
keys="$repo_root/out/keys"
if [ -f "$keys/orbis-repo-key.fingerprint" ] && [ -d "$keys/gnupg" ]; then
  export GNUPGHOME="$keys/gnupg"
  fpr="$(cat "$keys/orbis-repo-key.fingerprint")"
  verify_own_sig() {
    local f="$1"
    gpg --batch --status-fd 1 --verify "$f.sig" "$f" 2>/dev/null |
      awk -v key="$fpr" '$1=="[GNUPG:]" && $2=="VALIDSIG" && $3==key {ok=1} END {exit !ok}'
  }
  for f in "$out"/*.pkg.tar.zst; do
    if [ -s "$f.sig" ] && verify_own_sig "$f"; then continue; fi
    gpg --batch --yes --pinentry-mode loopback --passphrase '' -u "$fpr" --detach-sign --no-armor "$f"
    verify_own_sig "$f"
  done
  sign=(-s -k "$fpr")
  echo "signed with $fpr"
else
  echo "NOTE: no local signing key (packaging/scripts/orbis-repo-key.sh); repository is unsigned" >&2
fi
rm -f "$out"/orbis.db* "$out"/orbis.files*
repo-add -R "${sign[@]}" "$out/orbis.db.tar.zst" "$out"/*.pkg.tar.zst
( cd "$out" && sha256sum *.pkg.tar.zst > SHA256SUMS )
if [[ -n "${HOST_UID:-}" && -n "${HOST_GID:-}" ]]; then
  chown -R "${HOST_UID}:${HOST_GID}" "$repo_root/out/pkgcache-core" 2>/dev/null || true
fi
