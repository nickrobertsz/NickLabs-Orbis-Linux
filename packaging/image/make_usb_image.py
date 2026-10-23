#!/usr/bin/env python3
"""Build a flashable Orbis USB image (or write it to a disk) without loop devices or mounts.

Layout (MBR):
  p1  FAT32  label ORBIS-BOOT  (default 1 GiB)  bzImage, initramfs.cpio.gz,
                                                 bootargs.txt, vram.txt  — read by the PS4 payload
  p2  ext4   label ORBIS-ROOT  (rest)            the root filesystem

  make_usb_image.py image --rootfs-dir ROOT --kernel bzImage --initramfs initramfs.cpio.gz \
                          --size 16G -o orbis.img [--plan]
  make_usb_image.py write --image orbis.img --device /dev/sdX [--yes-erase /dev/sdX]

Tools: mkfs.fat (dosfstools >= 4.2, for --offset), mcopy (mtools), mke2fs (e2fsprogs >= 1.43, -d).
Nothing is written to any block device unless `write` is given the device path twice
(--device and --yes-erase) and the device is not mounted, not the running root, and removable
or explicitly allowed.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

SECTOR = 512
ALIGN = 2048  # 1 MiB


def parse_size(s: str) -> int:
    mult = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}
    s = s.strip().upper().rstrip("B").rstrip("I")
    if s and s[-1] in mult:
        return int(float(s[:-1]) * mult[s[-1]])
    return int(s)


def layout(total_bytes: int, boot_bytes: int) -> dict:
    total = total_bytes // SECTOR
    p1_start = ALIGN
    p1_len = boot_bytes // SECTOR
    p2_start = ((p1_start + p1_len + ALIGN - 1) // ALIGN) * ALIGN
    p2_len = total - p2_start
    if p2_len < (1 << 30) // SECTOR:
        raise ValueError("image too small: root partition would be < 1 GiB")
    return {"total_sectors": total, "boot": (p1_start, p1_len), "root": (p2_start, p2_len)}


def mbr(lay: dict, disk_id: int = 0x4F524253) -> bytes:  # "ORBS"
    b = bytearray(SECTOR)
    struct.pack_into("<I", b, 440, disk_id)

    def entry(boot, ptype, start, length):
        # CHS fields set to the LBA-only marker (0xFE,0xFF,0xFF)
        return struct.pack("<B3sB3sII", boot, b"\xfe\xff\xff", ptype, b"\xfe\xff\xff", start, length)
    b[446:462] = entry(0x80, 0x0C, *lay["boot"])   # FAT32 LBA, active
    b[462:478] = entry(0x00, 0x83, *lay["root"])   # Linux
    b[510:512] = b"\x55\xaa"
    return bytes(b)


def need(tool: str) -> str:
    p = shutil.which(tool)
    if not p:
        raise SystemExit(f"missing tool: {tool}")
    return p


def cmd_image(a) -> int:
    lay = layout(parse_size(a.size), parse_size(a.boot_size))
    bootargs = a.bootargs or ("pci=nocrs intremap=off root=LABEL=ORBIS-ROOT rootfstype=ext4 rootwait rw "
                              "video=HDMI-A-1:1920x1080@60 consoleblank=0 net.ifnames=0 orbis.mode=" + a.mode)
    plan = {"output": a.output, "layout_sectors": lay, "bootargs": bootargs, "vram_mb": a.vram,
            "files": {"bzImage": a.kernel, "initramfs.cpio.gz": a.initramfs},
            "rootfs_dir": a.rootfs_dir}
    if a.plan:
        print(json.dumps(plan, indent=2))
        return 0
    for f in (a.kernel, a.initramfs):
        if not Path(f).is_file():
            raise SystemExit(f"missing {f}")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "platform"))
    from orbis import bzimage  # noqa: E402
    rep = bzimage.inspect(a.kernel)
    if not rep.ok:
        raise SystemExit("kernel fails the PS4 loader check: " + "; ".join(rep.errors))
    out = Path(a.output)
    with open(out, "wb") as fh:
        fh.truncate(lay["total_sectors"] * SECTOR)
        fh.write(mbr(lay))
    bs, bl = lay["boot"]
    rs, rl = lay["root"]
    subprocess.run([need("mkfs.fat"), "-F", "32", "-n", "ORBIS-BOOT", "--offset", str(bs),
                    str(out), str(bl * SECTOR // 1024)], check=True, capture_output=True)
    work = out.with_suffix(".bootfiles")
    work.mkdir(exist_ok=True)
    (work / "bootargs.txt").write_text(bootargs + "\n")
    (work / "vram.txt").write_text(f"{a.vram}\n")
    shutil.copyfile(a.kernel, work / "bzImage")
    shutil.copyfile(a.initramfs, work / "initramfs.cpio.gz")
    env = dict(os.environ, MTOOLS_SKIP_CHECK="1")
    for f in ("bzImage", "initramfs.cpio.gz", "bootargs.txt", "vram.txt"):
        subprocess.run([need("mcopy"), "-i", f"{out}@@{bs * SECTOR}", str(work / f), f"::{f}"],
                       check=True, env=env)
    shutil.rmtree(work)
    args = [need("mke2fs"), "-q", "-F", "-t", "ext4", "-L", "ORBIS-ROOT", "-E",
            f"offset={rs * SECTOR},root_owner=0:0", "-O", "^metadata_csum_seed"]
    if a.rootfs_dir:
        args += ["-d", a.rootfs_dir]
    args += [str(out), str(rl * SECTOR // 1024)]
    subprocess.run(args, check=True)
    print(json.dumps(plan, indent=2))
    return 0


def _device_safety(dev: str, allow_fixed: bool) -> list[str]:
    problems = []
    name = Path(os.path.realpath(dev)).name
    if not Path(f"/sys/class/block/{name}").exists():
        problems.append(f"{dev} is not a block device")
        return problems
    if Path(f"/sys/class/block/{name}/partition").exists():
        problems.append(f"{dev} is a partition; give the whole disk")
    removable = Path(f"/sys/class/block/{name}/removable")
    is_usb = "/usb" in os.path.realpath(f"/sys/class/block/{name}")
    if not allow_fixed and not (is_usb or (removable.exists() and removable.read_text().strip() == "1")):
        problems.append(f"{dev} is not a USB/removable disk (use --allow-fixed-disk if you are sure)")
    mounts = Path("/proc/mounts").read_text()
    for line in mounts.splitlines():
        src = line.split()[0]
        if Path(src).name.startswith(name):
            problems.append(f"{src} is mounted ({line.split()[1]})")
    return problems


def cmd_write(a) -> int:
    img = Path(a.image)
    problems = _device_safety(a.device, a.allow_fixed_disk)
    plan = {"image": str(img), "bytes": img.stat().st_size if img.exists() else None,
            "device": a.device, "problems": problems,
            "will_erase": a.yes_erase == a.device and not problems}
    print(json.dumps(plan, indent=2))
    if problems:
        return 3
    if a.yes_erase != a.device:
        print(f"dry run: nothing written. Repeat with --yes-erase {a.device} to ERASE it.", file=sys.stderr)
        return 0
    with open(img, "rb") as src, open(a.device, "r+b", buffering=0) as dst:
        while True:
            chunk = src.read(4 << 20)
            if not chunk:
                break
            dst.write(chunk)
        os.fsync(dst.fileno())
    print("written; you can now copy additional files or eject", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("image")
    i.add_argument("--rootfs-dir")
    i.add_argument("--kernel", required=True)
    i.add_argument("--initramfs", required=True)
    i.add_argument("--size", default="16G")
    i.add_argument("--boot-size", default="1G")
    i.add_argument("--mode", default="pc", choices=("pc", "gaming", "ai"))
    i.add_argument("--vram", type=int, default=1024)
    i.add_argument("--bootargs")
    i.add_argument("-o", "--output", required=True)
    i.add_argument("--plan", action="store_true")
    w = sub.add_parser("write")
    w.add_argument("--image", required=True)
    w.add_argument("--device", required=True)
    w.add_argument("--yes-erase")
    w.add_argument("--allow-fixed-disk", action="store_true")
    a = ap.parse_args()
    return cmd_image(a) if a.cmd == "image" else cmd_write(a)


if __name__ == "__main__":
    raise SystemExit(main())
