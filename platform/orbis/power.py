"""Power/performance presets and per-profile sysctls.

Only knobs the running kernel actually exposes are touched. On the PS4
kernels inspected, cpufreq policy is not qualified (linux-psx: "cpufreq
policy ... remain[s] open") and fan control is not exposed at all, so a
preset may legitimately be reported as UNSUPPORTED.
"""

from __future__ import annotations

from . import paths

PRESETS = {
    # governor preference order; first one the kernel offers wins.
    "quiet": ["powersave", "conservative", "schedutil"],
    "balanced": ["schedutil", "ondemand", "conservative"],
    "performance": ["performance", "schedutil"],
}

# Only these sysctl keys may be set from profiles.
SYSCTL_ALLOW = {
    "vm.swappiness", "vm.vfs_cache_pressure", "vm.dirty_background_ratio",
    "vm.dirty_ratio", "vm.min_free_kbytes", "vm.overcommit_memory",
    "vm.watermark_scale_factor", "kernel.sched_autogroup_enabled",
}


def cpufreq_policies() -> list:
    base = paths.sysfs("devices/system/cpu/cpufreq")
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.name.startswith("policy"))


def apply_preset(preset: str, dry_run: bool = False) -> dict:
    wanted = PRESETS.get(preset)
    if wanted is None:
        return {"preset": preset, "state": "FAILED", "detail": "unknown preset"}
    pols = cpufreq_policies()
    if not pols:
        return {"preset": preset, "state": "UNSUPPORTED",
                "detail": "kernel exposes no cpufreq policies; CPU runs at firmware-set clocks"}
    changed = []
    for pol in pols:
        try:
            avail = (pol / "scaling_available_governors").read_text().split()
            cur = (pol / "scaling_governor").read_text().strip()
        except OSError as exc:
            return {"preset": preset, "state": "FAILED", "detail": str(exc)}
        gov = next((g for g in wanted if g in avail), None)
        if gov is None:
            return {"preset": preset, "state": "UNSUPPORTED",
                    "detail": f"none of {wanted} offered (available: {avail})"}
        if gov != cur:
            changed.append(f"{pol.name}: {cur} -> {gov}")
            if not dry_run:
                (pol / "scaling_governor").write_text(gov)
    return {"preset": preset, "state": "APPLIED", "changes": changed}


def apply_sysctls(values: dict, dry_run: bool = False) -> list[dict]:
    out = []
    for key, val in sorted(values.items()):
        if key not in SYSCTL_ALLOW:
            out.append({"key": key, "state": "REJECTED", "detail": "not in allow-list"})
            continue
        f = paths.procfs("sys", *key.split("."))
        try:
            cur = f.read_text().strip()
        except OSError:
            out.append({"key": key, "state": "UNSUPPORTED"})
            continue
        if cur == str(val):
            out.append({"key": key, "state": "UNCHANGED", "value": cur})
            continue
        if not dry_run:
            try:
                f.write_text(str(val))
            except OSError as exc:
                out.append({"key": key, "state": "FAILED", "detail": str(exc)})
                continue
        out.append({"key": key, "state": "APPLIED", "from": cur, "to": str(val)})
    return out
