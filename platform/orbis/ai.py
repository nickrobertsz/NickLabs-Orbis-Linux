"""AI personality: model registry, safe-limit computation, backend admission.

Design rules (from the brief and from the PS4 GPU-inference record):
  * CPU is the default, always-available engine.
  * Vulkan offload is admitted only when an inference-harness gate receipt
    matching the *current* kernel, Mesa, llama.cpp build and model exists.
    Otherwise the request is refused *visibly* and the service runs on CPU
    with `active_backend = cpu` and a stated reason. No green GPU label while
    computing on the CPU.
  * Context, batch and offload are capped from measured memory, never from
    the model's advertised maximum context.

The GGUF reader below parses only the metadata header (stdlib only).
"""

from __future__ import annotations

import json
import os
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import hardware, paths

GGUF_MAGIC = b"GGUF"
_SCALAR = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?",
           10: "<Q", 11: "<q", 12: "<d"}
_KV_BYTES = {"f32": 4.0, "f16": 2.0, "bf16": 2.0, "q8_0": 34 / 32, "q5_1": 24 / 32,
             "q5_0": 22 / 32, "q4_1": 20 / 32, "q4_0": 18 / 32}


class GGUFError(Exception):
    pass


def _read_str(fh) -> str:
    (n,) = struct.unpack("<Q", fh.read(8))
    if n > 1 << 24:
        raise GGUFError("string length implausible")
    return fh.read(n).decode("utf-8", "replace")


def _read_value(fh, vtype: int, keep_arrays: bool):
    if vtype in _SCALAR:
        fmt = _SCALAR[vtype]
        return struct.unpack(fmt, fh.read(struct.calcsize(fmt)))[0]
    if vtype == 8:
        return _read_str(fh)
    if vtype == 9:
        (itype,) = struct.unpack("<I", fh.read(4))
        (n,) = struct.unpack("<Q", fh.read(8))
        if itype in _SCALAR and not keep_arrays:
            fh.seek(n * struct.calcsize(_SCALAR[itype]), os.SEEK_CUR)
            return {"array_of": itype, "len": n}
        items = []
        for _ in range(n):
            v = _read_value(fh, itype, keep_arrays)
            if keep_arrays:
                items.append(v)
        return items if keep_arrays else {"array_of": itype, "len": n}
    raise GGUFError(f"unknown GGUF value type {vtype}")


def read_gguf_metadata(path: str | Path) -> dict:
    with open(path, "rb") as fh:
        if fh.read(4) != GGUF_MAGIC:
            raise GGUFError("not a GGUF file")
        (version,) = struct.unpack("<I", fh.read(4))
        if version < 2:
            raise GGUFError(f"GGUF v{version} not supported")
        n_tensors, n_kv = struct.unpack("<QQ", fh.read(16))
        kv = {}
        for _ in range(n_kv):
            key = _read_str(fh)
            (vtype,) = struct.unpack("<I", fh.read(4))
            kv[key] = _read_value(fh, vtype, keep_arrays=False)
    kv["_gguf_version"] = version
    kv["_n_tensors"] = n_tensors
    return kv


