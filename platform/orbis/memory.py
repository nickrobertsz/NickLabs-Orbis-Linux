"""Memory measurement and post-transition residue detection.

Rule from the brief: never claim a stopped service freed memory until it is
measured. Every transition takes a snapshot before and after; the difference
is what Orbis reports.

Sources (all read-only):
  /proc/meminfo                     system totals
  /proc/<pid>/smaps_rollup          per-process PSS (needs root for others)
  /proc/<pid>/fdinfo/*              DRM client usage: drm-memory-{vram,gtt}
  /sys/fs/cgroup/**/memory.current  per-slice usage (orbis-*.slice)
  /sys/class/drm/card*/device/mem_info_*   amdgpu heap counters
"""

from __future__ import annotations

import fnmatch
import os
import re
import time
from pathlib import Path

from . import hardware, paths


def _pids() -> list[int]:
    base = paths.procfs("")
    out = []
    try:
        for e in os.scandir(base):
            if e.name.isdigit():
                out.append(int(e.name))
    except OSError:
        pass
    return sorted(out)


def _comm(pid: int) -> str | None:
    try:
        return paths.procfs(str(pid), "comm").read_text().strip()
    except OSError:
        return None


def _cmdline(pid: int) -> str:
    try:
        raw = paths.procfs(str(pid), "cmdline").read_bytes()
        return raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()
    except OSError:
        return ""


def _pss_kib(pid: int) -> int | None:
    try:
        text = paths.procfs(str(pid), "smaps_rollup").read_text()
    except OSError:
        try:
            text = paths.procfs(str(pid), "status").read_text()
            m = re.search(r"^VmRSS:\s+(\d+)", text, re.M)
            return int(m.group(1)) if m else None
        except OSError:
            return None
    m = re.search(r"^Pss:\s+(\d+)", text, re.M)
    return int(m.group(1)) if m else None


_UNIT_RE = re.compile(r"/([^/]+\.(?:service|scope))$")


def _unit_of(pid: int) -> str | None:
    try:
        text = paths.procfs(str(pid), "cgroup").read_text()
    except OSError:
        return None
    for line in text.splitlines():
        path = line.split(":", 2)[-1]
        m = _UNIT_RE.search(path)
        if m:
            return m.group(1)
    return None


def drm_clients() -> list[dict]:
    """Processes holding a DRM fd, with amdgpu per-client memory when exposed."""
    out = []
    for pid in _pids():
        fd_dir = paths.procfs(str(pid), "fd")
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        seen_clients: dict[str, dict] = {}
        holds = False
        for fd in fds:
            try:
                target = os.readlink(fd_dir / fd)
            except OSError:
                continue
            if not target.startswith("/dev/dri/"):
                continue
            holds = True
            try:
                info = paths.procfs(str(pid), "fdinfo", fd).read_text()
            except OSError:
                continue
            kv = dict(re.findall(r"^(drm-[\w-]+):\s+(.*)$", info, re.M))
            cid = kv.get("drm-client-id")
            if cid and cid not in seen_clients:
                seen_clients[cid] = kv
        if not holds:
            continue
        vram = gtt = 0
        for kv in seen_clients.values():
            vram += _parse_size(kv.get("drm-memory-vram") or kv.get("drm-resident-vram"))
            gtt += _parse_size(kv.get("drm-memory-gtt") or kv.get("drm-resident-gtt"))
        out.append({"pid": pid, "comm": _comm(pid), "unit": _unit_of(pid),
                    "clients": len(seen_clients), "vram_kib": vram, "gtt_kib": gtt})
    return out


