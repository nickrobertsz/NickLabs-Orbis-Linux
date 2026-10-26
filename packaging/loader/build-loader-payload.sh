#!/usr/bin/env bash
# Build the ps4-linux-loader AIO payload from the commit pinned in manifests/sources.json,
# as an alternative to the prebuilt community payload (manifests/references.json).
#
#   packaging/loader/build-loader-payload.sh [--vram 1024] [--out out/loader]
#
# Needs gcc and make (any x86-64 Linux; the Orbis build container has both). The payload is
# freestanding (-nostdlib -ffreestanding) and runs on the PS4 under GoldHEN, not on this machine.
# Status: WRITTEN, not executed by the Orbis build host (see docs/RELEASE-STATUS.md).
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
vram=1024; out="$repo_root/out/loader"
while [ $# -gt 0 ]; do
  case "$1" in
    --vram) vram="$2"; shift 2 ;;
    --out) out="$2"; shift 2 ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done
case "$vram" in 32|64|128|256|512|1024|2048|3072|4096) ;; *) echo "--vram must be one of the loader's sizes" >&2; exit 2 ;; esac
commit="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['loader']['commit'])" "$repo_root/manifests/sources.json")"
src="$repo_root/out/src/ps4-linux-loader"
[ -d "$src/.git" ] || git clone https://github.com/ps4-linux/ps4-linux-loader "$src"
git -C "$src" fetch -q origin
git -C "$src" checkout -q "$commit"
[ "$(git -C "$src" rev-parse HEAD)" = "$commit" ] || { echo "loader source is not at $commit" >&2; exit 3; }
make -C "$src/linux" "payloads/linux-${vram}mb.bin"
mkdir -p "$out"
cp "$src/linux/payloads/linux-${vram}mb.bin" "$out/"
( cd "$out" && sha256sum "linux-${vram}mb.bin" | tee "linux-${vram}mb.bin.sha256" )
echo "payload: $out/linux-${vram}mb.bin (ps4-linux-loader $commit). vram.txt on the USB disk overrides its size."
