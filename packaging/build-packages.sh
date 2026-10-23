#!/usr/bin/env bash
# Build every Orbis package into out/repo (run inside packaging/Containerfile).
#   packaging/build-packages.sh [pkg ...]
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
out="$repo_root/out/repo"; mkdir -p "$out"
pkgs=("$@"); [ ${#pkgs[@]} -gt 0 ] || pkgs=(orbis-platform llama-cpp-orbis linux-orbis-ps4 mesa-orbis-ps4 lib32-mesa-orbis-ps4 xf86-video-amdgpu-orbis-ps4)
for p in "${pkgs[@]}"; do
  if [ -d "$repo_root/packaging/archlinux/$p" ]; then d="$repo_root/packaging/archlinux/$p"; else d="$repo_root/graphics/$p"; fi
  echo "== $p ($d)"
  ( cd "$d" && sudo -u builder makepkg --syncdeps --noconfirm --cleanbuild --force PKGDEST="$out" )
done
repo-add -n -R "$out/orbis.db.tar.zst" "$out"/*.pkg.tar.zst
( cd "$out" && sha256sum *.pkg.tar.zst > SHA256SUMS )
