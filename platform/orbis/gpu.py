"""GPU acceleration truth-telling.

A loaded amdgpu module, a Vulkan instance or an "AMD" device name is not
proof of hardware acceleration. Orbis reports separate tiers, each backed by
a specific measurement from `orbis-gpu-probe` (tools/gpu-probe), which runs in
a child process with a hard timeout so a GPU hang cannot wedge orbisd.

Tiers (capability ids):
  gpu.scanout          a connector is connected+enabled and DRM driver is amdgpu
  gpu.gl.hw            GL_RENDERER is a hardware AMD renderer (not llvmpipe etc.)
  gpu.vk.device        a non-CPU Vulkan physical device exists
  gpu.vk.compute       compute correctness suite passed on that device
  gpu.vk.inference     llama.cpp CPU-vs-Vulkan gate passed (inference harness receipt)
  gpu.vk.longrun       sustained inference gate passed
  gpu.video.decode     hardware decode test passed (not attempted by default)
"""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import hardware, paths

SOFTWARE_RENDERER_PATTERNS = [
    r"llvmpipe", r"softpipe", r"\bswrast\b", r"software rasterizer", r"lavapipe",
    r"swiftshader", r"\bSWR\b", r"Microsoft Basic Render", r"zink.*llvmpipe",
]
HW_AMD_PATTERNS = [r"\bAMD\b", r"\bRadeon\b", r"LIVERPOOL", r"GLADIUS", r"\bradeonsi\b",
                   r"\bRADV\b", r"BONAIRE"]

VK_PHYSICAL_DEVICE_TYPE = {0: "other", 1: "integrated", 2: "discrete", 3: "virtual", 4: "cpu"}
VK_DRIVER_ID = {1: "AMD_PROPRIETARY", 2: "AMD_OPEN_SOURCE", 3: "MESA_RADV", 10: "GOOGLE_SWIFTSHADER",
                13: "MESA_LLVMPIPE"}

FAULT_PATTERNS = [
    ("vm_fault", re.compile(r"amdgpu.*(VM fault|page fault|\[gfxhub\]|\[mmhub\]|GCVM_L2_PROTECTION_FAULT|VM_CONTEXT1_PROTECTION_FAULT)", re.I)),
    ("ring_timeout", re.compile(r"(amdgpu|drm).*(ring \S+ timeout|job timed? ?out|amdgpu_job_timedout)", re.I)),
    ("gpu_reset", re.compile(r"amdgpu.*(GPU reset|gpu_recover|GPU recovery|reset (succeeded|failed))", re.I)),
    ("ih_overflow", re.compile(r"amdgpu.*IH ring buffer overflow", re.I)),
    ("init_failure", re.compile(r"amdgpu.*(failed to (initialize|resume|init)|Fatal error during GPU init)", re.I)),
    ("uvd", re.compile(r"PS4_UVD_RUNTIME CLOSED|No more free UVD handles", re.I)),
]


def classify_renderer(renderer: str | None) -> str:
    if not renderer:
        return "unknown"
    for pat in SOFTWARE_RENDERER_PATTERNS:
        if re.search(pat, renderer, re.I):
            return "software"
    for pat in HW_AMD_PATTERNS:
        if re.search(pat, renderer, re.I):
            return "hardware"
    return "unknown"


def classify_vk_device(dev: dict) -> str:
    t = dev.get("device_type")
    if t == 4 or VK_DRIVER_ID.get(dev.get("driver_id")) in ("MESA_LLVMPIPE", "GOOGLE_SWIFTSHADER"):
        return "software"
    if dev.get("vendor_id") == 0x1002 and t in (1, 2):
        return "hardware"
    if t in (1, 2, 3):
        return "hardware-non-amd"
    return "unknown"


def probe_binary() -> str | None:
    for cand in (os.environ.get("ORBIS_GPU_PROBE"), "/usr/lib/orbis/orbis-gpu-probe",
                 shutil.which("orbis-gpu-probe")):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def run_probe(args: list[str], timeout: float = 60, env: dict | None = None) -> dict:
    """Run orbis-gpu-probe and return its JSON. Never raises on probe failure."""
    binary = probe_binary()
    if not binary:
        return {"ok": False, "error": "orbis-gpu-probe not installed"}
    e = dict(os.environ)
    e.update(env or {})
    t0 = time.monotonic()
    try:
        r = subprocess.run([binary, "--json", *args], capture_output=True, text=True,
                           timeout=timeout, env=e)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"probe timed out after {timeout:.0f}s (possible GPU hang)",
                "timeout": True}
    dt = time.monotonic() - t0
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "probe produced no JSON", "rc": r.returncode,
                "stderr": r.stderr[-2000:]}
    data.setdefault("ok", r.returncode == 0)
    data["rc"] = r.returncode
    data["elapsed_s"] = round(dt, 3)
    if r.returncode < 0:
        data["signal"] = -r.returncode
    return data


