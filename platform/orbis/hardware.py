"""Read-only hardware inventory.

Everything here reads sysfs/procfs (re-rooted under ORBIS_ROOT for tests)
and never writes or probes hardware registers. Results are plain dicts so
they serialise directly into `orbis status --json` and bug reports.

PCI identifiers come from the PS4 kernel trees (include/linux/pci_ids.h in
the PS4 sub-architecture patches) and the amdgpu PCI table additions.
"""

from __future__ import annotations

import gzip
import os
import platform
import re
from pathlib import Path

from . import paths

SONY_VENDOR = 0x104D
AMD_VENDOR = 0x1002

# Sony southbridge functions (subset is enough to identify the family).
SOUTHBRIDGE_IDS = {
    0x908F: "aeolia", 0x909E: "aeolia", 0x909F: "aeolia", 0x90A0: "aeolia",
    0x90A1: "aeolia", 0x90A2: "aeolia", 0x90A3: "aeolia", 0x90A4: "aeolia",
    0x90C8: "belize", 0x90C9: "belize", 0x90CA: "belize", 0x90CB: "belize",
    0x90CC: "belize", 0x90CD: "belize", 0x90CE: "belize", 0x90CF: "belize",
    0x90D7: "baikal", 0x90D8: "baikal", 0x90D9: "baikal", 0x90DA: "baikal",
    0x90DB: "baikal", 0x90DC: "baikal", 0x90DD: "baikal", 0x90DE: "baikal",
}
SOUTHBRIDGE_FUNCTION = {
    0x908F: "acpi", 0x909E: "ethernet", 0x909F: "ahci", 0x90A0: "sdhci",
    0x90A1: "pcie-glue", 0x90A2: "dmac", 0x90A3: "mem", 0x90A4: "xhci",
    0x90C8: "acpi", 0x90C9: "ethernet", 0x90CA: "ahci", 0x90CB: "sdhci",
    0x90CC: "pcie-glue", 0x90CD: "dmac", 0x90CE: "mem", 0x90CF: "xhci",
    0x90D7: "acpi", 0x90D8: "ethernet", 0x90D9: "ahci", 0x90DA: "sdhci",
    0x90DB: "pcie-glue", 0x90DC: "dmac", 0x90DD: "mem", 0x90DE: "xhci",
}

GPU_IDS = {
    0x9920: ("liverpool", "AMD Liverpool (PS4)"),
    0x9922: ("liverpool", "AMD Liverpool (PS4)"),
    0x9923: ("liverpool", "AMD Liverpool (PS4)"),
    0x9924: ("gladius", "AMD Gladius (PS4 Pro)"),
}

# x86-64 micro-architecture levels (psABI). Flag names as in /proc/cpuinfo.
ISA_LEVELS = {
    "x86-64-v2": {"cx16", "lahf_lm", "popcnt", "pni", "sse4_1", "sse4_2", "ssse3"},
    "x86-64-v3": {"avx", "avx2", "bmi1", "bmi2", "f16c", "fma", "abm", "movbe", "xsave"},
    "x86-64-v4": {"avx512f", "avx512bw", "avx512cd", "avx512dq", "avx512vl"},
}
JAGUAR_EXPECTED_FLAGS = {"sse4_2", "avx", "f16c", "bmi1", "movbe", "aes", "pclmulqdq"}
JAGUAR_ABSENT_FLAGS = {"avx2", "fma", "bmi2"}


def _read(path: Path, default: str | None = None) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except (FileNotFoundError, PermissionError, IsADirectoryError, OSError):
        return default


def _read_int(path: Path) -> int | None:
    v = _read(path)
    if v is None:
        return None
    try:
        return int(v, 0)
    except ValueError:
        return None


# ---------------------------------------------------------------- PCI

def pci_devices() -> list[dict]:
    base = paths.sysfs("bus/pci/devices")
    out = []
    if not base.is_dir():
        return out
    for dev in sorted(base.iterdir()):
        vendor = _read_int(dev / "vendor")
        device = _read_int(dev / "device")
        if vendor is None or device is None:
            continue
        driver = None
        try:
            driver = os.path.basename(os.readlink(dev / "driver"))
        except OSError:
            pass
        out.append({
            "slot": dev.name, "vendor": vendor, "device": device,
            "class": _read_int(dev / "class"), "revision": _read_int(dev / "revision"),
            "driver": driver,
        })
    return out


