"""orbis-ai-launch: ExecStart helper for orbis-llama.service.

Computes the launch plan from *measured* state at service start (memory,
Vulkan heaps, gate receipt), records it to /run/orbis/llama-launch.json for
the dashboard (so the active backend shown is the one actually launched),
then exec()s llama-server. The Python process is replaced, so it costs no
memory while the model runs.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import ai, config, gpu, hardware, paths

CPU_BIN = os.environ.get("ORBIS_LLAMA_CPU_BIN", "/usr/bin/llama-server")
VK_BIN = os.environ.get("ORBIS_LLAMA_VK_BIN", "/usr/lib/orbis/llama/vulkan/llama-server")


def _vk_heap_bytes() -> int | None:
    pr = gpu.run_probe(["--vk"], timeout=30)
    devs = (pr.get("vulkan") or {}).get("devices") or []
    hw = [d for d in devs if gpu.classify_vk_device(d) == "hardware"]
    if not hw:
        return None
    heaps = [h for h in hw[0].get("heaps", []) if h.get("device_local")]
    if not heaps:
        return None
    return max(int(h.get("budget") or h.get("size") or 0) for h in heaps)


def main(argv=None) -> int:
    cfg = config.load_config()
    a = cfg.get("ai", {})
    model = a.get("model")
    if not model:
        print("orbis-ai-launch: no model configured; set one with `orbis ai set model <file.gguf>`", file=sys.stderr)
        return 78  # EX_CONFIG: systemd will not restart-loop on this (RestartPreventExitStatus)
    mp = Path(model) if "/" in model else ai.models_dir(cfg) / model
    try:
        mi = ai.ModelInfo.from_file(mp)
    except (OSError, ai.GGUFError) as exc:
        print(f"orbis-ai-launch: cannot read model {mp}: {exc}", file=sys.stderr)
        return 78
    meta = {}
    mf = mp.with_suffix(".orbis.json")
    if mf.exists():
        try:
            meta = json.loads(mf.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    build = None
    bf = paths.share_dir() / "llama-build"
    if bf.exists():
        build = bf.read_text().strip()
    requested = a.get("backend", "cpu")
    heap = _vk_heap_bytes() if requested in ("vulkan", "auto") else None
    gate = gpu.load_gate_receipt()
    ident = ai.current_identity(build, meta.get("sha256"))
    lp = ai.plan_launch(cfg, mi, hardware.meminfo().get("MemAvailable", 0), os.cpu_count() or 1,
                        heap, gate, ident, requested)
    binary = VK_BIN if lp.active_backend == "vulkan" else CPU_BIN
    if lp.active_backend == "vulkan" and not os.path.exists(VK_BIN):
        lp.active_backend, lp.backend_reason = "cpu", "Vulkan build of llama-server not installed; running on CPU"
        binary = CPU_BIN
        lp.argv = [x for x in lp.argv]
        i = lp.argv.index("--n-gpu-layers")
        lp.argv[i + 1] = "0"
        lp.argv += ["--device", "none"]
    rec = lp.to_dict()
    rec["binary"] = binary
    rec["identity"] = ident
    out_dir = Path(os.environ.get("RUNTIME_DIRECTORY", "").split(":")[0] or paths.p("run/orbis-ai"))
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "llama-launch.json").write_text(json.dumps(rec, indent=2))
    except OSError:
        pass
    print(f"orbis-ai-launch: backend={lp.active_backend} ({lp.backend_reason}); ctx={lp.n_ctx} "
          f"ngl={lp.n_gpu_layers} threads={lp.threads}", file=sys.stderr, flush=True)
    for e in lp.errors:
        print(f"orbis-ai-launch: WARNING: {e}", file=sys.stderr, flush=True)
    argv = [binary] + lp.argv[1:]
    os.execv(binary, argv)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
