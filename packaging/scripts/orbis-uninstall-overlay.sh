#!/usr/bin/env bash
# Remove the Orbis overlay from an existing installation. Dry run unless --apply.
# Leaves the PS4-patched graphics stack in place (removing it would break the display);
# restores the previous kernel pair on the boot partition if one exists.
set -euo pipefail
apply=0; [ "${1:-}" = "--apply" ] && apply=1
run() { echo "+ $*"; [ "$apply" = 1 ] && "$@" || true; }
run systemctl disable --now orbis-web.socket orbisd.service
run systemctl isolate graphical.target
if [ -f /boot/ps4/bzImage.orbis-prev ]; then run orbis --direct boot rollback-kernel; fi
run orbis --direct boot set-vram 1024
echo "+ allow removal past the package guard (one transaction)"
[ "$apply" = 1 ] && echo "uninstall overlay" > /etc/orbis/allow-graphics-change
run pacman -R --noconfirm orbis-platform
echo "kept: mesa-orbis-ps4, lib32-mesa-orbis-ps4, xf86-video-amdgpu-orbis-ps4, linux-orbis-ps4 (remove manually if you switch to another PS4 graphics stack)"