def detect_platform(pci: list[dict] | None = None) -> dict:
    pci = pci_devices() if pci is None else pci
    sb_family = None
    sb_functions = {}
    gpu = None
    for d in pci:
        if d["vendor"] == SONY_VENDOR and d["device"] in SOUTHBRIDGE_IDS:
            sb_family = SOUTHBRIDGE_IDS[d["device"]]
            sb_functions[SOUTHBRIDGE_FUNCTION[d["device"]]] = {
                "slot": d["slot"], "driver": d["driver"], "pci_id": f"104d:{d['device']:04x}"}
        if d["vendor"] == AMD_VENDOR and d["device"] in GPU_IDS:
            fam, name = GPU_IDS[d["device"]]
            gpu = {"family": fam, "name": name, "pci_id": f"1002:{d['device']:04x}",
                   "revision": d["revision"], "slot": d["slot"], "driver": d["driver"]}
    is_ps4 = sb_family is not None and gpu is not None
    return {
        "is_ps4": is_ps4,
        "southbridge": sb_family,
        "southbridge_functions": sb_functions,
        "gpu": gpu,
        "console_group": console_group(sb_family, gpu["family"] if gpu else None),
    }


def console_group(sb: str | None, gpu_family: str | None) -> dict:
    """Map detected chips to a qualification group.

    The southbridge alone does not determine the console revision: Belize
    appears in late fat consoles, slims and Pros. We never guess more than the
    silicon proves; the operator-declared CUH model narrows it further.
    """
    if gpu_family == "gladius":
        return {"group": "pro", "certain": True, "candidates": ["pro"]}
    if sb == "aeolia":
        return {"group": "fat", "certain": True, "candidates": ["fat"]}
    if sb == "baikal":
        return {"group": "slim", "certain": True, "candidates": ["slim"]}
    if sb == "belize":
        return {"group": "fat-or-slim", "certain": False, "candidates": ["fat", "slim"]}
    return {"group": "unknown", "certain": False, "candidates": []}


def model_group_from_cuh(model: str | None) -> str | None:
    """CUH-1xxx = fat, CUH-2xxx = slim, CUH-7xxx = pro."""
    if not model:
        return None
    m = re.match(r"^\s*CUH-?(\d)", model.upper())
    if not m:
        return None
    return {"1": "fat", "2": "slim", "7": "pro"}.get(m.group(1))


# ---------------------------------------------------------------- CPU

def cpu_info() -> dict:
    text = _read(paths.procfs("cpuinfo"), "") or ""
    flags: set[str] = set()
    model = None
    family = None
    cores = 0
    for line in text.splitlines():
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if key == "flags" and not flags:
            flags = set(val.split())
        elif key == "model name" and model is None:
            model = val
        elif key == "cpu family" and family is None:
            try:
                family = int(val)
            except ValueError:
                pass
        elif key == "processor":
            cores += 1
    level = "x86-64"
    if flags:
        for name in ("x86-64-v2", "x86-64-v3", "x86-64-v4"):
            if ISA_LEVELS[name] <= flags:
                level = name
            else:
                break
    jaguar_like = (family == 22 and JAGUAR_EXPECTED_FLAGS <= flags
                   and not (JAGUAR_ABSENT_FLAGS & flags))
    return {
        "model": model, "family": family, "logical_cpus": cores,
        "isa_level": level if flags else None,
        "has": {f: (f in flags) for f in sorted(JAGUAR_EXPECTED_FLAGS | JAGUAR_ABSENT_FLAGS)},
        "jaguar_like": jaguar_like,
        "machine": platform.machine(),
    }


# ---------------------------------------------------------------- memory

def meminfo() -> dict[str, int]:
    """Return /proc/meminfo in KiB."""
    out = {}
    text = _read(paths.procfs("meminfo"), "") or ""
    for line in text.splitlines():
        m = re.match(r"^(\S+):\s+(\d+)", line)
        if m:
            out[m.group(1)] = int(m.group(2))
    return out


