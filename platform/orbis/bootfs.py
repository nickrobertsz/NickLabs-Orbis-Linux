"""Adapter for the PS4 Linux loader's boot partition.

The PS4 has no Linux bootloader. The kexec payload (ps4-linux/ps4-linux-loader,
linux/main-aio.c) reads four files, USB first (/mnt/usb0, /mnt/usb1 in Orbis
OS terms, i.e. the FAT partition of the first USB mass-storage devices), then
/data/linux/boot and /user/system/boot on the internal drive:

    bzImage              kernel (required)
    initramfs.cpio.gz    initramfs (required)
    bootargs.txt         kernel command line, FIRST LINE ONLY (optional)
    vram.txt             GPU carve-out in MB, 32..4609, default 1024 (optional;
                         takes priority over the payload's built-in value)

Linux therefore cannot change its own VRAM reservation at runtime: the
carve-out is excluded from the E820 map and programmed into the GPU memory
controller by the loader before Linux starts. Orbis writes vram.txt and
bootargs.txt for the *next* payload launch. Every reboot returns to the PS4
system software; the operator relaunches the payload.

Orbis only writes the USB FAT partition it was configured to manage. It never
writes Sony system partitions or the internal drive.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import bzimage, paths
from .config import atomic_write

LOADER_VRAM_MIN = 32
LOADER_VRAM_MAX = 4609          # VRAM_MB_MAX in main-aio.c
LOADER_VRAM_DEFAULT = 1024      # VRAM_MB_DEFAULT
# linux_boot.c programs MTRRs with a mask that assumes a power-of-two size.
POWER_OF_TWO_VRAM = (32, 64, 128, 256, 512, 1024, 2048, 4096)

KERNEL = "bzImage"
INITRD = "initramfs.cpio.gz"
BOOTARGS = "bootargs.txt"
VRAM = "vram.txt"
PREV_SUFFIX = ".orbis-prev"
ORBIS_MODE_KEY = "orbis.mode"

# Loader's built-in command line when bootargs.txt is absent (main-aio.c).
LOADER_DEFAULT_CMDLINE = ("panic=0 clocksource=tsc consoleblank=0 net.ifnames=0 "
                          "radeon.dpm=0 amdgpu.dpm=0 drm.debug=0 "
                          "console=ttyS0,115200n8 console=tty0 "
                          "video=HDMI-A-1:1920x1080@60")


class BootFsError(Exception):
    pass


@dataclass
class Action:
    kind: str
    path: str
    detail: str
    before: str | None = None
    after: str | None = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_vram(mb: int, allowed: list[int] | None = None) -> list[str]:
    problems = []
    if not (LOADER_VRAM_MIN <= mb <= LOADER_VRAM_MAX):
        problems.append(f"{mb} MB is outside the loader range {LOADER_VRAM_MIN}..{LOADER_VRAM_MAX}")
    if mb not in POWER_OF_TWO_VRAM:
        problems.append(f"{mb} MB is not a power of two; the loader's MTRR setup assumes one")
    if allowed is not None and mb not in allowed:
        problems.append(f"{mb} MB is not in the configured preset list {allowed}")
    return problems


def parse_cmdline(text: str) -> list[str]:
    first = text.splitlines()[0] if text else ""
    return first.split()


def set_token(tokens: list[str], key: str, value: str | None) -> list[str]:
    out = [t for t in tokens if not (t == key or t.startswith(key + "="))]
    if value is not None:
        out.append(f"{key}={value}")
    return out


@dataclass
class BootPartition:
    """A mounted FAT directory holding the loader's files."""

    root: Path
    cmdline_limit: int = 2047
    actions: list[Action] = field(default_factory=list)

    # ---- reads
    def exists(self, name: str) -> bool:
        return (self.root / name).is_file()

    def read_text(self, name: str) -> str | None:
        try:
            return (self.root / name).read_text(encoding="ascii", errors="replace")
        except FileNotFoundError:
            return None

    def bootargs(self) -> list[str]:
        txt = self.read_text(BOOTARGS)
        if txt is None:
            return LOADER_DEFAULT_CMDLINE.split()
        return parse_cmdline(txt)

    def bootargs_is_default(self) -> bool:
        return self.read_text(BOOTARGS) is None

    def vram_mb(self) -> int:
        """Effective value the loader will use on next launch (mirrors main-aio.c)."""
        txt = self.read_text(VRAM)
        if not txt:
            return LOADER_VRAM_DEFAULT
        digits = ""
        for ch in txt.strip():
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            return LOADER_VRAM_DEFAULT
        v = int(digits)
        if v < LOADER_VRAM_MIN or v > LOADER_VRAM_MAX:
            return LOADER_VRAM_DEFAULT
        return v

    def status(self) -> dict:
        out = {"path": str(self.root),
               "kernel_present": self.exists(KERNEL), "initramfs_present": self.exists(INITRD),
               "previous_kernel_present": self.exists(KERNEL + PREV_SUFFIX),
               "bootargs": " ".join(self.bootargs()),
               "bootargs_file": self.exists(BOOTARGS),
               "next_vram_mb": self.vram_mb(),
               "vram_file": self.exists(VRAM)}
        toks = self.bootargs()
        out["next_mode"] = next((t.split("=", 1)[1] for t in toks
                                 if t.startswith(ORBIS_MODE_KEY + "=")), None)
        return out

    # ---- writes (each records an Action; dry_run only records)
    def _write(self, name: str, content: str, dry_run: bool, why: str) -> None:
        target = self.root / name
        before = self.read_text(name)
        if before == content:
            return
        self.actions.append(Action("write", str(target), why,
                                   before.strip() if before else None, content.strip()))
        if dry_run:
            return
        if before is not None:
            # Keep exactly one previous copy so an operator can restore it over FTP.
            shutil.copyfile(target, self.root / (name + PREV_SUFFIX))
        atomic_write(target, content.encode("ascii"))

    def set_vram(self, mb: int, allowed: list[int] | None, dry_run: bool = False) -> None:
        problems = validate_vram(mb, allowed)
        if problems:
            raise BootFsError("; ".join(problems))
        self._write(VRAM, f"{mb}\n", dry_run, f"GPU carve-out for next payload launch: {mb} MB")

    def set_mode(self, mode: str, dry_run: bool = False) -> None:
        toks = set_token(self.bootargs(), ORBIS_MODE_KEY, mode)
        line = " ".join(toks)
        if len(line) >= self.cmdline_limit:
            raise BootFsError(f"command line would be {len(line)} bytes; limit {self.cmdline_limit}")
        self._write(BOOTARGS, line + "\n", dry_run, f"select {mode} personality on next boot")

    def install_kernel_pair(self, kernel: Path, initramfs: Path, dry_run: bool = False,
                            force: bool = False) -> dict:
        """Install a kernel + initramfs, keeping the current pair as *.orbis-prev.

        The new kernel is checked against the loader contract first. The copy is
        staged under a temporary name and renamed so there is never a moment
        without a bootable kernel on the partition.
        """
        rep = bzimage.inspect(kernel)
        if not rep.ok and not force:
            raise BootFsError("refusing kernel: " + "; ".join(rep.errors))
        if rep.cmdline_size:
            self.cmdline_limit = rep.cmdline_size
        for src, name in ((kernel, KERNEL), (initramfs, INITRD)):
            dst = self.root / name
            self.actions.append(Action("install", str(dst), f"from {src}",
                                       sha256_file(dst) if dst.exists() else None,
                                       sha256_file(src)))
            if dry_run:
                continue
            if dst.exists():
                shutil.copyfile(dst, self.root / (name + PREV_SUFFIX))
            tmp = self.root / (name + ".orbis-new")
            shutil.copyfile(src, tmp)
            with open(tmp, "rb+") as fh:
                os.fsync(fh.fileno())
            os.replace(tmp, dst)
        return rep.to_dict()

    def rollback_kernel_pair(self, dry_run: bool = False) -> None:
        for name in (KERNEL, INITRD):
            prev = self.root / (name + PREV_SUFFIX)
            if not prev.exists():
                raise BootFsError(f"no previous {name} to roll back to")
        for name in (KERNEL, INITRD):
            cur = self.root / name
            prev = self.root / (name + PREV_SUFFIX)
            self.actions.append(Action("swap", str(cur), f"swap with {prev.name}"))
            if dry_run:
                continue
            tmp = self.root / (name + ".orbis-swap")
            os.replace(cur, tmp)
            os.replace(prev, cur)
            os.replace(tmp, prev)


