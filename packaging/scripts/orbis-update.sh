#!/usr/bin/env bash
# Controlled Orbis update. Dry run unless --apply.
#   sudo orbis-update [--to-snapshot YYYY/MM/DD] [--apply]
# * Arch packages only move to an Arch Linux Archive date listed as QUALIFIED in
#   /usr/share/orbis/qualified-snapshots (shipped and signed with orbis-platform).
# * The PS4 graphics/kernel guard hooks stay active: a generic Mesa or kernel can never
#   sneak in; Orbis graphics/kernel only change when the [orbis] repo publishes them.
# * Before changing anything: a package-list checkpoint (and a btrfs snapshot when / is
#   btrfs), and the current kernel pair stays on the boot partition as *.orbis-prev.
set -euo pipefail
apply=0; to=""
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) apply=1; shift ;;
    --to-snapshot) to="$2"; shift 2 ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done
cur="$(sed -n 's#^Server = https://archive.archlinux.org/repos/\([0-9/]*\)/.*#\1#p' /etc/pacman.conf | head -1)"
qual=/usr/share/orbis/qualified-snapshots
echo "current snapshot: ${cur:-unknown}"
if [ -n "$to" ]; then
  grep -qx "$to" "$qual" 2>/dev/null || { echo "snapshot $to is not in $qual (not qualified)" >&2; exit 3; }
fi
stamp="$(date +%Y%m%d-%H%M%S)"
echo "+ checkpoint package list -> /var/lib/orbis/checkpoints/$stamp.txt"
if [ "$apply" = 1 ]; then mkdir -p /var/lib/orbis/checkpoints; pacman -Q > "/var/lib/orbis/checkpoints/$stamp.txt"; fi
if [ "$(findmnt -no FSTYPE /)" = btrfs ]; then
  echo "+ btrfs snapshot of / -> /.orbis-snapshots/$stamp (read-only)"
  [ "$apply" = 1 ] && { mkdir -p /.orbis-snapshots; btrfs subvolume snapshot -r / "/.orbis-snapshots/$stamp"; }
fi
if [ -n "$to" ] && [ "$to" != "$cur" ]; then
  echo "+ move Arch snapshot $cur -> $to"
  [ "$apply" = 1 ] && sed -i "s#/repos/$cur/#/repos/$to/#g" /etc/pacman.conf
fi
if [ "$apply" = 1 ]; then
  pacman -Syu
  orbis doctor || echo "!! doctor reports problems; 'orbis boot rollback-kernel' restores the previous kernel pair"
else
  echo "+ pacman -Syu   (preview follows)"
  pacman -Sup --print-format '%n %v' 2>/dev/null | head -100 || true
fi
