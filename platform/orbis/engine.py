"""The Orbis mode engine: plan and execute PC / GAMING / AI transitions.

A transition is a managed state change:

  plan     decide fast vs reboot from evidence (VRAM carve-out, GPU health,
           policy), list what will stop and start, and what boot files change
  stop     end graphical sessions that belong to the old personality, then
           `systemctl isolate orbis-<mode>.target` (systemd stops everything
           not required by the new target; shared services live under
           multi-user.target and survive)
  verify   wait until no must-stop process of other personalities remains;
           if something refuses to exit, report it and offer a reboot
  apply    profile sysctls and power preset
  ready    profile readiness checks (unit active, HTTP health, session)
  measure  memory before/after; GPU client residue
  persist  desired mode for the next boot (generator + bootargs.txt)

VRAM is a boot-time carve-out written by the PS4 loader (see bootfs.py), so
any change to it means a reboot through the PS4 system software.
"""

from __future__ import annotations

import fnmatch
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Callable

from . import MODES, bootfs, config, gpu, hardware, memory, paths, power
from .config import atomic_write
from .systemd import Systemd, SystemdError

POLICIES = ("auto", "fast", "reboot")


@dataclass
class Plan:
    from_mode: str | None
    to_mode: str
    policy: str
    method: str                      # fast | reboot | noop | blocked
    backend: str | None = None
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    will_stop: list[str] = field(default_factory=list)
    will_start: list[str] = field(default_factory=list)
    sessions_to_end: list[dict] = field(default_factory=list)
    vram_current_mb: int | None = None
    vram_min_mb: int | None = None
    vram_next_mb: int | None = None
    reboot_required: bool = False
    reboot_recommended: bool = False
    ram_returned_by_reboot_mib: int | None = None
    boot_actions: list[dict] = field(default_factory=list)
    blocked: str | None = None
    operator_steps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Result:
    ok: bool
    plan: dict
    started: float
    finished: float
    steps: list[dict] = field(default_factory=list)
    memory_before: dict | None = None
    memory_after: dict | None = None
    memory_delta: dict | None = None
    residue: list[dict] = field(default_factory=list)
    gpu_residue: list[dict] = field(default_factory=list)
    message: str = ""
    offer_reboot: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _http_ok(url: str, timeout: float) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 (local URL from profile)
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