def locate(cfg: dict, mount: bool = False) -> BootPartition | None:
    """Find the managed boot partition.

    Order: explicit mountpoint from config if it holds a bzImage; else the
    partition with the configured FAT label (mounted on demand when `mount`).
    """
    bcfg = cfg.get("boot", {})
    mp = paths.p(bcfg.get("mountpoint", "/boot/ps4"))
    if (mp / KERNEL).is_file():
        return BootPartition(mp)
    label = bcfg.get("fat_label", "ORBIS-BOOT")
    dev = paths.p("dev/disk/by-label", label)
    if dev.exists() and mount and os.geteuid() == 0:
        mp.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["mount", "-t", "vfat", "-o", "rw,noatime,flush,umask=0022",
                            str(dev), str(mp)], capture_output=True, text=True)
        if r.returncode != 0:
            raise BootFsError(f"mount {dev} failed: {r.stderr.strip()}")
        if (mp / KERNEL).is_file():
            return BootPartition(mp)
    return None


def candidate_loader_partitions() -> list[str]:
    """FAT filesystems holding a bzImage. More than one is ambiguous for the loader."""
    found = []
    mounts = (paths.procfs("self/mounts").read_text(errors="replace")
              if paths.procfs("self/mounts").exists() else "")
    for line in mounts.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] in ("vfat", "exfat", "msdos"):
            mnt = paths.p(parts[1].replace("\\040", " "))
            if (mnt / KERNEL).is_file():
                found.append(parts[1])
    return found
