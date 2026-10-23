"""pacman (ALPM) hook handler that protects the PS4 graphics and kernel stack.

Failure mode being prevented: a routine `pacman -Syu` or `pacman -S mesa`
replaces the PS4-patched Mesa/libdrm/xf86-video-amdgpu with generic Arch
packages, or pulls a generic kernel, and the next boot falls back to
llvmpipe or loses display.

Hooks (packaging/archlinux/orbis-platform installs them):
  90-orbis-guard-install.hook   PreTransaction, Install/Upgrade of generic names
  90-orbis-guard-remove.hook    PreTransaction, Remove of Orbis-owned packages
Both run `orbis-pkg-guard <mode>` with NeedsTargets (names on stdin) and
AbortOnFail.

A deliberate change is allowed exactly once via a flag file created with
`orbis pkg allow-graphics-change --reason "..."`. The flag is consumed and the
decision is logged to /var/log/orbis/pkg-guard.log.
"""

from __future__ import annotations

import fnmatch
import json
import sys
import time

from . import paths

# Generic packages that would replace or shadow the Orbis PS4 builds.
GENERIC_GRAPHICS = [
    "mesa", "lib32-mesa", "mesa-git", "lib32-mesa-git", "mesa-amber", "vulkan-radeon",
    "lib32-vulkan-radeon", "vulkan-swrast", "lib32-vulkan-swrast", "opencl-mesa",
    # libdrm is intentionally NOT guarded: the PS4 libdrm patch only adds marketing names
    # to amdgpu.ids, so Orbis uses Arch's (snapshot-pinned) libdrm.
    "xf86-video-amdgpu", "xf86-video-amdgpu-git",
    "mesa-ps4", "lib32-mesa-ps4", "libdrm-ps4", "lib32-libdrm-ps4", "xf86-video-amdgpu-ps4",
    "amdvlk", "lib32-amdvlk", "vulkan-amdgpu-pro",
]
GENERIC_KERNELS = ["linux", "linux-lts", "linux-zen", "linux-hardened", "linux-rt",
                   "linux-cachyos*", "linux-ps4*"]
ORBIS_OWNED = [
    "mesa-orbis-ps4", "lib32-mesa-orbis-ps4",
    "xf86-video-amdgpu-orbis-ps4", "linux-orbis-ps4*", "orbis-platform", "orbis-initramfs",
]


def _flag_path():
    return paths.etc_dir() / "allow-graphics-change"


def _log(rec: dict) -> None:
    f = paths.log_dir() / "pkg-guard.log"
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(rec, t=time.time())) + "\n")
    except OSError:
        pass


def consume_override() -> str | None:
    f = _flag_path()
    try:
        reason = f.read_text().strip() or "no reason given"
    except OSError:
        return None
    try:
        f.unlink()
    except OSError:
        pass
    return reason


def evaluate(mode: str, targets: list[str]) -> tuple[bool, list[str]]:
    """Return (allowed, offending_targets)."""
    if mode == "install":
        pats = GENERIC_GRAPHICS + GENERIC_KERNELS
    elif mode == "remove":
        pats = ORBIS_OWNED
    else:
        raise ValueError(mode)
    bad = [t for t in targets if any(fnmatch.fnmatch(t, p) for p in pats)]
    return (not bad, bad)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ("install", "remove"):
        print("usage: orbis-pkg-guard install|remove < targets", file=sys.stderr)
        return 2
    mode = argv[0]
    targets = [t.strip() for t in sys.stdin.read().split() if t.strip()]
    ok, bad = evaluate(mode, targets)
    if ok:
        return 0
    reason = consume_override()
    if reason:
        _log({"mode": mode, "targets": bad, "decision": "allowed-by-override", "reason": reason})
        print(f":: orbis: allowing {mode} of {', '.join(bad)} (one-time override: {reason})")
        return 0
    _log({"mode": mode, "targets": bad, "decision": "blocked"})
    what = "replace the PS4 graphics/kernel stack with generic packages" if mode == "install" \
        else "remove PS4-specific Orbis packages"
    print(f"error: orbis: this transaction would {what}: {', '.join(bad)}", file=sys.stderr)
    print("error: orbis: generic Mesa/libdrm/kernels do not support the PS4 Liverpool GPU/bridge.",
          file=sys.stderr)
    print("error: orbis: if you really mean it: orbis pkg allow-graphics-change --reason '...'",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
