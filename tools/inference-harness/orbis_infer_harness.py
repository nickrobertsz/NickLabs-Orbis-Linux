#!/usr/bin/env python3
"""orbis-infer-harness — CPU-vs-Vulkan llama.cpp correctness and fault-boundary harness.

Built for hardware where a failing step can take the whole machine down
(PS4 Liverpool + RADV: a long prefill faulted and recovery failed upstream).

Principles
  * Pin everything: model sha256, llama.cpp builds, kernel, Mesa, RADV env.
  * The CPU build is the reference. Vulkan output is compared to it on the
    same tokens: logits (KL divergence, top-1 agreement via llama-perplexity),
    intermediate tensors (llama-eval-callback sums), and generated tokens
    (llama-server greedy completions).
  * Journal-first: each step is written and fsync()ed as "started" before the
    GPU is touched, so after a hard hang + power cycle `resume` marks that step
    HANG_OR_RESET_INFERRED and continues. Optional UDP mirroring sends journal
    lines and live kernel messages to another machine while the step runs.
  * Classify, don't guess: PASS, NUMERIC_MISMATCH, OUT_OF_MEMORY,
    GPU_PAGE_FAULT, RING_TIMEOUT, GPU_RESET, DEVICE_LOST, CRASH, TIMEOUT,
    HANG_OR_RESET_INFERRED, SETUP_ERROR. Multiple kernel/userspace signals are
    all recorded; the class is the most specific one observed.

Subcommands
  run      execute (or continue) a campaign from a JSON config
  resume   same as run, after a crash/power cycle
  report   summarise a campaign directory
  gate     evaluate the gate profile and optionally write the receipt Orbis reads
  receive  UDP listener for the journal/kmsg mirror (run on another machine)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import resource
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HARNESS_VERSION = "0.1.0"

FAULT_PATTERNS = [
    ("GPU_PAGE_FAULT", re.compile(r"amdgpu.*(VM fault|page fault|\[gfxhub\]|PROTECTION_FAULT)", re.I)),
    ("RING_TIMEOUT", re.compile(r"(amdgpu|drm).*(ring \S+ timeout|job timed? ?out|amdgpu_job_timedout)", re.I)),
    ("GPU_RESET", re.compile(r"amdgpu.*(GPU reset|gpu_recover|GPU recovery|reset (succeeded|failed))", re.I)),
    ("IH_OVERFLOW", re.compile(r"amdgpu.*IH ring buffer overflow", re.I)),
    ("OOM_KILL", re.compile(r"(Out of memory: Killed process|oom-kill)", re.I)),
]
USER_PATTERNS = [
    ("UNSUPPORTED_DEVICE", re.compile(r"(does not support 16-bit storage|Unsupported device|"
                                      r"no usable GPU found|Vulkan0.*not found|invalid device)", re.I)),
    ("DEVICE_LOST", re.compile(r"(VK_ERROR_DEVICE_LOST|ErrorDeviceLost|DeviceLostError|device lost)", re.I)),
    ("OUT_OF_MEMORY", re.compile(r"(VK_ERROR_OUT_OF_(DEVICE|HOST)_MEMORY|ErrorOutOfDeviceMemory|"
                                 r"Device memory allocation of size \d+ failed|failed to allocate|out of memory)", re.I)),
]
SEVERITY = ["GPU_PAGE_FAULT", "RING_TIMEOUT", "GPU_RESET", "DEVICE_LOST", "OUT_OF_MEMORY", "OOM_KILL",
            "UNSUPPORTED_DEVICE", "CRASH", "TIMEOUT", "NUMERIC_MISMATCH", "TOOL_ERROR", "SETUP_ERROR", "PASS"]


# ----------------------------------------------------------------- utilities
def sha256_file(p: str | Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def now() -> float:
    return time.time()


class Mirror:
    """Best-effort UDP mirror of journal lines and kernel messages."""

    def __init__(self, url: str | None):
        self.addr = None
        self.sock = None
        if url:
            m = re.match(r"^udp://([^:]+):(\d+)$", url)
            if not m:
                raise SystemExit(f"bad remote log url {url!r} (want udp://host:port)")
            self.addr = (m.group(1), int(m.group(2)))
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, kind: str, payload) -> None:
        if not self.sock:
            return
        try:
            msg = json.dumps({"k": kind, "t": now(), "host": platform.node(), "p": payload})
            self.sock.sendto(msg.encode()[:60000], self.addr)
        except OSError:
            pass


class Journal:
    def __init__(self, path: Path, mirror: Mirror):
        self.path = path
        self.mirror = mirror

    def append(self, rec: dict) -> None:
        rec = dict(rec, t=now())
        line = json.dumps(rec, sort_keys=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        try:  # also fsync the directory entry on first creation
            dfd = os.open(str(self.path.parent), os.O_RDONLY)
            os.fsync(dfd)
            os.close(dfd)
        except OSError:
            pass
        self.mirror.send("journal", rec)

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({"event": "corrupt-line", "raw": line[:200]})
        return out


class KmsgTap(threading.Thread):
    """Stream /dev/kmsg during a step to a file (fsync'd) and the UDP mirror."""

    def __init__(self, out: Path, mirror: Mirror):
        super().__init__(daemon=True)
        self.out = out
        self.mirror = mirror
        self.lines: list[str] = []
        self.stop_evt = threading.Event()
        self.available = False
        self.fd = -1
        # ORBIS_HARNESS_KMSG_SOURCE may name a regular file (e.g. a netconsole capture
        # or a test fixture) that is tailed instead of /dev/kmsg.
        self.source = os.environ.get("ORBIS_HARNESS_KMSG_SOURCE", "/dev/kmsg")
        self.is_file = os.path.isfile(self.source)
        try:
            self.fd = os.open(self.source, os.O_RDONLY | os.O_NONBLOCK)
            os.lseek(self.fd, 0, os.SEEK_END)  # only new messages
            self.available = True
        except OSError:
            self.available = False

    def run(self) -> None:
        if not self.available:
            return
        import select
        with open(self.out, "a", encoding="utf-8") as fh:
            while not self.stop_evt.is_set() or self.is_file:
                if self.is_file:
                    chunk = os.read(self.fd, 65536)
                    if not chunk:
                        if self.stop_evt.is_set():
                            break
                        time.sleep(0.1)
                        continue
                    for msg in chunk.decode("utf-8", "replace").splitlines():
                        self.lines.append(msg)
                        fh.write(msg + "\n")
                        self.mirror.send("kmsg", msg)
                    fh.flush()
                    continue
                r, _, _ = select.select([self.fd], [], [], 0.25)
                if not r:
                    continue
                while True:
                    try:
                        rec = os.read(self.fd, 8192)
                    except BlockingIOError:
                        break
                    except OSError:
                        break
                    if not rec:
                        break
                    msg = rec.decode("utf-8", "replace").split(";", 1)[-1].rstrip("\n")
                    self.lines.append(msg)
                    fh.write(msg + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                    self.mirror.send("kmsg", msg)

    def stop(self) -> list[str]:
        self.stop_evt.set()
        if self.is_alive():
            self.join(timeout=2)
        if self.fd >= 0:
            os.close(self.fd)
        return self.lines


def dmesg_lines() -> list[str]:
    if shutil.which("dmesg"):
        r = subprocess.run(["dmesg"], capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.splitlines()
    return []


def gpu_mem_sample() -> dict:
    out = {}
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]")):
        dev = card / "device"
        for k in ("mem_info_vram_used", "mem_info_gtt_used", "mem_info_vram_total", "mem_info_gtt_total"):
            try:
                out[f"{card.name}.{k}"] = int((dev / k).read_text())
            except (OSError, ValueError):
                pass
    try:
        mi = Path("/proc/meminfo").read_text()
        m = re.search(r"MemAvailable:\s+(\d+)", mi)
        if m:
            out["MemAvailable_kib"] = int(m.group(1))
    except OSError:
        pass
    return out


# ----------------------------------------------------------------- parsing
def parse_kld(text: str) -> dict:
    out = {}
    pats = {
        "mean_kld": r"Mean\s+KLD:\s+(-?[\d.]+)",
        "max_kld": r"Maximum KLD:\s+(-?[\d.]+)",
        "kld_p99": r"99\.0%\s+KLD:\s+(-?[\d.]+)",
        "same_top_p": r"Same top p:\s+([\d.]+)",
        "rms_dp": r"RMS Δp\s*:\s+([\d.]+)",
        "ppl_q": r"Mean PPL\(Q\)\s*:\s+([\d.]+)",
        "ppl_base": r"Mean PPL\(base\)\s*:\s+([\d.]+)",
    }
    for k, p in pats.items():
        m = re.search(p, text)
        if m:
            out[k] = float(m.group(1))
    return out


_EVAL_HDR = re.compile(r"^common_debug_cb_eval:\s+(\S+)\s+=\s+\((\w+)\)\s+(\w+)\((.*)\)\s+=\s+\{([^}]*)\}")
_EVAL_SUM = re.compile(r"^\s+sum\s+=\s+(-?[\d.eE+-]+|nan|inf|-inf)")


def parse_eval_callback(text: str) -> list[dict]:
    rows, cur = [], None
    for line in text.splitlines():
        m = _EVAL_HDR.match(line)
        if m:
            cur = {"name": m.group(1), "type": m.group(2), "op": m.group(3), "shape": m.group(5).strip()}
            rows.append(cur)
            continue
        m = _EVAL_SUM.match(line)
        if m and cur is not None and "sum" not in cur:
            try:
                cur["sum"] = float(m.group(1))
            except ValueError:
                cur["sum"] = float("nan")
    return rows


def compare_tensors(ref: list[dict], cand: list[dict], rel_tol: float, abs_tol: float) -> dict:
    """Align by tensor name occurrence order and report divergence."""
    from collections import defaultdict
    idx = defaultdict(list)
    for r in cand:
        idx[(r["name"], r["op"])].append(r)
    used = defaultdict(int)
    diffs, missing = [], 0
    first = None
    for r in ref:
        key = (r["name"], r["op"])
        lst = idx.get(key, [])
        if used[key] >= len(lst):
            missing += 1
            continue
        c = lst[used[key]]
        used[key] += 1
        a, b = r.get("sum"), c.get("sum")
        if a is None or b is None:
            continue
        err = abs(a - b)
        lim = max(abs_tol, rel_tol * abs(a))
        bad = not (err <= lim)  # NaN-safe
        d = {"name": r["name"], "op": r["op"], "shape": r["shape"], "ref_sum": a, "cand_sum": b,
             "abs_err": err, "limit": lim, "bad": bad}
        diffs.append(d)
        if bad and first is None:
            first = d
    worst = sorted((d for d in diffs if d["abs_err"] == d["abs_err"]), key=lambda d: d["abs_err"] / max(d["limit"], 1e-30),
                   reverse=True)[:10]
    return {"compared": len(diffs), "missing_in_candidate": missing, "first_divergence": first,
            "worst": worst, "bad_count": sum(d["bad"] for d in diffs)}


def scan_shader_dump(text: str) -> dict:
    """Count GFX7 memory-instruction families in a RADV_DEBUG=shaders dump.

    linux-psx found ACO selecting FLAT (VGPR address) instead of MUBUF for a
    compute fill on GFX7 and a Liverpool hang on that path. This counts how
    much FLAT addressing llama.cpp's pipelines use on this exact driver."""
    counts = {}
    for fam in ("flat_load", "flat_store", "flat_atomic", "global_load", "global_store",
                "buffer_load", "buffer_store", "buffer_atomic", "s_buffer_load", "ds_read", "ds_write"):
        counts[fam] = len(re.findall(r"\b" + fam + r"\w*", text))
    shaders = len(re.findall(r"^(?:disasm|ACO shader|.*Shader:|BB0:)", text, re.M))
    return {"instruction_counts": counts, "shader_blocks_seen": shaders,
            "uses_flat": any(counts[k] for k in ("flat_load", "flat_store", "flat_atomic"))}


# ----------------------------------------------------------------- the runner
class Campaign:
    def __init__(self, cfg: dict, out: Path):
        self.cfg = cfg
        self.out = out
        out.mkdir(parents=True, exist_ok=True)
        (out / "steps").mkdir(exist_ok=True)
        (out / "reference").mkdir(exist_ok=True)
        self.mirror = Mirror(cfg.get("remote_log"))
        self.journal = Journal(out / "journal.jsonl", self.mirror)

    # ---- pins
    def pins(self) -> dict:
        p = self.cfg["pins"]
        pins = {
            "harness_version": HARNESS_VERSION,
            "model": p["model"],
            "model_sha256": p.get("model_sha256") or sha256_file(p["model"]),
            "llama_build": p.get("llama_build"),
            "llama_cpu_bin_dir": p["llama_cpu_bin_dir"],
            "llama_vk_bin_dir": p["llama_vk_bin_dir"],
            "kernel_release": platform.release(),
            "kernel_cmdline": Path("/proc/cmdline").read_text().strip() if Path("/proc/cmdline").exists() else None,
            "candidate_env": self.cfg.get("candidate", {}).get("env", {}),
        }
        probe = shutil.which("orbis-gpu-probe") or p.get("gpu_probe")
        if probe and os.path.exists(probe):
            try:
                r = subprocess.run([probe, "--vk", "--json"], capture_output=True, text=True, timeout=60,
                                   env=self._env(self.cfg.get("candidate", {}).get("env", {})))
                d = json.loads(r.stdout)
                devs = d.get("vulkan", {}).get("devices", [])
                pins["vulkan_devices"] = [{k: x.get(k) for k in ("name", "device_type", "driver_name", "driver_info",
                                                                   "api_version", "heaps")} for x in devs]
                pins["mesa_version"] = next((x.get("driver_info") for x in devs if x.get("driver_name")), None)
            except (OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
                pins["vulkan_devices_error"] = str(exc)
        if p.get("expect_model_sha256") and pins["model_sha256"] != p["expect_model_sha256"]:
            raise SystemExit(f"model sha256 mismatch: {pins['model_sha256']} != {p['expect_model_sha256']}")
        return pins

    def _env(self, extra: dict) -> dict:
        e = dict(os.environ)
        e.update({k: str(v) for k, v in (extra or {}).items()})
        return e

    # ---- one subprocess, fully observed
    def _run(self, step_id: str, argv: list[str], env: dict, timeout: float) -> dict:
        sdir = self.out / "steps" / step_id
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / "argv.json").write_text(json.dumps({"argv": argv, "env": {k: env[k] for k in env
                                                                           if k.startswith(("RADV", "AMD_", "MESA", "GGML", "VK_"))}}, indent=1))
        tap = KmsgTap(sdir / "kmsg.log", self.mirror)
        dm_before = None if tap.available else dmesg_lines()
        tap.start()
        mem_before = gpu_mem_sample()
        ru0 = resource.getrusage(resource.RUSAGE_CHILDREN)
        t0 = time.monotonic()
        timed_out = False
        with open(sdir / "stdout.log", "wb") as so, open(sdir / "stderr.log", "wb") as se:
            try:
                proc = subprocess.Popen(argv, stdout=so, stderr=se, env=env, start_new_session=True)
                rc = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
                try:
                    rc = proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    rc = None  # D-state: stuck in the kernel (GPU wait)
            except OSError as exc:
                return {"setup_error": str(exc), "rc": None}
        elapsed = time.monotonic() - t0
        ru1 = resource.getrusage(resource.RUSAGE_CHILDREN)
        kmsg = tap.stop()
        if dm_before is not None:
            after = dmesg_lines()
            kmsg = after[len(dm_before):] if len(after) >= len(dm_before) else after
            (sdir / "kmsg.log").write_text("\n".join(kmsg))
        out_txt = (sdir / "stdout.log").read_text(errors="replace")
        err_txt = (sdir / "stderr.log").read_text(errors="replace")
        return {"rc": rc, "timed_out": timed_out, "elapsed_s": round(elapsed, 3),
                "max_rss_kib_children": ru1.ru_maxrss, "cpu_user_s": round(ru1.ru_utime - ru0.ru_utime, 3),
                "mem_before": mem_before, "mem_after": gpu_mem_sample(), "kmsg": kmsg[-400:],
                "stdout": out_txt, "stderr": err_txt}

    @staticmethod
    def classify(run: dict, numeric_ok: bool | None) -> tuple[str, list[str]]:
        signals = []
        if run.get("setup_error"):
            return "SETUP_ERROR", [run["setup_error"]]
        for kind, rx in FAULT_PATTERNS:
            for line in run.get("kmsg", []):
                if rx.search(line):
                    signals.append(f"{kind}: {line.strip()[:200]}")
                    break
        text = run.get("stderr", "") + run.get("stdout", "")
        for kind, rx in USER_PATTERNS:
            m = rx.search(text)
            if m:
                signals.append(f"{kind}: {m.group(0)[:200]}")
        if run.get("timed_out"):
            signals.append("TIMEOUT: step exceeded its deadline" + (" (process stuck in kernel)" if run.get("rc") is None else ""))
        rc = run.get("rc")
        if run.get("timed_out"):
            pass  # our own SIGKILL after the deadline is not a crash
        elif rc is not None and rc < 0:
            signals.append(f"CRASH: killed by signal {-rc} ({signal.Signals(-rc).name if -rc in signal.Signals._value2member_map_ else '?'})")
        elif rc not in (0, None):
            signals.append(f"CRASH: exit code {rc}")
        if numeric_ok is False:
            signals.append("NUMERIC_MISMATCH: candidate logits/tokens differ beyond thresholds")
        kinds = [s.split(":", 1)[0] for s in signals]
        for k in SEVERITY:
            if k in kinds:
                return k, signals
        return ("PASS" if numeric_ok is not False else "NUMERIC_MISMATCH"), signals

    # ---- reference logits (CPU), cached per ctx
    def reference(self, ctx: int) -> Path:
        base = self.out / "reference" / f"base_c{ctx}.kld"
        if base.exists():
            return base
        ref = self.cfg.get("reference", {})
        argv = [str(Path(self.cfg["pins"]["llama_cpu_bin_dir"]) / "llama-perplexity"),
                "-m", self.cfg["pins"]["model"], "-f", self.cfg["corpus"], "-c", str(ctx), "-b", str(ctx),
                "--chunks", str(self.cfg.get("axes", {}).get("chunks", 2)),
                "-t", str(ref.get("threads", os.cpu_count() or 2)),
                "--kl-divergence-base", str(base), "--device", "none"]
        run = self._run(f"ref_c{ctx}", argv, self._env(ref.get("env", {})), 3600)
        if run.get("rc") != 0 or not base.exists():
            raise SystemExit(f"reference run failed for ctx {ctx}: rc={run.get('rc')}\n{run.get('stderr', '')[-2000:]}")
        return base

    def _ngl(self, spec) -> int:
        n_layer = self.cfg["pins"].get("n_layer") or 99
        if spec == "all":
            return 999
        if spec == "half":
            return max(1, (n_layer + 1) // 2)
        return int(spec)

    def preflight(self) -> dict:
        """Can the candidate build load the model on the candidate device at all?
        A cheap, GPU-light check (ngl=1, 1 token) so a missing feature (e.g. 16-bit
        storage) is reported as UNSUPPORTED_DEVICE instead of as a sweep of crashes."""
        sid = "preflight"
        done = {r["step"]: r for r in self.journal.records() if r.get("event") == "finished"}
        if sid in done and done[sid]["outcome"] == "PASS":
            return done[sid]
        cand = self.cfg.get("candidate", {})
        argv = [str(Path(self.cfg["pins"]["llama_vk_bin_dir"]) / "llama-eval-callback"),
                "-m", self.cfg["pins"]["model"], "-p", "a", "-n", "1", "-ngl", "1", "-t", "1"]
        self.journal.append({"event": "started", "step": sid, "kind": "preflight"})
        run = self._run(sid, argv, self._env(cand.get("env", {})), 300)
        outcome, signals = self.classify(run, None)
        rec = {"event": "finished", "step": sid, "kind": "preflight", "outcome": outcome, "signals": signals}
        self.journal.append(rec)
        return rec

    def step_kld(self, ctx: int, ubatch: int, ngl, tag: str = "") -> dict:
        step_id = f"kld_c{ctx}_ub{ubatch}_ngl{ngl}{tag}"
        done = {r["step"]: r for r in self.journal.records() if r.get("event") == "finished"}
        if step_id in done:
            return done[step_id]
        base = self.reference(ctx)
        cand = self.cfg.get("candidate", {})
        thr = self.cfg.get("thresholds", {})
        argv = [str(Path(self.cfg["pins"]["llama_vk_bin_dir"]) / "llama-perplexity"),
                "-m", self.cfg["pins"]["model"], "-f", self.cfg["corpus"], "-c", str(ctx), "-b", str(ctx),
                "-ub", str(min(ubatch, ctx)), "--chunks", str(self.cfg.get("axes", {}).get("chunks", 2)),
                "-ngl", str(self._ngl(ngl)), "-t", str(cand.get("threads", os.cpu_count() or 2)),
                "--kl-divergence-base", str(base), "--kl-divergence"]
        if cand.get("device"):
            argv += ["--device", cand["device"]]
        params = {"ctx": ctx, "ubatch": ubatch, "ngl": ngl}
        self.journal.append({"event": "started", "step": step_id, "kind": "kld", "params": params})
        attempts = []
        for attempt in range(int(self.cfg.get("incomplete_retries", 2)) + 1):
            sid = step_id if attempt == 0 else f"{step_id}_retry{attempt}"
            run = self._run(sid, argv, self._env(cand.get("env", {})),
                            float(self.cfg.get("timeouts", {}).get("step_s", 900)))
            kld = parse_kld(run.get("stdout", "") + run.get("stderr", ""))
            attempts.append({"rc": run.get("rc"), "complete": bool(kld), "elapsed_s": run.get("elapsed_s")})
            # llama-perplexity can exit 0 without its statistics (an error line lost by the
            # asynchronous logger); that is a tool anomaly, retried, not a GPU verdict.
            if kld or run.get("rc") != 0 or run.get("timed_out") or any(
                    rx.search(l) for _, rx in FAULT_PATTERNS for l in run.get("kmsg", [])):
                break
        numeric_ok = None
        if kld:
            numeric_ok = (kld.get("mean_kld", 1e9) <= thr.get("max_mean_kld", 0.01)
                          and kld.get("same_top_p", 0) >= thr.get("min_same_top_p", 99.0)
                          and kld.get("kld_p99", 1e9) <= thr.get("max_kld_p99", 0.1))
        outcome, signals = self.classify(run, numeric_ok if kld else (None if run.get("rc") == 0 else None))
        if run.get("rc") == 0 and not kld and outcome == "PASS":
            outcome, signals = "TOOL_ERROR", [f"no KL-divergence statistics after {len(attempts)} attempt(s) "
                                              "(llama-perplexity exited 0 early)"]
        rec = {"event": "finished", "step": step_id, "kind": "kld", "params": params, "outcome": outcome,
               "signals": signals, "kld": kld, "elapsed_s": run.get("elapsed_s"),
               "rc": run.get("rc"), "timed_out": run.get("timed_out"), "attempts": attempts,
               "mem_before": run.get("mem_before"), "mem_after": run.get("mem_after"),
               "max_rss_kib_children": run.get("max_rss_kib_children")}
        self.journal.append(rec)
        return rec

    def step_tensors(self, prompt_tokens: int, ngl) -> dict:
        step_id = f"tensors_p{prompt_tokens}_ngl{ngl}"
        words = Path(self.cfg["corpus"]).read_text().split()
        prompt = " ".join(words[:max(1, prompt_tokens // 2)])
        res = {}
        for which, bindir, extra, env in (
                ("ref", self.cfg["pins"]["llama_cpu_bin_dir"], ["--device", "none"], self.cfg.get("reference", {}).get("env", {})),
                ("cand", self.cfg["pins"]["llama_vk_bin_dir"], ["-ngl", str(self._ngl(ngl))], self.cfg.get("candidate", {}).get("env", {}))):
            sid = f"{step_id}_{which}"
            argv = [str(Path(bindir) / "llama-eval-callback"), "-m", self.cfg["pins"]["model"], "-p", prompt,
                    "-n", "1", "-t", "2", "--seed", "1", *extra]
            if which == "cand":
                self.journal.append({"event": "started", "step": sid, "kind": "tensors"})
            run = self._run(sid, argv, self._env(env), float(self.cfg.get("timeouts", {}).get("step_s", 900)))
            res[which] = (run, parse_eval_callback(run.get("stdout", "") + run.get("stderr", "")))
        cmp = compare_tensors(res["ref"][1], res["cand"][1], self.cfg.get("thresholds", {}).get("tensor_rel_tol", 1e-3),
                              self.cfg.get("thresholds", {}).get("tensor_abs_tol", 1e-3))
        outcome, signals = self.classify(res["cand"][0], cmp["bad_count"] == 0 if cmp["compared"] else None)
        rec = {"event": "finished", "step": f"{step_id}_cand", "kind": "tensors", "outcome": outcome,
               "signals": signals, "comparison": cmp,
               "ref_tensors": len(res["ref"][1]), "cand_tensors": len(res["cand"][1])}
        self.journal.append(rec)
        return rec

    def step_shader_scan(self) -> dict:
        sid = "shader_scan"
        done = {r["step"]: r for r in self.journal.records() if r.get("event") == "finished"}
        if sid in done:
            return done[sid]
        env = dict(self.cfg.get("candidate", {}).get("env", {}))
        env["RADV_DEBUG"] = ",".join(x for x in (env.get("RADV_DEBUG", ""), "shaders") if x)
        argv = [str(Path(self.cfg["pins"]["llama_vk_bin_dir"]) / "llama-eval-callback"), "-m", self.cfg["pins"]["model"],
                "-p", "the PS4", "-n", "1", "-ngl", "999"]
        self.journal.append({"event": "started", "step": sid, "kind": "shader_scan"})
        run = self._run(sid, argv, self._env(env), 1800)
        scan = scan_shader_dump(run.get("stderr", "") + run.get("stdout", ""))
        outcome, signals = self.classify(run, None)
        rec = {"event": "finished", "step": sid, "kind": "shader_scan", "outcome": outcome,
               "signals": signals, "scan": scan,
               "note": "only meaningful on RADV; other drivers ignore RADV_DEBUG"}
        self.journal.append(rec)
        return rec

    def step_server(self) -> dict:
        """Greedy completions from CPU and Vulkan llama-server; mixed prompt lengths in one process."""
        sid = "server_mixed"
        done = {r["step"]: r for r in self.journal.records() if r.get("event") == "finished"}
        if sid in done:
            return done[sid]
        lengths = self.cfg.get("server", {}).get("prompt_words", [16, 256, 32, 512, 64])
        words = Path(self.cfg["corpus"]).read_text().split()
        prompts = [" ".join(words[i * 7:i * 7 + n]) for i, n in enumerate(lengths)]
        ctx = int(self.cfg.get("server", {}).get("ctx", 2048))
        results = {}
        self.journal.append({"event": "started", "step": sid, "kind": "server", "params": {"lengths": lengths}})
        for which, bindir, extra, env, port in (
                ("ref", self.cfg["pins"]["llama_cpu_bin_dir"], ["--device", "none"], self.cfg.get("reference", {}).get("env", {}), 18181),
                ("cand", self.cfg["pins"]["llama_vk_bin_dir"], ["-ngl", "999"], self.cfg.get("candidate", {}).get("env", {}), 18182)):
            sdir = self.out / "steps" / f"{sid}_{which}"
            sdir.mkdir(parents=True, exist_ok=True)
            argv = [str(Path(bindir) / "llama-server"), "-m", self.cfg["pins"]["model"], "-c", str(ctx),
                    "--host", "127.0.0.1", "--port", str(port), "--no-webui", "-np", "1", "--seed", "1", *extra]
            tap = KmsgTap(sdir / "kmsg.log", self.mirror)
            tap.start()
            with open(sdir / "server.log", "wb") as log:
                proc = subprocess.Popen(argv, stdout=log, stderr=log, env=self._env(env), start_new_session=True)
            outs, err, timed_out = [], None, False
            try:
                deadline = time.monotonic() + float(self.cfg.get("server", {}).get("health_timeout_s", 300))
                healthy = False
                while time.monotonic() < deadline:
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                            if r.status == 200:
                                healthy = True
                                break
                    except OSError:
                        time.sleep(0.5)
                    if proc.poll() is not None:
                        err = f"server exited rc={proc.returncode}"
                        break
                if not healthy and err is None:
                    err, timed_out = "server did not become healthy before the deadline", True
                for p in prompts if err is None else []:
                    body = json.dumps({"prompt": p, "n_predict": 12, "temperature": 0, "top_k": 1,
                                       "seed": 1, "cache_prompt": False, "return_tokens": True}).encode()
                    req = urllib.request.Request(f"http://127.0.0.1:{port}/completion", data=body,
                                                 headers={"Content-Type": "application/json"})
                    try:
                        with urllib.request.urlopen(req, timeout=600) as r:
                            d = json.loads(r.read())
                        outs.append({"content": d.get("content"), "tokens": d.get("tokens"),
                                     "prompt_n": (d.get("timings") or {}).get("prompt_n"),
                                     "prompt_per_second": (d.get("timings") or {}).get("prompt_per_second"),
                                     "predicted_per_second": (d.get("timings") or {}).get("predicted_per_second")})
                    except (OSError, json.JSONDecodeError) as exc:
                        err = f"request failed: {exc}"
                        break
            finally:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    proc.wait(timeout=20)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except OSError:
                        pass
                kmsg = tap.stop()
            results[which] = {"outputs": outs, "error": err, "timed_out": timed_out, "kmsg": kmsg[-200:],
                              "log_tail": (sdir / "server.log").read_text(errors="replace")[-3000:]}
        ref, cand = results["ref"], results["cand"]
        same = (not ref["error"] and not cand["error"] and len(ref["outputs"]) == len(cand["outputs"]) and
                all((a.get("tokens") or a.get("content")) == (b.get("tokens") or b.get("content"))
                    for a, b in zip(ref["outputs"], cand["outputs"])))
        fake_run = {"rc": 0 if not cand["error"] else (None if cand.get("timed_out") else 1),
                    "timed_out": cand.get("timed_out", False), "kmsg": cand["kmsg"], "stderr": cand["log_tail"]}
        outcome, signals = self.classify(fake_run, same if not cand["error"] else None)
        if ref["error"]:
            outcome, signals = "SETUP_ERROR", [f"reference server: {ref['error']}"]
        rec = {"event": "finished", "step": sid, "kind": "server", "outcome": outcome, "signals": signals,
               "identical_greedy_outputs": same,
               "ref": [{k: o[k] for k in ("content", "prompt_n", "prompt_per_second", "predicted_per_second")} for o in ref["outputs"]],
               "cand": [{k: o[k] for k in ("content", "prompt_n", "prompt_per_second", "predicted_per_second")} for o in cand["outputs"]],
               "cand_error": cand["error"]}
        self.journal.append(rec)
        return rec

    # ---- orchestration
    def mark_interrupted(self) -> list[str]:
        recs = self.journal.records()
        started = {r["step"]: r for r in recs if r.get("event") == "started"}
        finished = {r["step"] for r in recs if r.get("event") == "finished"}
        orphans = [s for s in started if s not in finished]
        for s in orphans:
            self.journal.append({"event": "finished", "step": s, "kind": started[s].get("kind"),
                                 "params": started[s].get("params"), "outcome": "HANG_OR_RESET_INFERRED",
                                 "signals": ["step started but never finished: the machine hung, reset or lost power "
                                             "during this step (see steps/<id>/kmsg.log and the UDP mirror)"]})
        return orphans

    def run(self) -> dict:
        orphans = self.mark_interrupted()
        if not any(r.get("event") == "pins" for r in self.journal.records()):
            self.journal.append({"event": "pins", "pins": self.pins()})
        else:
            cur = self.pins()
            old = next(r["pins"] for r in self.journal.records() if r.get("event") == "pins")
            for k in ("model_sha256", "kernel_release", "mesa_version", "llama_build"):
                if old.get(k) != cur.get(k):
                    raise SystemExit(f"pin changed since campaign start: {k} {old.get(k)} -> {cur.get(k)}; "
                                     "start a new campaign directory")
        pf = self.preflight()
        if pf["outcome"] != "PASS":
            summary = {"event": "summary", "aborted": True, "reason": "candidate preflight failed",
                       "preflight": pf, "boundaries": [], "orphans_marked": orphans}
            self.journal.append(summary)
            return summary
        axes = self.cfg.get("axes", {})
        ctxs = sorted(axes.get("ctx", [128, 256, 512, 1024, 2048]))
        boundaries = []
        if self.cfg.get("shader_scan", True):
            self.step_shader_scan()
        for ub in axes.get("ubatch", [512]):
            for ngl in axes.get("ngl", ["all"]):
                last_pass, first_fail = None, None
                for c in ctxs:
                    rec = self.step_kld(c, ub, ngl)
                    if rec["outcome"] == "PASS":
                        last_pass = c
                    else:
                        first_fail = c
                        break
                # Bisect between last pass and first failure (skip if failures were hangs and
                # bisect_hangs is false: each probe could cost a power cycle).
                fail_rec = None
                if first_fail is not None:
                    fail_rec = next(r for r in self.journal.records() if r.get("event") == "finished"
                                    and r.get("step") == f"kld_c{first_fail}_ub{ub}_ngl{ngl}")
                bisect = self.cfg.get("bisect", True) and first_fail is not None and last_pass is not None
                if bisect and fail_rec and fail_rec["outcome"] in ("HANG_OR_RESET_INFERRED",) and not self.cfg.get("bisect_hangs", False):
                    bisect = False
                lo, hi = last_pass, first_fail
                steps = 0
                while bisect and hi - lo > int(self.cfg.get("bisect_resolution", 64)) and steps < 8:
                    mid = ((lo + hi) // 2) // 32 * 32
                    if mid <= lo or mid >= hi:
                        break
                    rec = self.step_kld(mid, ub, ngl)
                    if rec["outcome"] == "PASS":
                        lo = mid
                    else:
                        hi = mid
                    steps += 1
                boundaries.append({"ubatch": ub, "ngl": ngl, "last_pass_ctx": lo, "first_fail_ctx": hi,
                                   "first_fail_outcome": fail_rec["outcome"] if fail_rec else None})
        if self.cfg.get("tensors", {}).get("enabled", True):
            tb = self.cfg["tensors"].get("prompt_tokens", [32])
            for pt in tb:
                self.step_tensors(pt, "all")
        if self.cfg.get("server", {}).get("enabled", True):
            self.step_server()
        summary = {"event": "summary", "boundaries": boundaries, "orphans_marked": orphans}
        self.journal.append(summary)
        return summary


def report(out: Path) -> dict:
    j = Journal(out / "journal.jsonl", Mirror(None))
    recs = j.records()
    pins = next((r["pins"] for r in recs if r.get("event") == "pins"), {})
    fin = [r for r in recs if r.get("event") == "finished"]
    by_outcome = {}
    for r in fin:
        by_outcome.setdefault(r["outcome"], []).append(r["step"])
    summ = [r for r in recs if r.get("event") == "summary"]
    return {"pins": pins, "steps": len(fin), "by_outcome": by_outcome,
            "boundaries": summ[-1]["boundaries"] if summ else None,
            "kld_table": [{"step": r["step"], **(r.get("params") or {}), "outcome": r["outcome"],
                           **{k: r.get("kld", {}).get(k) for k in ("mean_kld", "kld_p99", "same_top_p")},
                           "elapsed_s": r.get("elapsed_s")} for r in fin if r.get("kind") == "kld"],
            "tensors": [{"step": r["step"], "outcome": r["outcome"],
                         "first_divergence": (r.get("comparison") or {}).get("first_divergence")}
                        for r in fin if r.get("kind") == "tensors"],
            "shader_scan": next((r.get("scan") for r in fin if r.get("kind") == "shader_scan"), None),
            "server": next(({k: r.get(k) for k in ("outcome", "identical_greedy_outputs", "cand_error")}
                            for r in fin if r.get("kind") == "server"), None)}


def gate(out: Path, short_ctx: list[int], long_ctx: list[int]) -> dict:
    rep = report(out)
    table = rep["kld_table"]

    def passed(ctxs):
        rows = [r for r in table if r.get("ctx") in ctxs and r.get("ngl") in ("all", 999)]
        return bool(rows) and all(r["outcome"] == "PASS" for r in rows), rows
    short_ok, _ = passed(short_ctx)
    long_ok, _ = passed(long_ctx)
    server_ok = (rep.get("server") or {}).get("outcome") == "PASS"
    tensors_ok = all(t["outcome"] == "PASS" for t in rep["tensors"]) if rep["tensors"] else False
    max_pass = max([r["ctx"] for r in table if r["outcome"] == "PASS" and r.get("ngl") in ("all", 999)] or [0])
    passed_all = short_ok and long_ok and server_ok and tensors_ok
    p = rep["pins"]
    devs = p.get("vulkan_devices", []) or []
    on_software = any((d.get("device_type") == 4) for d in devs)
    # The candidate must demonstrably have run on a hardware AMD GPU: llama.cpp names the
    # device it uses in every candidate log ("ggml_vulkan: 0 = <name> ...").
    used = set()
    for logf in (out / "steps").glob("kld_*/stderr.log"):
        for m in re.finditer(r"ggml_vulkan: \d+ = ([^|(\n]+)", logf.read_text(errors="replace")):
            used.add(m.group(1).strip())
    hw_used = [u for u in used if re.search(r"AMD|Radeon|LIVERPOOL|GLADIUS|BONAIRE", u, re.I)
               and not re.search(r"llvmpipe|swiftshader|lavapipe", u, re.I)]
    hw_evidence = bool(hw_used) and not on_software
    return {
        "schema": 1, "harness_version": HARNESS_VERSION, "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "pins": {k: p.get(k) for k in ("kernel_release", "mesa_version", "llama_build", "model_sha256", "model")},
        "inference_gate": {"passed": passed_all and hw_evidence, "hardware_gpu_evidence": sorted(hw_used),
                           "short_prompts": short_ok,
                           "long_prefill": long_ok, "server_mixed_greedy_identical": server_ok,
                           "intermediate_tensors": tensors_ok, "max_prompt_tokens": max_pass,
                           "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                           "summary": (f"short={short_ok} long={long_ok} server={server_ok} tensors={tensors_ok} "
                                       f"max_ctx_pass={max_pass}" +
                                       ("" if hw_evidence else " [NO EVIDENCE THE CANDIDATE RAN ON A HARDWARE AMD GPU: not a GPU result]"))},
        "longrun_gate": None,
        "evidence_dir": str(out),
    }


def receive(port: int, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", port))
    print(f"listening on udp/{port}, writing to {out}", file=sys.stderr)
    with open(out / "mirror.jsonl", "a", encoding="utf-8") as fh:
        while True:
            data, addr = s.recvfrom(65535)
            line = data.decode("utf-8", "replace")
            fh.write(line + "\n")
            fh.flush()
            try:
                d = json.loads(line)
                if d.get("k") == "kmsg" and re.search(r"amdgpu|drm|fault|timeout|reset", d.get("p", ""), re.I):
                    print(f"[{addr[0]}] KMSG {d['p']}", flush=True)
                elif d.get("k") == "journal":
                    p = d.get("p", {})
                    print(f"[{addr[0]}] {p.get('event')} {p.get('step', '')} {p.get('outcome', '')}", flush=True)
            except json.JSONDecodeError:
                pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="orbis-infer-harness")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "resume"):
        sp = sub.add_parser(name)
        sp.add_argument("--config", required=True)
        sp.add_argument("--out", required=True)
    sp = sub.add_parser("report")
    sp.add_argument("--out", required=True)
    sp = sub.add_parser("gate")
    sp.add_argument("--out", required=True)
    sp.add_argument("--short-ctx", default="128,256")
    sp.add_argument("--long-ctx", default="1024,2048")
    sp.add_argument("--write-receipt", help="e.g. /var/lib/orbis/gpu-gate.json")
    sp = sub.add_parser("receive")
    sp.add_argument("--port", type=int, default=47999)
    sp.add_argument("--out", default="orbis-mirror")
    a = ap.parse_args(argv)
    if a.cmd in ("run", "resume"):
        cfg = json.loads(Path(a.config).read_text())
        res = Campaign(cfg, Path(a.out)).run()
        print(json.dumps(res, indent=2))
        return 0
    if a.cmd == "report":
        print(json.dumps(report(Path(a.out)), indent=2))
        return 0
    if a.cmd == "gate":
        g = gate(Path(a.out), [int(x) for x in a.short_ctx.split(",")], [int(x) for x in a.long_ctx.split(",")])
        print(json.dumps(g, indent=2))
        if a.write_receipt:
            Path(a.write_receipt).parent.mkdir(parents=True, exist_ok=True)
            Path(a.write_receipt).write_text(json.dumps(g, indent=2))
        return 0 if g["inference_gate"]["passed"] else 1
    if a.cmd == "receive":
        receive(a.port, Path(a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