def read_kernel_log(max_bytes: int = 4 << 20) -> list[str]:
    """Read /dev/kmsg non-blockingly (root). Falls back to `dmesg` text."""
    lines: list[str] = []
    kmsg = paths.p("dev/kmsg")
    try:
        fd = os.open(str(kmsg), os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        fd = -1
    if fd >= 0 and kmsg == Path("/dev/kmsg"):
        total = 0
        try:
            while total < max_bytes:
                try:
                    rec = os.read(fd, 8192)
                except OSError as exc:
                    if exc.errno == errno.EAGAIN:
                        break
                    if exc.errno == errno.EPIPE:  # overwritten record; continue
                        continue
                    raise
                if not rec:
                    break
                total += len(rec)
                txt = rec.decode("utf-8", "replace")
                _, _, msg = txt.partition(";")
                lines.append(msg.rstrip("\n"))
        finally:
            os.close(fd)
        return lines
    if fd >= 0:
        os.close(fd)
    # Fixture or unprivileged fallback.
    f = paths.p("var/log/orbis/kmsg.fixture")
    if f.exists():
        return f.read_text().splitlines()
    if shutil.which("dmesg"):
        r = subprocess.run(["dmesg"], capture_output=True, text=True)
        return r.stdout.splitlines()
    return lines


def fault_events(lines: list[str] | None = None) -> dict[str, list[str]]:
    lines = read_kernel_log() if lines is None else lines
    out: dict[str, list[str]] = {}
    for line in lines:
        for kind, rx in FAULT_PATTERNS:
            if rx.search(line):
                out.setdefault(kind, []).append(line.strip()[:300])
    return out


def graphics_state_clean(lines: list[str] | None = None) -> tuple[bool, dict]:
    ev = fault_events(lines)
    unclean = {k: v for k, v in ev.items() if k in ("vm_fault", "ring_timeout", "gpu_reset",
                                                     "init_failure")}
    return (not unclean, unclean)


def summarize(probe: dict | None, inventory: dict | None = None,
              harness_gate: dict | None = None) -> dict:
    """Turn raw probe output into per-tier capability states."""
    inv = inventory or {}
    drm = inv.get("drm") or hardware.drm_info()
    tiers: dict[str, dict] = {}
    drivers = {c.get("driver") for c in drm.get("cards", []) if c.get("driver")}
    connected = [c for c in drm.get("connectors", []) if c.get("status") == "connected"]
    if "amdgpu" in drivers and connected:
        tiers["gpu.scanout"] = {"state": "PASSED_ON_THIS_DEVICE",
                                "evidence": f"amdgpu; connected: {', '.join(c['name'] for c in connected)}"}
    elif "amdgpu" in drivers:
        tiers["gpu.scanout"] = {"state": "UNVERIFIED", "evidence": "amdgpu bound, no connected connector (headless?)"}
    else:
        tiers["gpu.scanout"] = {"state": "FAILED" if drivers else "UNVERIFIED",
                                "evidence": f"DRM drivers: {sorted(d for d in drivers if d) or 'none'}"}
    probe = probe or {}
    gl = probe.get("gl") or {}
    if gl:
        kind = classify_renderer(gl.get("renderer"))
        tiers["gpu.gl.hw"] = {
            "state": {"hardware": "PASSED_ON_THIS_DEVICE", "software": "FAILED"}.get(kind, "UNVERIFIED"),
            "evidence": f"GL_RENDERER={gl.get('renderer')!r} ({kind})"}
    else:
        tiers["gpu.gl.hw"] = {"state": "UNVERIFIED", "evidence": probe.get("gl_error") or "GL not probed"}
    devs = (probe.get("vulkan") or {}).get("devices") or []
    hw = [d for d in devs if classify_vk_device(d) == "hardware"]
    if hw:
        d = hw[0]
        tiers["gpu.vk.device"] = {"state": "PASSED_ON_THIS_DEVICE",
                                  "evidence": f"{d.get('name')} type={VK_PHYSICAL_DEVICE_TYPE.get(d.get('device_type'))} "
                                              f"driver={d.get('driver_name')} {d.get('driver_info')}"}
    elif devs:
        tiers["gpu.vk.device"] = {"state": "FAILED",
                                  "evidence": "only software Vulkan devices: " +
                                              ", ".join(str(d.get("name")) for d in devs)}
    else:
        tiers["gpu.vk.device"] = {"state": "UNVERIFIED",
                                  "evidence": (probe.get("vulkan") or {}).get("error") or "Vulkan not probed"}
    comp = probe.get("compute") or {}
    if comp:
        target_hw = classify_vk_device(comp.get("device") or {}) == "hardware"
        npass, nfail = int(comp.get("passed_count", 0)), int(comp.get("failed_count", 0))
        passed = npass > 0 and nfail == 0 and not comp.get("fatal")
        if passed and target_hw:
            st = "PASSED_ON_THIS_DEVICE"
        elif passed:
            st = "UNVERIFIED"  # passed on a software device: says nothing about the GPU
        else:
            st = "FAILED"
        failed_names = [t["test"] for t in comp.get("tests", []) if not t.get("ok")]
        ev = (f"{npass} passed, {nfail} failed on {(comp.get('device') or {}).get('name')} "
              f"(queue family {comp.get('queue_family')})")
        if comp.get("fatal"):
            ev += f"; stopped: {comp.get('fatal_reason')}"
        if failed_names:
            ev += f"; failed: {', '.join(failed_names[:6])}"
        if passed and not target_hw:
            ev += "; device is a software rasterizer, so this says nothing about the PS4 GPU"
        tiers["gpu.vk.compute"] = {"state": st, "evidence": ev}
    else:
        tiers["gpu.vk.compute"] = {"state": "UNVERIFIED", "evidence": "compute suite not run"}
    gate = harness_gate or {}
    for tier, key in (("gpu.vk.inference", "inference_gate"), ("gpu.vk.longrun", "longrun_gate")):
        g = gate.get(key)
        if g:
            tiers[tier] = {"state": "PASSED_ON_THIS_DEVICE" if g.get("passed") else "FAILED",
                           "evidence": g.get("summary", "")}
        else:
            tiers[tier] = {"state": "UNVERIFIED", "evidence": "no inference-harness gate receipt"}
    tiers["gpu.video.decode"] = {"state": "UNVERIFIED",
                                 "evidence": "UVD is experimental upstream; Orbis ships software decode"}
    return tiers


def load_gate_receipt() -> dict:
    f = paths.state_dir() / "gpu-gate.json"
    try:
        return json.loads(f.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