def gpu_memory(card: str | None = None) -> dict:
    """amdgpu memory counters in bytes (None when the driver does not expose them)."""
    card = card or primary_card()
    if not card:
        return {}
    dev = paths.sysfs("class/drm", card, "device")
    keys = ["mem_info_vram_total", "mem_info_vram_used", "mem_info_vis_vram_total",
            "mem_info_vis_vram_used", "mem_info_gtt_total", "mem_info_gtt_used"]
    return {k: _read_int(dev / k) for k in keys}


# ---------------------------------------------------------------- DRM

def primary_card() -> str | None:
    base = paths.sysfs("class/drm")
    if not base.is_dir():
        return None
    for d in sorted(base.iterdir()):
        if re.fullmatch(r"card\d+", d.name):
            return d.name
    return None


def drm_info() -> dict:
    base = paths.sysfs("class/drm")
    out = {"cards": [], "connectors": [], "render_nodes": []}
    if not base.is_dir():
        return out
    for d in sorted(base.iterdir()):
        name = d.name
        if re.fullmatch(r"card\d+", name):
            drv = None
            try:
                drv = os.path.basename(os.readlink(d / "device" / "driver"))
            except OSError:
                pass
            out["cards"].append({"name": name, "driver": drv})
        elif name.startswith("renderD"):
            out["render_nodes"].append(name)
        elif re.fullmatch(r"card\d+-.+", name):
            modes = (_read(d / "modes", "") or "").splitlines()
            out["connectors"].append({
                "name": name, "status": _read(d / "status"),
                "enabled": _read(d / "enabled"), "dpms": _read(d / "dpms"),
                "modes": modes[:8],
                "edid_bytes": _edid_size(d / "edid"),
            })
    return out


def _edid_size(p: Path) -> int | None:
    try:
        return len(p.read_bytes())
    except OSError:
        return None


# ---------------------------------------------------------------- sensors

def sensors() -> dict:
    """Only sensors the kernel actually exposes. No synthesised values."""
    base = paths.sysfs("class/hwmon")
    temps, fans, pwm = [], [], []
    if base.is_dir():
        for h in sorted(base.iterdir()):
            name = _read(h / "name", h.name)
            for f in sorted(h.glob("temp*_input")):
                v = _read_int(f)
                if v is not None:
                    label = _read(f.with_name(f.name.replace("_input", "_label")))
                    temps.append({"chip": name, "sensor": label or f.name, "celsius": v / 1000.0})
            for f in sorted(h.glob("fan*_input")):
                v = _read_int(f)
                if v is not None:
                    fans.append({"chip": name, "sensor": f.name, "rpm": v})
            for f in sorted(h.glob("pwm[0-9]")):
                v = _read_int(f)
                if v is not None:
                    pwm.append({"chip": name, "sensor": f.name, "value": v,
                                "writable": os.access(f, os.W_OK)})
    return {"temperatures": temps, "fans": fans, "pwm": pwm,
            "fan_control": "UNSUPPORTED" if not pwm else "UNVERIFIED"}


# ---------------------------------------------------------------- network

def network() -> list[dict]:
    base = paths.sysfs("class/net")
    out = []
    if not base.is_dir():
        return out
    for n in sorted(base.iterdir()):
        if n.name == "lo":
            continue
        driver = None
        try:
            driver = os.path.basename(os.readlink(n / "device" / "driver"))
        except OSError:
            pass
        out.append({
            "name": n.name, "operstate": _read(n / "operstate"),
            "carrier": _read_int(n / "carrier"),
            "wireless": (n / "wireless").exists() or (n / "phy80211").exists(),
            "driver": driver,
        })
    return out


# ---------------------------------------------------------------- storage

def _mountinfo_root() -> dict | None:
    text = _read(paths.procfs("self/mountinfo"), "") or ""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) > 9 and parts[4] == "/":
            sep = parts.index("-")
            return {"majmin": parts[2], "fstype": parts[sep + 1], "source": parts[sep + 2],
                    "options": parts[5]}
    return None