@dataclass
class ModelInfo:
    file: str
    size_bytes: int
    arch: str | None
    name: str | None
    n_layer: int | None
    n_ctx_train: int | None
    n_embd: int | None
    n_head: int | None
    n_head_kv: int | None
    head_dim_k: int | None
    head_dim_v: int | None
    n_vocab: int | None
    file_type: int | None

    @classmethod
    def from_file(cls, path: str | Path) -> "ModelInfo":
        p = Path(path)
        md = read_gguf_metadata(p)
        arch = md.get("general.architecture")

        def g(k):
            return md.get(f"{arch}.{k}") if arch else None
        n_head = g("attention.head_count")
        n_head_kv = g("attention.head_count_kv") or n_head
        n_embd = g("embedding_length")
        hd = (n_embd // n_head) if (n_embd and n_head) else None
        vocab = None
        tok = md.get("tokenizer.ggml.tokens")
        if isinstance(tok, dict):
            vocab = tok.get("len")
        vocab = g("vocab_size") or vocab
        return cls(file=str(p), size_bytes=p.stat().st_size, arch=arch,
                   name=md.get("general.name"), n_layer=g("block_count"),
                   n_ctx_train=g("context_length"), n_embd=n_embd, n_head=n_head,
                   n_head_kv=n_head_kv, head_dim_k=g("attention.key_length") or hd,
                   head_dim_v=g("attention.value_length") or hd, n_vocab=vocab,
                   file_type=md.get("general.file_type"))

    def kv_cache_bytes(self, n_ctx: int, cache_type: str = "f16") -> int:
        if not (self.n_layer and self.n_head_kv and self.head_dim_k and self.head_dim_v):
            raise GGUFError("model metadata lacks attention geometry")
        per_tok = self.n_head_kv * (self.head_dim_k + self.head_dim_v)
        return int(self.n_layer * n_ctx * per_tok * _KV_BYTES[cache_type])

    def compute_buffer_bytes(self, n_ubatch: int) -> int:
        """Conservative estimate of llama.cpp's compute buffer (logits + activations)."""
        vocab = self.n_vocab or 32000
        embd = self.n_embd or 2048
        return int(n_ubatch * (vocab * 4 + embd * 4 * 24))


@dataclass
class LaunchPlan:
    model: str
    requested_backend: str
    active_backend: str
    backend_reason: str
    n_ctx: int
    n_batch: int
    n_ubatch: int
    n_gpu_layers: int
    threads: int
    threads_batch: int
    cache_type: str
    host: str
    port: int
    budget: dict = field(default_factory=dict)
    argv: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


THREAD_PRESETS = {"quiet": 0.5, "balanced": 0.75, "performance": 1.0}


def gate_matches(gate: dict, ident: dict) -> tuple[bool, str]:
    """A Vulkan gate receipt is valid only for the exact stack it was earned on."""
    if not gate:
        return False, "no inference-harness gate receipt on this system"
    g = gate.get("inference_gate") or {}
    if not g.get("passed"):
        return False, "last inference-harness gate did not pass"
    pins = gate.get("pins", {})
    for k in ("kernel_release", "mesa_version", "llama_build", "model_sha256"):
        if pins.get(k) and ident.get(k) and pins[k] != ident[k]:
            return False, f"gate receipt was earned with a different {k} ({pins[k]} != {ident[k]})"
    return True, f"gate passed at {g.get('time', '?')} up to prompt {g.get('max_prompt_tokens', '?')} tokens"


def plan_launch(cfg: dict, model: ModelInfo, mem_available_kib: int, ncpu: int,
                vk_heap_bytes: int | None, gate: dict, ident: dict,
                requested: str | None = None) -> LaunchPlan:
    a = cfg.get("ai", {})
    req = requested or a.get("backend", "cpu")
    cache_type = a.get("cache_type", "f16")
    headroom = int(a.get("headroom_mib", 512)) << 20
    n_ubatch = int(a.get("ubatch", 256))
    n_batch = int(a.get("batch", 512))
    max_prompt = int(a.get("max_prompt_tokens", 0))  # 0 = unlimited by gate
    frac = THREAD_PRESETS.get(a.get("power_preset", "balanced"), 0.75)
    threads = max(1, int(round(ncpu * frac)))
    lp = LaunchPlan(model=model.file, requested_backend=req, active_backend="cpu",
                    backend_reason="CPU is the default engine", n_ctx=0, n_batch=n_batch,
                    n_ubatch=n_ubatch, n_gpu_layers=0, threads=threads,
                    threads_batch=max(threads, ncpu if frac >= 0.75 else threads),
                    cache_type=cache_type, host=a.get("listen", "127.0.0.1"),
                    port=int(a.get("port", 8081)))

    if req in ("vulkan", "auto"):
        ok, why = gate_matches(gate, ident)
        if ok and vk_heap_bytes:
            lp.active_backend = "vulkan"
            lp.backend_reason = why
            gmax = (gate.get("inference_gate") or {}).get("max_prompt_tokens")
            if gmax:
                max_prompt = int(gmax)
                n_ubatch = min(n_ubatch, max_prompt)
                n_batch = min(n_batch, max_prompt)
                lp.n_ubatch, lp.n_batch = n_ubatch, n_batch
        else:
            lp.backend_reason = (f"Vulkan {'requested' if req == 'vulkan' else 'auto'} but not admitted: "
                                 f"{why if not ok else 'no Vulkan device heap reported'}; running on CPU")

    avail = mem_available_kib * 1024
    weights = model.size_bytes
    compute = model.compute_buffer_bytes(n_ubatch)
    want = int(a.get("ctx_size", 4096))
    cands = sorted({c for c in (want, 8192, 4096, 2048, 1024, 512) if c <= want}, reverse=True)
    if model.n_ctx_train:
        cands = [c for c in cands if c <= model.n_ctx_train] or [min(512, model.n_ctx_train)]
    chosen = None
    for c in cands:
        kv = model.kv_cache_bytes(c, cache_type)
        if weights + kv + compute + headroom <= avail:
            chosen = c
            break
    if chosen is None:
        lp.errors.append(
            f"model needs ≥{(weights + model.kv_cache_bytes(cands[-1], cache_type) + compute + headroom) >> 20} MiB "
            f"at ctx {cands[-1]} but only {avail >> 20} MiB is available")
        chosen = cands[-1]
    lp.n_ctx = chosen
    kv = model.kv_cache_bytes(chosen, cache_type)
    lp.budget = {"mem_available_mib": avail >> 20, "weights_mib": weights >> 20,
                 "kv_cache_mib": kv >> 20, "compute_est_mib": compute >> 20,
                 "headroom_mib": headroom >> 20}

    if lp.active_backend == "vulkan" and model.n_layer:
        per_layer = weights / (model.n_layer + 1)
        usable = int(vk_heap_bytes * 0.85) - kv - compute
        ngl = max(0, min(model.n_layer + 1, int(usable // per_layer))) if usable > 0 else 0
        lp.n_gpu_layers = ngl
        lp.budget["vk_heap_mib"] = vk_heap_bytes >> 20
        if ngl == 0:
            lp.active_backend = "cpu"
            lp.backend_reason = "Vulkan admitted but no layer fits the device heap; running on CPU"

    lp.argv = [
        "llama-server", "--model", model.file, "--ctx-size", str(lp.n_ctx),
        "--batch-size", str(lp.n_batch), "--ubatch-size", str(lp.n_ubatch),
        "--threads", str(lp.threads), "--threads-batch", str(lp.threads_batch),
        "--n-gpu-layers", str(lp.n_gpu_layers), "--cache-type-k", cache_type,
        "--cache-type-v", cache_type, "--host", lp.host, "--port", str(lp.port),
        "--metrics", "--no-webui", "--parallel", "1",
    ]
    if lp.active_backend == "cpu":
        lp.argv += ["--device", "none"]
    return lp


def models_dir(cfg: dict) -> Path:
    return paths.p(cfg.get("ai", {}).get("models_dir", "/var/lib/orbis/models"))


def list_models(cfg: dict) -> list[dict]:
    out = []
    d = models_dir(cfg)
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.gguf")):
        try:
            mi = ModelInfo.from_file(f)
            row = asdict(mi)
        except (GGUFError, OSError, struct.error) as exc:
            row = {"file": str(f), "error": str(exc)}
        meta = f.with_suffix(".orbis.json")
        if meta.exists():
            try:
                row["orbis"] = json.loads(meta.read_text())
            except (OSError, json.JSONDecodeError):
                pass
        out.append(row)
    return out


def current_identity(llama_build: str | None = None, model_sha256: str | None = None) -> dict:
    k = hardware.kernel_info()
    mesa = None
    try:
        mesa = (paths.p("usr/share/orbis/mesa-version").read_text().strip())
    except OSError:
        pass
    return {"kernel_release": k.get("release"), "mesa_version": mesa,
            "llama_build": llama_build, "model_sha256": model_sha256}
