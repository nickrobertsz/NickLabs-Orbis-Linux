#!/usr/bin/env bash
# NickLabs Orbis — hardware qualification run. Execute ON THE PS4 as root, over SSH
# (the run switches personalities and closes graphical sessions).
#   sudo orbis-qualify [--model CUH-1215A] [--skip-gaming] [--deep]      (installed copy)
#   sudo tests/hardware/qualify.sh ...                                   (from a source checkout)
# Writes <date>-<group>-<release>.json to /var/lib/orbis/receipts (installed copy) or
# tests/hardware/receipts (checkout). Nothing here reboots.
set -euo pipefail
model=""; skip_gaming=0; deep=""
while [ $# -gt 0 ]; do
  case "$1" in --model) model="$2"; shift 2;; --skip-gaming) skip_gaming=1; shift;; --deep) deep="--deep"; shift;; *) exit 2;; esac
done
here="$(cd "$(dirname "$0")" && pwd)"
case "$here" in /usr/*) out_dir="${ORBIS_RECEIPTS_DIR:-/var/lib/orbis/receipts}" ;; *) out_dir="$here/receipts" ;; esac
mkdir -p "$out_dir"
tmp="$(mktemp -d)"
orbis --json --direct doctor $deep > "$tmp/doctor.json" || true
orbis --json --direct capabilities > "$tmp/caps.json"
cp /var/log/orbis/transitions.jsonl "$tmp/transitions.before" 2>/dev/null || : > "$tmp/transitions.before"
seq=(ai pc)
[ "$skip_gaming" = 1 ] || seq=(ai gaming pc)
for m in "${seq[@]}"; do
  echo "== switching to $m"
  orbis --json --direct mode "$m" --policy fast --force --yes > "$tmp/switch-$m.json" || true
  sleep 10
  orbis --json --direct budget > "$tmp/budget-$m.json"
  /usr/lib/orbis/orbis-gpu-probe --json --gl --vk > "$tmp/probe-$m.json" 2>/dev/null || true
done
python3 - "$tmp" "$out_dir" "$model" <<'PY'
import json, sys, time, platform, glob, os
tmp, out, model = sys.argv[1:4]
L = lambda p: json.load(open(p)) if os.path.exists(p) and os.path.getsize(p) else None
caps = L(f"{tmp}/caps.json") or {}
rec = {"schema": 1, "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "declared_model": model,
       "group": caps.get("group"), "kernel": platform.release(),
       "doctor": L(f"{tmp}/doctor.json"), "capabilities": caps,
       "switches": {os.path.basename(p)[7:-5]: L(p) for p in sorted(glob.glob(f"{tmp}/switch-*.json"))},
       "budgets": {os.path.basename(p)[7:-5]: {k: (L(p) or {}).get(k) for k in ("mem_total_kib", "mem_available_kib", "mem_used_kib", "gpu", "slices")}
                   for p in sorted(glob.glob(f"{tmp}/budget-*.json"))},
       "probes": {os.path.basename(p)[6:-5]: L(p) for p in sorted(glob.glob(f"{tmp}/probe-*.json"))},
       "operator_checks_required": ["HDMI picture visible after each switch (operator confirms)",
                                    "X11 -> TTY -> X11 transition keeps picture",
                                    "audio plays over HDMI", "controller works in GAMING",
                                    "Wi-Fi associates to the LAN (mwifiex_sdio) or Ethernet link is up",
                                    "LAN-only: 'curl -m5 https://example.com' fails while the LAN dashboard works"]}
name = f"{out}/{time.strftime('%Y%m%d-%H%M%S')}-{rec['group'] or 'unknown'}-{rec['kernel']}.json"
json.dump(rec, open(name, "w"), indent=2, default=str)
print(name)
PY