class Engine:
    def __init__(self, sd: Systemd, cfg: dict | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 http_ok: Callable[[str, float], bool] = _http_ok,
                 gpu_state: Callable[[], tuple[bool, dict]] | None = None,
                 vram_reader: Callable[[], int | None] | None = None,
                 snapshot: Callable[[], dict] | None = None,
                 residue_fn: Callable[[list[str], set[int]], list[dict]] | None = None,
                 drm_clients_fn: Callable[[], list[dict]] | None = None,
                 dry_run: bool = False):
        self.sd = sd
        self.cfg = cfg if cfg is not None else config.load_config()
        self.sleep = sleep
        self.http_ok = http_ok
        self.gpu_state = gpu_state or gpu.graphics_state_clean
        self.vram_reader = vram_reader or self._read_vram_mb
        self.snapshot = snapshot or (lambda: memory.snapshot(include_processes=True))
        self.residue_fn = residue_fn or memory.residue
        self.drm_clients_fn = drm_clients_fn or memory.drm_clients
        self.dry_run = dry_run
        self.profiles = config.load_profiles()

    # ------------------------------------------------------------ state
    @staticmethod
    def _read_vram_mb() -> int | None:
        total = hardware.gpu_memory().get("mem_info_vram_total")
        return int(round(total / (1 << 20))) if total else None

    def current_mode(self) -> str | None:
        for m in MODES:
            if self.sd.is_active(self.profiles[m].target) == "active":
                return m
        return None

    @staticmethod
    def desired_mode() -> str | None:
        try:
            v = paths.desired_mode_file().read_text().strip()
        except OSError:
            return None
        return v if v in MODES else None

    def persist_desired(self, mode: str) -> None:
        if self.dry_run:
            return
        atomic_write(paths.desired_mode_file(), (mode + "\n").encode())

    def backend_for(self, mode: str, override: str | None = None) -> str | None:
        if mode != "ai":
            return None
        return override or self.cfg.get("ai", {}).get("backend", "cpu")

    def boot_partition(self) -> bootfs.BootPartition | None:
        try:
            return bootfs.locate(self.cfg, mount=not self.dry_run)
        except bootfs.BootFsError:
            return None

    def foreign_patterns(self, mode: str) -> list[str]:
        own = set(self.profiles[mode].owned_patterns) | set(self.profiles[mode].start_units)
        out: list[str] = []
        for m in MODES:
            if m != mode:
                out += [x for x in self.profiles[m].owned_patterns if x not in own and x not in out]
        return out

    # ------------------------------------------------------------ planning
    def plan(self, to_mode: str, policy: str | None = None,
             backend: str | None = None, force: bool = False) -> Plan:
        if to_mode not in MODES:
            raise ValueError(f"unknown mode {to_mode!r}")
        policy = policy or self.cfg.get("orbis", {}).get("switch_policy", "auto")
        if policy not in POLICIES:
            raise ValueError(f"unknown policy {policy!r}")
        prof = self.profiles[to_mode]
        frm = self.current_mode()
        be = self.backend_for(to_mode, backend)
        vmin, vpref = prof.vram_for_backend(be)
        cur_vram = self.vram_reader()
        p = Plan(from_mode=frm, to_mode=to_mode, policy=policy, method="fast", backend=be,
                 vram_current_mb=cur_vram, vram_min_mb=vmin, vram_next_mb=vpref)

        # What stops / starts.
        for m in MODES:
            if m == to_mode:
                continue
            other = self.profiles[m]
            for u in self.sd.list_units([other.target, *other.start_units]):
                if u["active"] == "active":
                    p.will_stop.append(u["unit"])
        p.will_start = [prof.target, *prof.start_units]
        for s in self.sd.sessions():
            if s.get("type") in ("x11", "wayland", "mir") and s.get("class") == "user":
                if frm != to_mode:
                    p.sessions_to_end.append(s)

        # Evidence that forces or recommends a reboot.
        if cur_vram is None:
            p.warnings.append("current VRAM carve-out unknown (amdgpu not exposing mem_info_vram_total)")
        elif cur_vram < vmin:
            p.reboot_required = True
            p.reasons.append(f"{prof.title} needs at least {vmin} MB VRAM; "
                             f"this boot has {cur_vram} MB (set by the PS4 loader)")
        elif cur_vram < vpref:
            p.warnings.append(f"this boot has {cur_vram} MB VRAM; the next payload launch will use the "
                              f"{prof.title} preset of {vpref} MB")
        elif cur_vram > vpref:
            saving = cur_vram - vpref
            p.ram_returned_by_reboot_mib = saving
            thr = int(self.cfg.get("orbis", {}).get("reboot_saving_threshold_mb", 512))
            if saving >= thr:
                p.reboot_recommended = True
                p.reasons.append(f"a restart with {vpref} MB VRAM would return {saving} MiB to Linux")

        needs_gpu = prof.display or be in ("vulkan", "auto")
        clean, events = self.gpu_state()
        if not clean and needs_gpu:
            p.reboot_required = True
            p.reasons.append("graphics state is unclean since boot: " +
                             ", ".join(f"{k}×{len(v)}" for k, v in events.items()))

        if frm == to_mode and not p.reboot_required:
            p.method = "noop"
            p.reasons.append(f"already in {prof.title} mode")
        elif policy == "reboot":
            p.method = "reboot"
            p.reasons.append("clean reboot requested")
        elif policy == "fast":
            if p.reboot_required and not force:
                p.method = "blocked"
                p.blocked = ("fast switch refused: " + "; ".join(p.reasons) +
                             ". Use --policy reboot (or --force to try anyway).")
            else:
                p.method = "fast"
                if p.reboot_required:
                    p.warnings.append("forced fast switch despite: " + "; ".join(p.reasons))
        else:  # auto
            p.method = "reboot" if p.reboot_required else "fast"
            if p.reboot_recommended and p.method == "fast":
                p.warnings.append(
                    f"switching now; vram.txt will be set to {vpref} MB so the next payload "
                    f"launch returns ~{p.ram_returned_by_reboot_mib} MiB to Linux")

        if p.method == "reboot":
            p.operator_steps = [
                "The console returns to the PS4 system software (Linux cannot reboot into itself here).",
                "Relaunch the Linux payload (vram.txt on the USB boot partition overrides the payload's VRAM size).",
                f"Orbis will start directly in {prof.title} mode with {vpref} MB VRAM.",
            ]

        # Boot-file changes (always staged so the next boot matches the choice).
        bp = self.boot_partition()
        if bp is None:
            p.warnings.append("PS4 boot partition not found/mounted: bootargs.txt and vram.txt "
                              "cannot be staged; next boot uses current files")
        else:
            probe = bootfs.BootPartition(bp.root, cmdline_limit=bp.cmdline_limit)
            try:
                probe.set_mode(to_mode, dry_run=True)
                allowed = self.cfg.get("boot", {}).get("allowed_vram_mb")
                probe.set_vram(vpref, allowed, dry_run=True)
            except bootfs.BootFsError as exc:
                p.warnings.append(f"boot files: {exc}")
            p.boot_actions = [a.to_dict() for a in probe.actions]
        if p.method == "reboot" and bp is None and p.reboot_required and cur_vram is not None and cur_vram < vmin:
            p.method = "blocked"
            p.blocked = ("a reboot cannot apply the VRAM requirement because the boot partition "
                         "is not available; mount it or set vram.txt manually")
        return p

    # ------------------------------------------------------------ execution
    def _step(self, res: Result, name: str, ok: bool, **detail) -> None:
        res.steps.append({"step": name, "ok": ok, "t": round(time.time() - res.started, 3), **detail})

    def _stage_boot_files(self, plan: Plan, res: Result) -> None:
        bp = self.boot_partition()
        if bp is None:
            self._step(res, "stage-boot-files", False, detail="boot partition unavailable")
            return
        try:
            bp.set_mode(plan.to_mode, dry_run=self.dry_run)
            bp.set_vram(plan.vram_next_mb, self.cfg.get("boot", {}).get("allowed_vram_mb"),
                        dry_run=self.dry_run)
            # Read back what the loader will see.
            st = bp.status()
            ok = self.dry_run or (st["next_mode"] == plan.to_mode and st["next_vram_mb"] == plan.vram_next_mb)
            self._step(res, "stage-boot-files", ok, next_mode=st["next_mode"],
                       next_vram_mb=st["next_vram_mb"], actions=[a.to_dict() for a in bp.actions])
        except (bootfs.BootFsError, OSError) as exc:
            self._step(res, "stage-boot-files", False, detail=str(exc))

    def _wait_residue(self, patterns: list[str], timeout: float) -> list[dict]:
        deadline = time.monotonic() + timeout
        left = self.residue_fn(patterns, {os.getpid()})
        while left and time.monotonic() < deadline:
            self.sleep(0.5)
            left = self.residue_fn(patterns, {os.getpid()})
        return left

    def _readiness(self, mode: str, res: Result) -> bool:
        prof = self.profiles[mode]
        all_ok = True
        for chk in prof.readiness:
            kind = chk.get("type")
            timeout = float(chk.get("timeout_s", 60))
            deadline = time.monotonic() + timeout
            ok = False
            while True:
                if kind == "unit":
                    ok = self.sd.is_active(chk["unit"]) == "active"
                elif kind == "http":
                    ok = self.http_ok(chk["url"], 2.0)
                elif kind == "session":
                    ok = any(s.get("type") in ("x11", "wayland") for s in self.sd.sessions())
                elif kind == "path":
                    ok = paths.p(chk["path"]).exists()
                else:
                    ok = False
                if ok or time.monotonic() >= deadline or self.dry_run:
                    break
                self.sleep(1.0)
            self._step(res, f"ready:{kind}", ok or self.dry_run, check=chk)
            all_ok &= ok or self.dry_run
        return all_ok

    def _record(self, res: Result) -> None:
        if self.dry_run:
            return
        f = paths.transitions_log()
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            rec = res.to_dict()
            for k in ("memory_before", "memory_after"):
                if rec.get(k):
                    rec[k] = {kk: vv for kk, vv in rec[k].items() if kk not in ("top", "drm_clients")}
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
        except OSError:
            pass

    def execute(self, plan: Plan, progress: Callable[[str], None] | None = None,
                reboot_now: bool = True) -> Result:
        say = progress or (lambda s: None)
        res = Result(ok=False, plan=plan.to_dict(), started=time.time(), finished=0.0)
        if plan.method == "blocked":
            res.message = plan.blocked or "blocked"
            res.finished = time.time()
            return res
        if plan.method == "noop":
            res.ok = True
            res.message = plan.reasons[0] if plan.reasons else "nothing to do"
            res.finished = time.time()
            return res

        self.persist_desired(plan.to_mode)
        self._step(res, "persist-desired-mode", True, mode=plan.to_mode)
        say("staging boot files")
        self._stage_boot_files(plan, res)

        if plan.method == "reboot":
            say("rebooting")
            os.sync() if not self.dry_run else None
            if reboot_now and not self.dry_run:
                try:
                    self.sd.reboot()
                    self._step(res, "reboot", True)
                except SystemdError as exc:
                    self._step(res, "reboot", False, detail=str(exc))
                    res.message = f"reboot failed: {exc}"
                    res.finished = time.time()
                    self._record(res)
                    return res
            res.ok = True
            res.message = " ".join(plan.operator_steps)
            res.finished = time.time()
            self._record(res)
            return res

        # ---- fast path
        res.memory_before = self.snapshot()
        prof = self.profiles[plan.to_mode]
        for s in plan.sessions_to_end:
            say(f"ending {s.get('type')} session {s.get('id')} ({s.get('user')})")
            try:
                if not self.dry_run:
                    self.sd.terminate_session(s["id"])
                self._step(res, "end-session", True, session=s.get("id"))
            except SystemdError as exc:
                self._step(res, "end-session", False, session=s.get("id"), detail=str(exc))
        say(f"isolating {prof.target}")
        try:
            if not self.dry_run:
                self.sd.isolate(prof.target)
            self._step(res, "isolate", True, target=prof.target)
        except SystemdError as exc:
            self._step(res, "isolate", False, detail=str(exc))
            res.message = f"systemd could not switch to {prof.target}: {exc}"
            res.offer_reboot = True
            res.finished = time.time()
            self._record(res)
            return res

        # Everything other personalities own must be gone.
        patterns = self.foreign_patterns(plan.to_mode)
        timeout = float(self.cfg.get("orbis", {}).get("stop_timeout_s", 30))
        left = [] if self.dry_run else self._wait_residue(patterns, timeout)
        res.residue = left
        self._step(res, "verify-stopped", not left, leftover=left)

        say("applying profile settings")
        self._step(res, "sysctl", True, changes=power.apply_sysctls(prof.sysctl, dry_run=self.dry_run))
        self._step(res, "power", True, result=power.apply_preset(prof.power_preset, dry_run=self.dry_run))

        say("waiting for readiness")
        ready = self._readiness(plan.to_mode, res)

        res.memory_after = self.snapshot()
        res.memory_delta = memory.diff(res.memory_before, res.memory_after)
        if not self.dry_run:
            # GPU clients that belong to another personality (or any client at all when the
            # target personality is headless and CPU-only) still hold GPU memory.
            clients = [c for c in self.drm_clients_fn() if c["pid"] != os.getpid()]
            if prof.allow_gpu_clients or plan.backend in ("vulkan", "auto"):
                res.gpu_residue = [c for c in clients
                                   if any(fnmatch.fnmatch(c.get("comm") or "", pt) or
                                          fnmatch.fnmatch(c.get("unit") or "", pt) for pt in patterns)]
            else:
                res.gpu_residue = clients
            self._step(res, "gpu-clients", not res.gpu_residue, clients=res.gpu_residue)

        res.ok = ready and not left and not res.gpu_residue
        if res.gpu_residue and not left:
            res.offer_reboot = True
            res.message = (f"{prof.title} is active but {len(res.gpu_residue)} GPU client(s) from the previous "
                           "mode still hold GPU memory")
        elif left:
            res.offer_reboot = True
            res.message = (f"{prof.title} is active but {len(left)} process(es) from the previous "
                           "mode did not exit; a clean reboot will release them")
        elif not ready:
            res.offer_reboot = True
            res.message = f"{prof.title} services did not report ready in time"
        else:
            d = res.memory_delta or {}
            res.message = (f"{prof.title} mode active. MemAvailable changed by "
                           f"{memory.human_kib(d.get('mem_available_delta_kib'))}.")
            if plan.reboot_recommended:
                res.message += (f" Next payload launch will use {plan.vram_next_mb} MB VRAM "
                                f"(returns ~{plan.ram_returned_by_reboot_mib} MiB).")
        res.finished = time.time()
        self._record(res)
        return res
