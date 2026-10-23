"""Machine-readable hardware capability matrix.

Static knowledge (manifests/capability-matrix.json, installed to
/usr/share/orbis/capability-matrix.json) is combined with the console's
detected qualification group and with runtime checks from this boot.

Rules:
  * Anything without evidence for *this* group reads UNVERIFIED.
  * A result from another group never propagates (no Baikal->Aeolia leakage).
  * An `orbis_policy` (UNSUPPORTED/BLOCKED) overrides upstream optimism: it is
    what Orbis will actually do.
  * Runtime overlays (PASSED_ON_THIS_DEVICE / FAILED) are shown alongside, not
    instead of, the static state.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import hardware, paths


def matrix_path() -> Path:
    for cand in (paths.share_dir() / "capability-matrix.json",
                 Path(__file__).resolve().parents[2] / "manifests" / "capability-matrix.json"):
        if cand.exists():
            return cand
    raise FileNotFoundError("capability-matrix.json not found")


def load_matrix() -> dict:
    return json.loads(matrix_path().read_text())


def group_key(platform: dict, declared_model: str | None = None) -> tuple[str | None, list[str]]:
    """Return (group, notes). None when the silicon + declaration cannot decide."""
    notes = []
    sb = platform.get("southbridge")
    gpu = (platform.get("gpu") or {}).get("family")
    declared = hardware.model_group_from_cuh(declared_model)
    cg = platform.get("console_group", {})
    if not platform.get("is_ps4"):
        return None, ["not running on PS4 hardware (no Sony southbridge + Liverpool/Gladius GPU)"]
    if declared and cg.get("candidates") and declared not in cg["candidates"]:
        notes.append(f"declared model {declared_model} ({declared}) contradicts detected "
                     f"{sb}/{gpu} ({cg.get('group')}); using detected silicon")
        declared = None
    form = cg.get("group") if cg.get("certain") else declared
    if form is None:
        notes.append(f"{sb} southbridge appears in {', '.join(cg.get('candidates', []))} consoles; "
                     "set hardware.model (e.g. CUH-1215A) in /etc/orbis/orbis.toml")
        return None, notes
    return f"{form}-{sb}", notes


def evaluate(platform: dict, declared_model: str | None, runtime: dict[str, dict] | None = None,
             matrix: dict | None = None) -> dict:
    m = matrix or load_matrix()
    group, notes = group_key(platform, declared_model)
    runtime = runtime or {}
    rows = []
    for cap in m["capabilities"]:
        by = cap.get("by_group", {})
        ent = by.get(group) if group else None
        static = dict(ent) if ent else {"state": m.get("default_state", "UNVERIFIED")}
        if group is None:
            static = {"state": "UNVERIFIED", "evidence": "qualification group unknown"}
        row = {"id": cap["id"], "title": cap["title"], "category": cap.get("category"),
               "static": static, "orbis_policy": cap.get("orbis_policy"),
               "notes": cap.get("notes")}
        rt = runtime.get(cap.get("runtime_check") or cap["id"])
        if rt:
            row["runtime"] = rt
        # Effective state: policy > runtime overlay > static.
        eff = static["state"]
        if rt and rt.get("state") in ("PASSED_ON_THIS_DEVICE", "FAILED"):
            eff = rt["state"]
        if cap.get("orbis_policy") in ("UNSUPPORTED", "BLOCKED"):
            if not (rt and rt.get("state") == "PASSED_ON_THIS_DEVICE"):
                eff = cap["orbis_policy"]
        row["effective"] = eff
        rows.append(row)
    return {"group": group, "notes": notes, "as_of": m.get("as_of"), "capabilities": rows}


def runtime_overlays(inv: dict, gpu_tiers: dict | None = None) -> dict:
    out = dict(gpu_tiers or {})
    s = inv.get("sensors") or {}
    if s.get("temperatures"):
        t = s["temperatures"][0]
        out["sensors.temperature"] = {"state": "PASSED_ON_THIS_DEVICE",
                                      "evidence": f"{t['chip']} {t['sensor']} = {t['celsius']:.1f} °C"}
    else:
        out["sensors.temperature"] = {"state": "UNVERIFIED", "evidence": "no hwmon temperature exposed"}
    if s.get("fans") or s.get("pwm"):
        out["sensors.fan_control"] = {"state": "UNVERIFIED", "evidence": "fan hwmon present but unqualified"}
    else:
        out["sensors.fan_control"] = {"state": "UNSUPPORTED", "evidence": "no fan/pwm hwmon exposed"}
    return out