def _parse_size(v: str | None) -> int:
    if not v:
        return 0
    m = re.match(r"(\d+)\s*(KiB|MiB|GiB|B)?", v)
    if not m:
        return 0
    n = int(m.group(1))
    unit = m.group(2) or "B"
    return {"B": n // 1024, "KiB": n, "MiB": n * 1024, "GiB": n * 1024 * 1024}[unit]


def slice_usage() -> dict[str, int]:
    """memory.current (bytes) of the Orbis per-mode slices."""
    out = {}
    base = paths.sysfs("fs/cgroup")
    for name in ("orbis.slice", "orbis-pc.slice", "orbis-gaming.slice", "orbis-ai.slice",
                 "user.slice", "system.slice"):
        for cand in (base / name, base / "orbis.slice" / name):
            f = cand / "memory.current"
            if f.exists():
                try:
                    out[name] = int(f.read_text().strip())
                except (OSError, ValueError):
                    pass
                break
    return out


def top_processes(limit: int = 25) -> list[dict]:
    rows = []
    for pid in _pids():
        pss = _pss_kib(pid)
        if not pss:
            continue
        rows.append({"pid": pid, "comm": _comm(pid), "pss_kib": pss, "unit": _unit_of(pid)})
    rows.sort(key=lambda r: r["pss_kib"], reverse=True)
    return rows[:limit]


def snapshot(include_processes: bool = True) -> dict:
    mi = hardware.meminfo()
    total = mi.get("MemTotal", 0)
    avail = mi.get("MemAvailable", 0)
    snap = {
        "time": time.time(),
        "mem_total_kib": total,
        "mem_available_kib": avail,
        "mem_used_kib": max(total - avail, 0),
        "swap_used_kib": max(mi.get("SwapTotal", 0) - mi.get("SwapFree", 0), 0),
        "gpu": hardware.gpu_memory(),
        "slices": slice_usage(),
    }
    if include_processes:
        snap["top"] = top_processes()
        snap["drm_clients"] = drm_clients()
    return snap


def diff(before: dict, after: dict) -> dict:
    def g(d, k):
        return d.get(k) or 0
    out = {
        "mem_available_delta_kib": g(after, "mem_available_kib") - g(before, "mem_available_kib"),
        "mem_used_delta_kib": g(after, "mem_used_kib") - g(before, "mem_used_kib"),
    }
    gb, ga = before.get("gpu") or {}, after.get("gpu") or {}
    for k in ("mem_info_vram_used", "mem_info_gtt_used"):
        if gb.get(k) is not None and ga.get(k) is not None:
            out[k + "_delta_bytes"] = ga[k] - gb[k]
    return out


def residue(patterns: list[str], ignore_pids: set[int] | None = None) -> list[dict]:
    """Processes whose comm/cmdline or owning unit match a must-stop pattern."""
    ignore_pids = ignore_pids or set()
    hits = []
    if not patterns:
        return hits
    for pid in _pids():
        if pid in ignore_pids:
            continue
        comm = _comm(pid) or ""
        unit = _unit_of(pid) or ""
        cmd = _cmdline(pid)
        exe = cmd.split(" ", 1)[0].rsplit("/", 1)[-1] if cmd else ""
        for pat in patterns:
            if (fnmatch.fnmatch(unit, pat) or fnmatch.fnmatch(comm, pat)
                    or (exe and fnmatch.fnmatch(exe, pat))):
                hits.append({"pid": pid, "comm": comm, "unit": unit or None, "pattern": pat})
                break
    return hits


def human_kib(kib: int | None) -> str:
    if kib is None:
        return "?"
    sign = "-" if kib < 0 else ""
    kib = abs(kib)
    if kib >= 1024 * 1024:
        return f"{sign}{kib / 1048576:.2f} GiB"
    if kib >= 1024:
        return f"{sign}{kib / 1024:.0f} MiB"
    return f"{sign}{kib} KiB"


def mem_pressure_level(cfg: dict, snap: dict | None = None) -> dict:
    """PSI is disabled in the PS4 kernels inspected (CONFIG_PSI unset), so the
    alert uses MemAvailable thresholds instead."""
    snap = snap or snapshot(include_processes=False)
    a = cfg.get("alerts", {})
    warn = int(a.get("mem_available_warn_mib", 400)) * 1024
    crit = int(a.get("mem_available_crit_mib", 200)) * 1024
    avail = snap["mem_available_kib"]
    level = "ok" if avail > warn else ("warning" if avail > crit else "critical")
    psi = Path(paths.procfs("pressure/memory"))
    return {"level": level, "mem_available_kib": avail, "warn_kib": warn, "crit_kib": crit,
            "psi_available": psi.exists()}
