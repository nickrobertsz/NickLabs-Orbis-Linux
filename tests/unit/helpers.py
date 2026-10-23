"""Shared fixtures for the Orbis unit tests (stdlib only)."""

from __future__ import annotations

import os
import struct
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform"))
sys.path.insert(0, str(REPO / "tools" / "inference-harness"))


def share_dir() -> Path:
    """A share dir assembled from the repository (profiles, defaults, matrix)."""
    d = Path(tempfile.mkdtemp(prefix="orbis-share-"))
    (d / "profiles").mkdir()
    for f in (REPO / "platform/config/profiles").glob("*.toml"):
        (d / "profiles" / f.name).write_text(f.read_text())
    (d / "orbis.toml").write_text((REPO / "platform/config/orbis.toml").read_text())
    (d / "capability-matrix.json").write_text((REPO / "manifests/capability-matrix.json").read_text())
    return d


class FixtureRoot:
    """A fake / with sysfs/procfs entries describing a PS4 (fat, Belize)."""

    def __init__(self, southbridge_dev=0x90CC, gpu_dev=0x9920, vram_mb=1024, mem_avail_kib=5_000_000):
        self.root = Path(tempfile.mkdtemp(prefix="orbis-root-"))
        self.share = share_dir()
        r = self.root
        pci = r / "sys/bus/pci/devices"
        self._pci(pci / "0000:00:01.0", 0x1002, gpu_dev, 0x030000)
        self._pci(pci / "0000:00:14.4", 0x104D, southbridge_dev, 0x088000)
        (r / "proc").mkdir(parents=True, exist_ok=True)
        flags = "fpu sse sse2 pni ssse3 cx16 sse4_1 sse4_2 popcnt aes xsave avx f16c lahf_lm abm bmi1 movbe pclmulqdq"
        (r / "proc/cpuinfo").write_text("".join(
            f"processor\t: {i}\ncpu family\t: 22\nmodel name\t: AMD Jaguar\nflags\t\t: {flags}\n\n" for i in range(8)))
        (r / "proc/meminfo").write_text(
            f"MemTotal:        6900000 kB\nMemFree:         4000000 kB\nMemAvailable:    {mem_avail_kib} kB\n"
            "SwapTotal:       2000000 kB\nSwapFree:        2000000 kB\nShmem:             10000 kB\nCached:  500000 kB\n")
        (r / "proc/loadavg").write_text("0.42 0.37 0.30 1/312 4242\n")
        (r / "proc/stat").write_text("cpu  4705 0 1950 92000 300 0 40 0 0 0\n")
        (r / "proc/cmdline").write_text("pci=nocrs intremap=off root=LABEL=ORBIS-ROOT rw orbis.mode=pc\n")
        (r / "proc/sys/kernel").mkdir(parents=True, exist_ok=True)
        (r / "proc/sys/kernel/osrelease").write_text("6.15.4-orbis-ps4\n")
        card = r / "sys/class/drm/card0"
        (card / "device").mkdir(parents=True, exist_ok=True)
        for k, v in (("mem_info_vram_total", vram_mb << 20), ("mem_info_vram_used", 64 << 20),
                     ("mem_info_gtt_total", 3 << 30), ("mem_info_gtt_used", 16 << 20)):
            (card / "device" / k).write_text(f"{v}\n")
        con = r / "sys/class/drm/card0-HDMI-A-1"
        con.mkdir(parents=True)
        (con / "status").write_text("connected\n")
        (con / "enabled").write_text("enabled\n")
        (con / "modes").write_text("1920x1080\n")
        (con / "edid").write_bytes(b"")
        hw = r / "sys/class/hwmon/hwmon0"
        hw.mkdir(parents=True)
        (hw / "name").write_text("k10temp\n")
        (hw / "temp1_input").write_text("58250\n")
        net = r / "sys/class/net/eth0"
        net.mkdir(parents=True)
        (net / "operstate").write_text("up\n")
        (net / "carrier").write_text("1\n")
        (r / "var/lib/orbis").mkdir(parents=True)
        (r / "etc/orbis").mkdir(parents=True)
        (r / "var/log/orbis").mkdir(parents=True)
        (r / "var/log/orbis/kmsg.fixture").write_text("[    1.0] amdgpu 0000:00:01.0: VRAM: 1024M\n")

    @staticmethod
    def _pci(d: Path, vendor: int, device: int, cls: int):
        d.mkdir(parents=True)
        (d / "vendor").write_text(f"0x{vendor:04x}\n")
        (d / "device").write_text(f"0x{device:04x}\n")
        (d / "class").write_text(f"0x{cls:06x}\n")
        (d / "revision").write_text("0x00\n")

    def boot_partition(self, vram: str | None = "1024", bootargs: str | None = None) -> Path:
        bp = self.root / "boot/ps4"
        bp.mkdir(parents=True, exist_ok=True)
        (bp / "bzImage").write_bytes(make_bzimage())
        (bp / "initramfs.cpio.gz").write_bytes(b"\x1f\x8b fake initramfs")
        if vram is not None:
            (bp / "vram.txt").write_text(vram)
        if bootargs is not None:
            (bp / "bootargs.txt").write_text(bootargs)
        return bp

    def __enter__(self):
        self._old = {k: os.environ.get(k) for k in ("ORBIS_ROOT", "ORBIS_SHARE_DIR")}
        os.environ["ORBIS_ROOT"] = str(self.root)
        os.environ["ORBIS_SHARE_DIR"] = str(self.share)
        return self

    def __exit__(self, *exc):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def make_bzimage(protocol=(2, 15), xloadflags=0x23, loadflags=0x01, version="6.15.4-orbis-ps4 (test)",
                 magic=b"HdrS") -> bytes:
    buf = bytearray(0x4000)
    buf[0x1F1] = 4                      # setup_sects
    buf[0x1FE:0x200] = b"\x55\xaa"
    buf[0x202:0x206] = magic
    struct.pack_into("<H", buf, 0x206, (protocol[0] << 8) | protocol[1])
    struct.pack_into("<H", buf, 0x20E, 0x300)   # kernel_version ptr (+0x200)
    v = version.encode() + b"\x00"
    buf[0x500:0x500 + len(v)] = v
    buf[0x211] = loadflags
    buf[0x234] = 1
    struct.pack_into("<H", buf, 0x236, xloadflags)
    struct.pack_into("<I", buf, 0x238, 2047)
    struct.pack_into("<II", buf, 0x248, 0, 0)    # payload offset/length (empty)
    return bytes(buf)


def make_gguf(path: Path, kv: dict) -> Path:
    """Write a metadata-only GGUF v3 file (no tensors)."""
    def s(x: str) -> bytes:
        b = x.encode()
        return struct.pack("<Q", len(b)) + b
    out = bytearray(b"GGUF" + struct.pack("<I", 3) + struct.pack("<QQ", 0, len(kv)))
    for k, v in kv.items():
        out += s(k)
        if isinstance(v, bool):
            out += struct.pack("<I", 7) + struct.pack("<?", v)
        elif isinstance(v, int):
            out += struct.pack("<I", 4) + struct.pack("<I", v)
        elif isinstance(v, float):
            out += struct.pack("<I", 6) + struct.pack("<f", v)
        elif isinstance(v, str):
            out += struct.pack("<I", 8) + s(v)
        elif isinstance(v, list):  # array of strings
            out += struct.pack("<I", 9) + struct.pack("<I", 8) + struct.pack("<Q", len(v))
            for item in v:
                out += s(item)
    path.write_bytes(bytes(out))
    return path
