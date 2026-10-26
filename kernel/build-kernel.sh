#!/usr/bin/env bash
# NickLabs Orbis — reproducible PS4 kernel build.
#   kernel/build-kernel.sh <target-id> [--jobs N] [--out DIR] [--src DIR]
# Fetches the pinned source, verifies the commit, applies the Orbis patch series,
# merges config fragments, builds bzImage + modules, runs the PS4 loader check,
# and writes artifacts + manifest + SHA256SUMS. Never touches the host's /boot.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
target="${1:?usage: build-kernel.sh <target-id>}"; shift
jobs="$(nproc)"; out="$repo/out/kernel/$target"; src=""
while [ $# -gt 0 ]; do
  case "$1" in
    --jobs) jobs="$2"; shift 2 ;;
    --out) out="$2"; shift 2 ;;
    --src) src="$2"; shift 2 ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done
manifest="$here/targets/$target.json"
[ -f "$manifest" ] || { echo "no target manifest $manifest" >&2; exit 2; }
j() { python3 -c "import json,sys; d=json.load(open('$manifest')); print(eval('d'+sys.argv[1]))" "$1"; }
url="$(j "['source']['repository']")"
commit="$(j "['source']['commit']")"
if [ "$target" = "baikal-7.2" ]; then
  echo "Baikal is built with Linux psX's own build system:" >&2
  echo "  git clone $url && cd linux-psx && git checkout $commit && make TARGET=$(j "['source']['make_target']")" >&2
  echo "then: orbis boot check-kernel output/*/bzImage" >&2
  exit 3
fi
tag="$(j "['source']['tag']")"
src="${src:-$repo/out/src/$target}"
mkdir -p "$out" "$(dirname "$src")"
if [ ! -d "$src/.git" ]; then
  git clone --depth 1 --branch "$tag" "$url" "$src"
fi
have="$(git -C "$src" rev-parse HEAD)"
if [ "$have" != "$commit" ]; then
  echo "source commit $have != pinned $commit" >&2; exit 4
fi
git -C "$src" reset -q --hard "$commit"
git -C "$src" clean -qfdx
while read -r p; do
  [ -z "$p" ] && continue
  git -C "$src" apply --index "$here/patches/$target/$p"
  echo "applied $p"
done < "$here/patches/$target/series"
cp "$src/config" "$src/.config"
make_args=(LLVM=1 -C "$src" -j"$jobs" KCFLAGS="-march=btver2 -mtune=btver2 -O2")
"$src/scripts/kconfig/merge_config.sh" -m -O "$src" "$src/.config" \
  $(python3 -c "import json; print(' '.join('$here/'+f for f in json.load(open('$manifest'))['config_fragments']))") >/dev/null
make "${make_args[@]}" olddefconfig >/dev/null
# Fragments must survive olddefconfig.
for want in 'CONFIG_EXTRA_FIRMWARE=""' 'CONFIG_IKCONFIG_PROC=y' 'CONFIG_X86_PS4=y' 'CONFIG_LTO_NONE=y'; do
  grep -qx "$want" "$src/.config" || { echo "config check failed: $want" >&2; exit 5; }
done
start=$(date +%s)
make "${make_args[@]}" bzImage modules
rel="$(make -s "${make_args[@]}" kernelrelease)"
rm -rf "$out/modroot"
make "${make_args[@]}" INSTALL_MOD_PATH="$out/modroot" INSTALL_MOD_STRIP=1 modules_install >/dev/null
cp "$src/arch/x86/boot/bzImage" "$out/bzImage"
cp "$src/.config" "$out/config"
tar -C "$out/modroot" -cf "$out/modules-$rel.tar" .
PYTHONPATH="$repo/platform" python3 -m orbis.bzimage --json "$out/bzImage" > "$out/bzimage-check.json" || {
  echo "bzImage FAILED the PS4 loader check" >&2; cat "$out/bzimage-check.json" >&2; exit 6; }
( cd "$out" && sha256sum bzImage config "modules-$rel.tar" > SHA256SUMS )
python3 - "$out" "$rel" "$target" "$commit" "$start" "$here/patches/$target/series" <<'PY'
import json, sys, time, subprocess, platform
out, rel, target, commit, start, series = sys.argv[1:7]
cc = subprocess.run(["clang", "--version"], capture_output=True, text=True).stdout.splitlines()[0]
json.dump({"target": target, "kernel_release": rel, "source_commit": commit,
           "patches": [l.strip() for l in open(series) if l.strip()],
           "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "build_seconds": int(time.time()) - int(start),
           "host": platform.platform(), "compiler": cc,
           "status": "compiled; NOT boot-tested", "bzimage_check": json.load(open(f"{out}/bzimage-check.json"))},
          open(f"{out}/build-manifest.json", "w"), indent=2)
PY
echo "built $rel -> $out"
