#!/usr/bin/env bash
# Controlled Orbis update. Dry run unless --apply.
#   sudo orbis-update [--to-snapshot YYYY/MM/DD] [--apply]
#   sudo orbis-update --orbis-only [--from URL] [--apply]
#        Offline/LAN-only path: sync ONLY the [orbis] repository, from the copy inside this
#        system (/var/lib/orbis/repo) or from URL (http://LAN-HOST:8780/ served by
#        orbis-serve-repo.sh, or file:///run/media/.../repo on a USB disk). With --from, the
#        fetched repository replaces /var/lib/orbis/repo after a successful upgrade.
#   sudo orbis-update --import-key FILE   trust a (new) local Orbis signing public key
# * Arch packages only move to an Arch Linux Archive date listed as QUALIFIED in
#   /usr/share/orbis/qualified-snapshots (shipped and signed with orbis-platform).
# * The PS4 graphics/kernel guard hooks stay active: a generic Mesa or kernel can never
#   sneak in; Orbis graphics/kernel only change when the [orbis] repo publishes them.
# * Before changing anything: a package-list checkpoint (and a btrfs snapshot when / is
#   btrfs), and the current kernel pair stays on the boot partition as *.orbis-prev.
set -euo pipefail
pconf="${ORBIS_PACMAN_CONF:-/etc/pacman.conf}"
apply=0; to=""; orbis_only=0; from=""; import_key=""
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) apply=1; shift ;;
    --to-snapshot) to="$2"; shift 2 ;;
    --orbis-only) orbis_only=1; shift ;;
    --from) from="$2"; orbis_only=1; shift 2 ;;
    --import-key) import_key="$2"; shift 2 ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done
if [ -n "$import_key" ]; then
  fpr="$(gpg --with-colons --show-keys "$import_key" | awk -F: '/^fpr:/ {print $10; exit}')"
  echo "+ pacman-key --add $import_key; --lsign-key $fpr"
  [ "$apply" = 1 ] && { pacman-key --add "$import_key"; pacman-key --lsign-key "$fpr"; }
  exit 0
fi
if [ "$orbis_only" = 1 ]; then
  conf="$(mktemp)"; trap 'rm -f "$conf"' EXIT
  sig="$(awk '/^\[orbis\]/{f=1;next} /^\[/{f=0} f && /^SigLevel/{print; exit}' "$pconf")"
  server="${from:-file:///var/lib/orbis/repo}"
  case "$server" in */) ;; *) server="$server/" ;; esac
  awk '/^\[/{ if (seen) exit; seen = 1 } {print}' "$pconf" > "$conf"
  printf '\n[orbis]\n%s\nServer = %s\n' "${sig:-SigLevel = Required}" "${server%/}" >> "$conf"
  echo "== Orbis-only update from $server (Arch packages are not touched)"
  stamp="$(date +%Y%m%d-%H%M%S)"
  if [ "$apply" = 1 ]; then
    mkdir -p /var/lib/orbis/checkpoints; pacman -Q > "/var/lib/orbis/checkpoints/$stamp.txt"
    pacman --config "$conf" -Syu --noconfirm
    if [ -n "$from" ]; then
      echo "+ refresh /var/lib/orbis/repo from $server"
      tmp="$(mktemp -d)"
      if [ "${server#file://}" != "$server" ]; then cp -a "${server#file://}". "$tmp/"
      else
        ( cd "$tmp" && curl -fsS "${server}orbis.db.tar.zst" -o orbis.db.tar.zst \
          && tar -xOf orbis.db.tar.zst --wildcards '*/desc' | awk '/^%FILENAME%/{getline; print}' \
          | while read -r f; do curl -fsS "${server}$f" -o "$f"; curl -fsS "${server}$f.sig" -o "$f.sig" || true; done
          curl -fsS "${server}orbis.db.tar.zst.sig" -o orbis.db.tar.zst.sig || true
          cp orbis.db.tar.zst orbis.db; )
      fi
      rm -rf /var/lib/orbis/repo.prev; mv /var/lib/orbis/repo /var/lib/orbis/repo.prev; mv "$tmp" /var/lib/orbis/repo
    fi
    orbis doctor || echo "!! doctor reports problems; 'orbis boot rollback-kernel' restores the previous kernel pair"
  else
    echo "--- temporary pacman.conf (Orbis repository only):"; sed -n '/^\[orbis\]/,$p' "$conf"
    echo "+ pacman --config <that file> -Syu   (dry run: nothing changed; add --apply)"
  fi
  exit 0
fi
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
