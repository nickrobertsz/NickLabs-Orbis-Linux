#!/usr/bin/env bash
# Build every Orbis package into out/repo (run inside packaging/Containerfile).
#   packaging/build-packages.sh [pkg ...]
# When out/keys/gnupg holds the local Orbis signing key (packaging/scripts/orbis-repo-key.sh),
# every package and the repository database are signed with it.
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
out="$repo_root/out/repo"; mkdir -p "$out"
pkgs=("$@"); [ ${#pkgs[@]} -gt 0 ] || pkgs=(orbis-platform llama-cpp-orbis linux-orbis-ps4 mesa-orbis-ps4 lib32-mesa-orbis-ps4 xf86-video-amdgpu-orbis-ps4)
chown -R builder "$out" 2>/dev/null || true
for p in "${pkgs[@]}"; do
  if [ -d "$repo_root/packaging/archlinux/$p" ]; then d="$repo_root/packaging/archlinux/$p"; else d="$repo_root/graphics/$p"; fi
  echo "== $p ($d)"
  ( cd "$d" && sudo -u builder env ORBIS_KERNEL_OUT="${ORBIS_KERNEL_OUT:-}" \
      makepkg --syncdeps --noconfirm --cleanbuild --force PKGDEST="$out" )
done
sign=()
keys="$repo_root/out/keys"
if [ -f "$keys/orbis-repo-key.fingerprint" ] && [ -d "$keys/gnupg" ]; then
  export GNUPGHOME="$keys/gnupg"
  fpr="$(cat "$keys/orbis-repo-key.fingerprint")"
  for f in "$out"/*.pkg.tar.zst; do
    [ -f "$f.sig" ] && [ "$f.sig" -nt "$f" ] && continue
    gpg --batch --yes --pinentry-mode loopback --passphrase '' -u "$fpr" --detach-sign --no-armor "$f"
  done
  sign=(-s -k "$fpr")
  echo "signed with $fpr"
else
  echo "NOTE: no local signing key (packaging/scripts/orbis-repo-key.sh); repository is unsigned" >&2
fi
rm -f "$out"/orbis.db* "$out"/orbis.files*
repo-add -n -R "${sign[@]}" "$out/orbis.db.tar.zst" "$out"/*.pkg.tar.zst
( cd "$out" && sha256sum *.pkg.tar.zst > SHA256SUMS )