def root_storage() -> dict:
    info = _mountinfo_root() or {}
    out = {"fstype": info.get("fstype"), "source": info.get("source"),
           "transport": None, "drivers": [], "behind_usb_hub": None}
    majmin = info.get("majmin")
    if not majmin:
        return out
    dev_link = paths.sysfs("dev/block", majmin)
    try:
        real = Path(os.path.realpath(dev_link))
    except OSError:
        return out
    drivers = []
    cur = real
    for _ in range(16):
        drv = cur / "driver"
        if drv.is_symlink():
            drivers.append(os.path.basename(os.readlink(drv)))
        if cur.parent == cur:
            break
        cur = cur.parent
    out["drivers"] = drivers
    s = str(real)
    if "/usb" in s:
        out["transport"] = "usb"
        # USB port paths look like 2-1 (root port) or 2-1.3 (behind a hub).
        ports = re.findall(r"/(\d+-[\d.]+)(?=/|$)", s)
        out["behind_usb_hub"] = any("." in p for p in ports)
        out["usb_attach"] = "uas" if "uas" in drivers else (
            "usb-storage" if "usb-storage" in drivers else None)
    elif "ahci" in drivers:
        out["transport"] = "sata"
    elif any(d.startswith("sdhci") for d in drivers):
        out["transport"] = "sdio"
    return out


# ---------------------------------------------------------------- kernel

INTERESTING_KCONFIG = (
    "CONFIG_X86_PS4", "CONFIG_DRM_AMDGPU", "CONFIG_DRM_AMDGPU_CIK", "CONFIG_HSA_AMD",
    "CONFIG_PSI", "CONFIG_MEMCG", "CONFIG_CGROUP_BPF", "CONFIG_SUSPEND",
    "CONFIG_HIBERNATION", "CONFIG_KEXEC", "CONFIG_EROFS_FS", "CONFIG_OVERLAY_FS",
    "CONFIG_BTRFS_FS", "CONFIG_ZRAM", "CONFIG_USB_UAS", "CONFIG_SENSORS_K10TEMP",
    "CONFIG_EXTRA_FIRMWARE", "CONFIG_DEBUG_FS",
)


def kernel_config() -> dict[str, str]:
    p = paths.procfs("config.gz")
    try:
        raw = gzip.decompress(p.read_bytes()).decode("utf-8", "replace")
    except (OSError, EOFError):
        return {}
    out = {}
    for line in raw.splitlines():
        m = re.match(r"^(CONFIG_\w+)=(.*)$", line)
        if m and m.group(1) in INTERESTING_KCONFIG:
            out[m.group(1)] = m.group(2)
        m = re.match(r"^# (CONFIG_\w+) is not set$", line)
        if m and m.group(1) in INTERESTING_KCONFIG:
            out[m.group(1)] = "n"
    return out


def kernel_info() -> dict:
    cmdline = _read(paths.procfs("cmdline"), "") or ""
    return {
        "release": _read(paths.procfs("sys/kernel/osrelease")) or platform.release(),
        "cmdline": cmdline,
        "orbis_mode_arg": next((t.split("=", 1)[1] for t in cmdline.split()
                                if t.startswith("orbis.mode=")), None),
        "config": kernel_config(),
    }


_CPU_PREV: dict = {}


def cpu_load() -> dict:
    """Load average plus utilisation since the previous call (first call: since boot)."""
    out = {}
    la = _read(paths.procfs("loadavg"))
    if la:
        parts = la.split()
        out["loadavg"] = [float(x) for x in parts[:3]]
    st = _read(paths.procfs("stat"), "") or ""
    for line in st.splitlines():
        if line.startswith("cpu "):
            vals = [int(x) for x in line.split()[1:]]
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            total = sum(vals[:8])
            pt, pi = _CPU_PREV.get("total", 0), _CPU_PREV.get("idle", 0)
            dt, di = total - pt, idle - pi
            if dt > 0:
                out["utilisation_pct"] = round(100.0 * (1 - di / dt), 1)
            _CPU_PREV.update(total=total, idle=idle)
            break
    return out


def inventory() -> dict:
    plat = detect_platform()
    mem = meminfo()
    return {
        "platform": plat,
        "cpu": cpu_info(),
        "memory_kib": {k: mem.get(k) for k in
                       ("MemTotal", "MemAvailable", "MemFree", "SwapTotal", "SwapFree",
                        "Shmem", "Cached")},
        "gpu_memory": gpu_memory(),
        "drm": drm_info(),
        "sensors": sensors(),
        "network": network(),
        "root_storage": root_storage(),
        "kernel": kernel_info(),
    }
