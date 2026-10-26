#!/usr/bin/env bash
# Serve the local [orbis] package repository (out/repo) to consoles on the LAN.
#   packaging/scripts/orbis-serve-repo.sh [--bind 192.168.1.20] [--port 8780]
# On the console:  sudo orbis-update --orbis-only --from http://192.168.1.20:8780/ --apply
# Read-only static files; stop with Ctrl-C. Open the port on Nobara for your LAN only:
#   sudo firewall-cmd --zone=home --add-port=8780/tcp     (runtime only; gone after reboot)
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
bind=""; port=8780
while [ $# -gt 0 ]; do
  case "$1" in
    --bind) bind="$2"; shift 2 ;;
    --port) port="$2"; shift 2 ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done
[ -f "$repo_root/out/repo/orbis.db.tar.zst" ] || { echo "no out/repo/orbis.db.tar.zst; run make packages first" >&2; exit 2; }
if [ -z "$bind" ]; then
  bind="$(ip -4 route get 192.168.0.1 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i=="src"){print $(i+1); exit}}')"
fi
case "$bind" in
  10.*|192.168.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*) ;;
  *) echo "refusing to serve on '$bind': give a private LAN address with --bind" >&2; exit 2 ;;
esac
echo "serving $repo_root/out/repo on http://$bind:$port/  (Ctrl-C to stop)"
exec python3 -m http.server "$port" --bind "$bind" --directory "$repo_root/out/repo"
