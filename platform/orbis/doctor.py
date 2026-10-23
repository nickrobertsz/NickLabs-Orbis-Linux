"""`orbis doctor`: one report for support and bug reports (text or JSON).

Each finding has a severity (OK, INFO, WARN, FAIL), a check id and a short
explanation; JSON output also carries the raw evidence. No MAC/IP addresses,
hostnames or user names are collected.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field

from . import (MODES, __version__, ai, bootfs, bzimage, capabilities, config, gpu, hardware,
               isa_audit, memory, paths)

SEVERITY_ORDER = {"FAIL": 3, "WARN": 2, "INFO": 1, "OK": 0}


@dataclass
class Finding:
    check: str
    severity: str
    message: str
    evidence: dict = field(default_factory=dict)


@dataclass
class Report:
    version: str
    time: str
    findings: list[Finding] = field(default_factory=list)
    inventory: dict = field(default_factory=dict)
    gpu_tiers: dict = field(default_factory=dict)
    capabilities: dict = field(default_factory=dict)

    def add(self, check, severity, message, **evidence):
        self.findings.append(Finding(check, severity, message, evidence))

    @property
    def worst(self) -> str:
        return max((f.severity for f in self.findings), key=lambda s: SEVERITY_ORDER[s], default="OK")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["worst"] = self.worst
        return d


def _pacman_q(pkgs: list[str]) -> dict[str, str | None]:
    out = {p: None for p in pkgs}
    if not shutil.which("pacman"):
        return out
    r = subprocess.run(["pacman", "-Q", *pkgs], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2:
            out[parts[0]] = parts[1]
    return out


def run(cfg: dict | None = None, deep: bool = False, probe: bool = True) -> Report:
    cfg = cfg if cfg is not None else config.load_config()
    rep = Report(version=__version__, time=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    inv = hardware.inventory()
    rep.inventory = inv
    plat = inv["platform"]

    # ---- platform
    declared = cfg.get("hardware", {}).get("model")
    if plat["is_ps4"]:
        rep.add("platform", "OK", f"PS4 detected: {plat['southbridge']} southbridge, "
                f"{plat['gpu']['name']} ({plat['gpu']['pci_id']})")
    else:
        rep.add("platform", "WARN", "not running on recognised PS4 hardware; hardware checks are informational")
    group, notes = capabilities.group_key(plat, declared)
    for n in notes:
        rep.add("platform.group", "WARN", n)
    if group:
        rep.add("platform.group", "OK", f"qualification group {group}" + (f" (declared {declared})" if declared else ""))

    # ---- CPU / ISA
    cpu = inv["cpu"]
    if plat["is_ps4"] and not cpu["jaguar_like"]:
        rep.add("cpu", "WARN", f"CPU flags do not look like Jaguar: {cpu['has']}")
    else:
        rep.add("cpu", "OK", f"{cpu['model']} level={cpu['isa_level']} ({cpu['logical_cpus']} CPUs)")
    pa = isa_audit.pacman_audit()
    if pa.get("error"):
        rep.add("isa.pacman", "INFO", pa["error"])
    elif pa.get("ok"):
        rep.add("isa.pacman", "OK", f"pacman Architecture={pa.get('architecture') or ['auto']}; no v3/v4 repos or packages")
    else:
        rep.add("isa.pacman", "FAIL", "x86-64-v3/v4 repositories or packages present: these SIGILL on Jaguar",
                bad_repos=pa.get("bad_repos"), foreign=pa.get("foreign_arch_packages")[:20])

    # ---- kernel
    k = inv["kernel"]
    kc = k.get("config", {})
    mods = paths.p("usr/lib/modules", k["release"] or "")
    if not mods.is_dir():
        rep.add("kernel.modules", "FAIL" if plat["is_ps4"] else "INFO",
                f"no /usr/lib/modules/{k['release']}: kernel and module tree do not match")
    else:
        rep.add("kernel.modules", "OK", f"modules for {k['release']} present")
    if kc:
        if kc.get("CONFIG_X86_PS4") != "y" and plat["is_ps4"]:
            rep.add("kernel.config", "FAIL", "running kernel lacks CONFIG_X86_PS4")
        if kc.get("CONFIG_PSI") in ("n", None):
            rep.add("kernel.psi", "INFO", "PSI disabled: memory alerts use MemAvailable thresholds")
        if kc.get("CONFIG_SUSPEND") == "y":
            rep.add("kernel.suspend", "WARN", "kernel offers suspend; it is not qualified on PS4 (Orbis masks sleep targets)")
    if k.get("orbis_mode_arg") and k["orbis_mode_arg"] not in MODES:
        rep.add("kernel.cmdline", "WARN", f"unknown orbis.mode={k['orbis_mode_arg']}")

    # ---- boot partition
    try:
        bp = bootfs.locate(cfg, mount=False)
    except bootfs.BootFsError as exc:
        bp = None
        rep.add("boot", "WARN", str(exc))
    cands = bootfs.candidate_loader_partitions()
    if len(cands) > 1:
        rep.add("boot.ambiguous", "WARN", "several FAT partitions hold a bzImage; the loader takes the first USB device "
                "it finds", mounts=cands)
    if bp:
        st = bp.status()
        rep.add("boot", "OK", f"boot partition {st['path']}: next mode={st['next_mode']}, "
                f"next VRAM={st['next_vram_mb']} MB", **st)
        try:
            br = bzimage.inspect(bp.root / bootfs.KERNEL, deep=deep)
            sev = "OK" if br.ok and not br.warnings else ("WARN" if br.ok else "FAIL")
            rep.add("boot.kernel", sev, f"bzImage protocol {br.to_dict()['protocol']}, {br.kernel_version}",
                    errors=br.errors, warnings=br.warnings)
        except OSError as exc:
            rep.add("boot.kernel", "WARN", f"cannot read bzImage: {exc}")
    else:
        rep.add("boot", "INFO", "PS4 boot partition not mounted; mode/VRAM changes cannot be staged for next boot")

    # ---- memory
    snap = memory.snapshot(include_processes=True)
    pr = memory.mem_pressure_level(cfg, snap)
    gm = inv.get("gpu_memory") or {}
    vram = gm.get("mem_info_vram_total")
    rep.add("memory", {"ok": "OK", "warning": "WARN", "critical": "FAIL"}[pr["level"]],
            f"MemAvailable {memory.human_kib(snap['mem_available_kib'])} of {memory.human_kib(snap['mem_total_kib'])}; "
            f"VRAM carve-out {(vram >> 20) if vram else '?'} MiB",
            top=snap.get("top", [])[:10], gpu=gm)

    # ---- graphics
    clean, events = gpu.graphics_state_clean()
    if not clean:
        rep.add("gpu.health", "FAIL", "GPU faults/resets logged since boot: " +
                ", ".join(f"{k}×{len(v)}" for k, v in events.items()),
                events={k: v[:5] for k, v in events.items()})
    else:
        rep.add("gpu.health", "OK", "no amdgpu VM faults, ring timeouts or resets logged since boot")
    probe_out = None
    if probe:
        args = ["--gl", "--vk"] + (["--compute"] if deep else [])
        probe_out = gpu.run_probe(args, timeout=120 if deep else 30)
        if probe_out.get("timeout"):
            rep.add("gpu.probe", "FAIL", probe_out["error"])
    tiers = gpu.summarize(probe_out, inv, gpu.load_gate_receipt())
    rep.gpu_tiers = tiers
    for tier, v in tiers.items():
        sev = {"PASSED_ON_THIS_DEVICE": "OK", "FAILED": "FAIL"}.get(v["state"], "INFO")
        if tier == "gpu.gl.hw" and v["state"] == "FAILED":
            v_msg = "SOFTWARE RENDERING: " + v["evidence"]
        else:
            v_msg = v["evidence"]
        rep.add(tier, sev, f"{v['state']}: {v_msg}")
    pk = _pacman_q(["mesa-orbis-ps4", "lib32-mesa-orbis-ps4", "xf86-video-amdgpu-orbis-ps4"])
    if shutil.which("pacman"):
        if pk.get("mesa-orbis-ps4") is None:
            rep.add("graphics.packages", "FAIL", "mesa-orbis-ps4 is not installed", packages=pk)
        elif pk.get("lib32-mesa-orbis-ps4") and pk["lib32-mesa-orbis-ps4"] != pk["mesa-orbis-ps4"]:
            rep.add("graphics.packages", "WARN", "32-bit and 64-bit Mesa versions differ", packages=pk)
        else:
            rep.add("graphics.packages", "OK", f"mesa-orbis-ps4 {pk['mesa-orbis-ps4']}", packages=pk)

    # ---- storage / network / sensors
    rs = inv["root_storage"]
    if rs.get("transport") == "usb":
        if rs.get("behind_usb_hub"):
            rep.add("storage.root", "WARN", "root filesystem is behind a USB hub; connect the SSD directly", **rs)
        elif rs.get("usb_attach") == "usb-storage":
            rep.add("storage.root", "WARN", "root SSD uses usb-storage (BOT), not UAS; expect lower throughput", **rs)
        else:
            rep.add("storage.root", "OK", f"root on USB ({rs.get('usb_attach')}), {rs.get('fstype')}", **rs)
    else:
        rep.add("storage.root", "INFO", f"root transport {rs.get('transport')}, {rs.get('fstype')}", **rs)
    nets = inv["network"]
    up = [n for n in nets if n["operstate"] == "up"]
    rep.add("network", "OK" if up else "WARN",
            f"{len(up)} interface(s) up: " + ", ".join(f"{n['name']}({n['driver']})" for n in up) if up
            else "no network interface is up; remote administration unavailable")
    s = inv["sensors"]
    rep.add("sensors", "OK" if s["temperatures"] else "INFO",
            (f"{len(s['temperatures'])} temperature sensor(s)" if s["temperatures"] else "no temperature sensors exposed")
            + f"; fan control {s['fan_control']}")

    # ---- AI
    models = ai.list_models(cfg)
    rep.add("ai.models", "OK" if models else "INFO", f"{len(models)} model(s) in {ai.models_dir(cfg)}")
    for b in ("/usr/bin/llama-server", "/usr/lib/orbis/llama/vulkan/llama-server"):
        bp_ = paths.p(b)
        if bp_.exists() and deep:
            res = isa_audit.audit_file(bp_)
            sev = "OK" if res["verdict"] in ("safe", "safe-if-dispatched") else "FAIL"
            rep.add("ai.isa", sev, f"{b}: {res['verdict']}")

    rep.capabilities = capabilities.evaluate(plat, declared, capabilities.runtime_overlays(inv, tiers))
    return rep


def render_text(rep: Report) -> str:
    lines = [f"NICKLABS / PS4 ORBIS LINUX — doctor v{rep.version} — {rep.time}", ""]
    for f in sorted(rep.findings, key=lambda f: -SEVERITY_ORDER[f.severity]):
        lines.append(f"[{f.severity:4}] {f.check:22} {f.message}")
    lines.append("")
    caps = rep.capabilities
    lines.append(f"Capability matrix (group: {caps.get('group') or 'unknown'}):")
    for c in caps.get("capabilities", []):
        lines.append(f"  {c['effective']:22} {c['id']:32} {c['title']}")
    lines.append("")
    lines.append(f"Overall: {rep.worst}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--deep", action="store_true", help="also run Vulkan compute suite and binary ISA audit")
    ap.add_argument("--no-probe", action="store_true")
    a = ap.parse_args(argv)
    rep = run(deep=a.deep, probe=not a.no_probe)
    print(json.dumps(rep.to_dict(), indent=2, default=str) if a.json else render_text(rep))
    return {"OK": 0, "INFO": 0, "WARN": 1, "FAIL": 2}[rep.worst]


if __name__ == "__main__":
    raise SystemExit(main())
