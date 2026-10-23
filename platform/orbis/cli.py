"""`orbis` command-line interface.

    orbis status                      current/desired mode, memory, boot files
    orbis mode pc|gaming|ai           switch personality (asks before closing sessions/rebooting)
    orbis plan pc|gaming|ai           show what a switch would do, change nothing
    orbis doctor [--deep] [--json]    diagnostics for support and bug reports
    orbis capabilities                hardware capability matrix for this console
    orbis budget                      memory budget and top processes
    orbis boot ...                    PS4 boot partition: status, vram, kernel install/rollback
    orbis isa-audit [PATH...]         Jaguar instruction-set safety audit
    orbis ai ...                      models, launch plan, settings
    orbis pkg allow-graphics-change   one-time override for the package guard

Talks to orbisd when it is running; `--direct` (root) runs in-process, and
`--simulate` drives an in-memory systemd model for demos and CI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import (MODES, __version__, ai, bootfs, bzimage, capabilities, config, doctor, hardware,
               isa_audit, memory, paths)
from .client import ApiError, Client, DaemonUnavailable

BANNER = "NICKLABS / PS4 ORBIS LINUX — PC · GAMING · AI"


def _p(obj, as_json: bool):
    if as_json:
        print(json.dumps(obj, indent=2, default=str))
        return True
    return False


def _engine(a):
    from .engine import Engine
    from .systemd import RealSystemd, default_fake
    sd = default_fake() if a.simulate else RealSystemd()
    kw = {}
    if a.simulate:
        sd.start("orbis-pc.target")
        kw = dict(gpu_state=lambda: (True, {}), vram_reader=lambda: 1024,
                  snapshot=lambda: memory.snapshot(include_processes=False),
                  residue_fn=lambda pats, ign: [], drm_clients_fn=lambda: [],
                  http_ok=lambda url, t: True)
    return Engine(sd, config.load_config(), dry_run=getattr(a, "dry_run", False) or a.simulate, **kw)


def _use_daemon(a) -> Client | None:
    if a.direct or a.simulate:
        return None
    c = Client()
    try:
        c.call("hello")
        return c
    except DaemonUnavailable:
        if os.geteuid() == 0:
            print("orbis: orbisd not running; operating directly", file=sys.stderr)
            return None
        raise SystemExit("orbis: orbisd is not running (start it: systemctl start orbisd) "
                         "or run as root with --direct")


def _print_plan(plan: dict) -> None:
    print(f"Switch: {plan['from_mode'] or '(none)'} → {plan['to_mode']}   method: {plan['method'].upper()}"
          f"   policy: {plan['policy']}" + (f"   backend: {plan['backend']}" if plan.get("backend") else ""))
    if plan.get("vram_current_mb") is not None:
        print(f"VRAM carve-out: now {plan['vram_current_mb']} MB, needs ≥{plan['vram_min_mb']} MB, "
              f"next boot {plan['vram_next_mb']} MB")
    for r in plan.get("reasons", []):
        print(f"  • {r}")
    for w in plan.get("warnings", []):
        print(f"  ! {w}")
    if plan.get("will_stop"):
        print("  stops:  " + ", ".join(plan["will_stop"]))
    print("  starts: " + ", ".join(plan.get("will_start", [])))
    for s in plan.get("sessions_to_end", []):
        print(f"  closes: {s.get('type')} session {s.get('id')} of {s.get('user')} (unsaved work is lost)")
    for b in plan.get("boot_actions", []):
        print(f"  boot:   {Path(b['path']).name}: {b.get('before')!r} → {b.get('after')!r}")
    for s in plan.get("operator_steps", []):
        print(f"  ➜ {s}")
    if plan.get("blocked"):
        print(f"  BLOCKED: {plan['blocked']}")


def cmd_status(a):
    c = _use_daemon(a)
    if c:
        st = c.call("status")
    else:
        eng = _engine(a)
        b = None
        try:
            bp = bootfs.locate(eng.cfg, mount=False)
            b = bp.status() if bp else None
        except bootfs.BootFsError:
            pass
        st = {"current_mode": eng.current_mode(), "desired_mode": eng.desired_mode(),
              "platform": hardware.detect_platform(), "memory": memory.snapshot(include_processes=False),
              "sensors": hardware.sensors(), "boot_partition": b, "alerts": []}
    if _p(st, a.json):
        return 0
    plat = st["platform"]
    mem = st["memory"]
    print(BANNER)
    print(f"  mode       {(st['current_mode'] or 'none').upper()}  (next boot: {(st['desired_mode'] or 'default').upper()})")
    print(f"  hardware   " + (f"{plat['southbridge']} / {plat['gpu']['name']}" if plat["is_ps4"] else "not a PS4"))
    print(f"  memory     {memory.human_kib(mem['mem_used_kib'])} used, "
          f"{memory.human_kib(mem['mem_available_kib'])} available of {memory.human_kib(mem['mem_total_kib'])}")
    g = mem.get("gpu") or {}
    if g.get("mem_info_vram_total"):
        print(f"  vram       {g['mem_info_vram_total'] >> 20} MiB carve-out, {(g.get('mem_info_vram_used') or 0) >> 20} MiB used; "
              f"GTT {(g.get('mem_info_gtt_used') or 0) >> 20}/{(g.get('mem_info_gtt_total') or 0) >> 20} MiB")
    temps = st.get("sensors", {}).get("temperatures", [])
    print("  thermal    " + (", ".join(f"{t['chip']} {t['celsius']:.0f}°C" for t in temps[:3]) if temps else "no sensors exposed")
          + f"; fan control {st.get('sensors', {}).get('fan_control', '?')}")
    b = st.get("boot_partition")
    print("  boot       " + (f"next VRAM {b['next_vram_mb']} MB, next mode {b['next_mode'] or '-'} ({b['path']})"
                             if b else "PS4 boot partition not mounted"))
    for al in st.get("alerts", [])[-5:]:
        print(f"  ALERT      [{al['level']}] {al['kind']}: {al['message']}")
    return 0


def cmd_plan(a):
    c = _use_daemon(a)
    plan = c.call("plan", mode=a.mode, policy=a.policy, backend=a.backend, force=a.force) if c \
        else _engine(a).plan(a.mode, a.policy, a.backend, a.force).to_dict()
    if not _p(plan, a.json):
        _print_plan(plan)
    return 0 if plan["method"] != "blocked" else 3


def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        return False
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def cmd_mode(a):
    if a.dry_run:
        return cmd_plan(a)
    c = _use_daemon(a)
    if c:
        plan = c.call("plan", mode=a.mode, policy=a.policy, backend=a.backend, force=a.force)
        if not a.json:
            _print_plan(plan)
        if plan["method"] == "blocked":
            return 3
        if plan["method"] == "noop":
            return 0
        ok_sessions = not plan.get("sessions_to_end") or _confirm("Close the graphical session(s)?", a.yes)
        ok_reboot = plan["method"] != "reboot" or _confirm(
            "Reboot now? The PS4 system software will start; relaunch the Linux payload afterwards.", a.yes)
        if not (ok_sessions and ok_reboot):
            print("cancelled")
            return 4
        r = c.call("switch", mode=a.mode, policy=a.policy, backend=a.backend, force=a.force,
                   confirm_sessions=True, confirm_reboot=True)
        job = c.wait_job(r["job"], on_progress=None if a.json else (lambda m: print(f"  … {m}")))
        res = job.get("result") or {}
    else:
        eng = _engine(a)
        plan = eng.plan(a.mode, a.policy, a.backend, a.force)
        if not a.json:
            _print_plan(plan.to_dict())
        if plan.method == "blocked":
            return 3
        if (plan.sessions_to_end and not _confirm("Close the graphical session(s)?", a.yes)) or \
           (plan.method == "reboot" and not _confirm("Reboot now?", a.yes)):
            print("cancelled")
            return 4
        res = eng.execute(plan, None if a.json else (lambda m: print(f"  … {m}")),
                          reboot_now=not a.stage_only).to_dict()
    if _p(res, a.json):
        return 0 if res.get("ok") else 1
    print(("OK: " if res.get("ok") else "NOT OK: ") + res.get("message", ""))
    for r in res.get("residue", []):
        print(f"  still running: pid {r['pid']} {r['comm']} ({r.get('unit')})")
    if res.get("offer_reboot"):
        print(f"  ➜ run `orbis mode {a.mode} --policy reboot` for a clean restart")
    return 0 if res.get("ok") else 1


def cmd_doctor(a):
    c = _use_daemon(a) if not a.direct else None
    if c:
        r = c.call("doctor.run", deep=a.deep)
        job = c.wait_job(r["job"], timeout=600)
        d = job["result"]
        if a.json:
            print(json.dumps(d, indent=2, default=str))
        else:
            rep = doctor.Report(version=d["version"], time=d["time"],
                                findings=[doctor.Finding(**f) for f in d["findings"]],
                                capabilities=d["capabilities"])
            print(doctor.render_text(rep))
        return {"OK": 0, "INFO": 0, "WARN": 1, "FAIL": 2}[d["worst"]]
    rep = doctor.run(deep=a.deep, probe=not a.no_probe)
    print(json.dumps(rep.to_dict(), indent=2, default=str) if a.json else doctor.render_text(rep))
    return {"OK": 0, "INFO": 0, "WARN": 1, "FAIL": 2}[rep.worst]


def cmd_capabilities(a):
    inv = hardware.inventory()
    cfg = config.load_config()
    res = capabilities.evaluate(inv["platform"], cfg.get("hardware", {}).get("model"),
                                capabilities.runtime_overlays(inv, None))
    if _p(res, a.json):
        return 0
    print(f"Qualification group: {res['group'] or 'unknown'} (matrix as of {res['as_of']})")
    for n in res["notes"]:
        print(f"  note: {n}")
    for c in res["capabilities"]:
        src = c["static"].get("source")
        print(f"  {c['effective']:22} {c['title']}" + (f"  [{src}]" if src else ""))
    return 0


def cmd_budget(a):
    snap = memory.snapshot(include_processes=True)
    if _p(snap, a.json):
        return 0
    print(f"MemTotal {memory.human_kib(snap['mem_total_kib'])}  used {memory.human_kib(snap['mem_used_kib'])}  "
          f"available {memory.human_kib(snap['mem_available_kib'])}")
    for k, v in snap.get("slices", {}).items():
        print(f"  slice {k:22} {memory.human_kib(v // 1024)}")
    print("  top processes (PSS):")
    for r in snap.get("top", [])[:15]:
        print(f"    {memory.human_kib(r['pss_kib']):>10}  {r['comm']:<20} {r.get('unit') or ''}")
    for c in snap.get("drm_clients", []):
        print(f"  GPU client pid {c['pid']} {c['comm']}: vram {memory.human_kib(c['vram_kib'])} gtt {memory.human_kib(c['gtt_kib'])}")
    return 0


def cmd_boot(a):
    cfg = config.load_config()
    if a.boot_cmd == "check-kernel":
        return bzimage.main([a.path] + (["--json"] if a.json else []))
    bp = bootfs.locate(cfg, mount=os.geteuid() == 0)
    if bp is None:
        raise SystemExit("orbis: PS4 boot partition not found (set boot.fat_label / boot.mountpoint)")
    if a.boot_cmd == "status":
        st = bp.status()
        if not _p(st, a.json):
            for k, v in st.items():
                print(f"  {k:26} {v}")
        return 0
    if a.boot_cmd == "set-vram":
        bp.set_vram(a.mb, cfg.get("boot", {}).get("allowed_vram_mb"), dry_run=a.dry_run)
    elif a.boot_cmd == "set-mode":
        bp.set_mode(a.mode, dry_run=a.dry_run)
    elif a.boot_cmd == "install-kernel":
        bp.install_kernel_pair(Path(a.kernel), Path(a.initramfs), dry_run=a.dry_run, force=a.force)
    elif a.boot_cmd == "rollback-kernel":
        bp.rollback_kernel_pair(dry_run=a.dry_run)
    for act in bp.actions:
        print(("would " if a.dry_run else "") + f"{act.kind} {act.path}: {act.detail}")
    return 0


def cmd_ai(a):
    cfg = config.load_config()
    if a.ai_cmd == "models":
        ms = ai.list_models(cfg)
        if not _p(ms, a.json):
            for m in ms:
                print(f"  {Path(m['file']).name:40} {m.get('arch')} layers={m.get('n_layer')} "
                      f"{(m.get('size_bytes') or 0) >> 20} MiB" if "error" not in m else f"  {m['file']}: {m['error']}")
        return 0
    if a.ai_cmd == "plan":
        model = a.model or cfg.get("ai", {}).get("model")
        if not model:
            raise SystemExit("orbis: no model configured (orbis ai set model <file.gguf>)")
        mp = Path(model) if "/" in model else ai.models_dir(cfg) / model
        mi = ai.ModelInfo.from_file(mp)
        heap = None
        gate = {}
        lp = ai.plan_launch(cfg, mi, hardware.meminfo().get("MemAvailable", 0), os.cpu_count() or 1,
                            heap, gate, ai.current_identity(), a.backend)
        if not _p(lp.to_dict(), a.json):
            print(f"backend: {lp.active_backend} ({lp.backend_reason})")
            print(f"ctx {lp.n_ctx}, batch {lp.n_batch}/{lp.n_ubatch}, gpu layers {lp.n_gpu_layers}, threads {lp.threads}")
            print(f"budget: {lp.budget}")
            for e in lp.errors:
                print(f"ERROR: {e}")
            print("argv: " + " ".join(lp.argv))
        return 0 if not lp.errors else 1
    if a.ai_cmd == "set":
        val = a.value
        if val.isdigit():
            val = int(val)
        elif val in ("true", "false"):
            val = val == "true"
        c = _use_daemon(a)
        out = c.call("ai.set", key=a.key, value=val) if c else config.set_setting(f"ai.{a.key}", val)
        _p(out, True)
        return 0
    return 2


def cmd_pkg(a):
    if os.geteuid() != 0:
        raise SystemExit("orbis: must be root")
    f = paths.etc_dir() / "allow-graphics-change"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(a.reason.strip() + "\n")
    print(f"next pacman transaction may change the graphics/kernel stack once (reason recorded): {a.reason}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="orbis", description=BANNER)
    ap.add_argument("--version", action="version", version=f"orbis {__version__}")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--direct", action="store_true", help="do not use orbisd (root only)")
    ap.add_argument("--simulate", action="store_true", help="use an in-memory systemd model (no changes)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    for name in ("mode", "plan"):
        sp = sub.add_parser(name)
        sp.add_argument("mode", choices=MODES)
        sp.add_argument("--policy", choices=("auto", "fast", "reboot"))
        sp.add_argument("--backend", choices=("cpu", "vulkan", "auto"), help="AI backend")
        sp.add_argument("--force", action="store_true")
        sp.add_argument("--dry-run", action="store_true", default=(name == "plan"))
        sp.add_argument("-y", "--yes", action="store_true")
        sp.add_argument("--stage-only", action="store_true", help="stage boot files but do not reboot")
    sp = sub.add_parser("doctor")
    sp.add_argument("--deep", action="store_true")
    sp.add_argument("--no-probe", action="store_true")
    sub.add_parser("capabilities")
    sub.add_parser("budget")
    bp = sub.add_parser("boot")
    bsub = bp.add_subparsers(dest="boot_cmd", required=True)
    bsub.add_parser("status")
    x = bsub.add_parser("set-vram"); x.add_argument("mb", type=int); x.add_argument("--dry-run", action="store_true")
    x = bsub.add_parser("set-mode"); x.add_argument("mode", choices=MODES); x.add_argument("--dry-run", action="store_true")
    x = bsub.add_parser("install-kernel"); x.add_argument("kernel"); x.add_argument("initramfs")
    x.add_argument("--dry-run", action="store_true"); x.add_argument("--force", action="store_true")
    x = bsub.add_parser("rollback-kernel"); x.add_argument("--dry-run", action="store_true")
    x = bsub.add_parser("check-kernel"); x.add_argument("path")
    ip = sub.add_parser("isa-audit")
    ip.add_argument("paths", nargs="*")
    ip.add_argument("--pacman", action="store_true")
    ip.add_argument("--no-disasm", action="store_true")
    aip = sub.add_parser("ai")
    asub = aip.add_subparsers(dest="ai_cmd", required=True)
    asub.add_parser("models")
    x = asub.add_parser("plan"); x.add_argument("--model"); x.add_argument("--backend", choices=("cpu", "vulkan", "auto"))
    x = asub.add_parser("set"); x.add_argument("key"); x.add_argument("value")
    pp = sub.add_parser("pkg")
    psub = pp.add_subparsers(dest="pkg_cmd", required=True)
    x = psub.add_parser("allow-graphics-change"); x.add_argument("--reason", required=True)
    return ap


def main(argv: list[str] | None = None) -> int:
    import signal
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # `orbis ... | head` should not traceback
    a = build_parser().parse_args(argv)
    try:
        if a.cmd == "status":
            return cmd_status(a)
        if a.cmd == "plan":
            return cmd_plan(a)
        if a.cmd == "mode":
            return cmd_mode(a)
        if a.cmd == "doctor":
            return cmd_doctor(a)
        if a.cmd == "capabilities":
            return cmd_capabilities(a)
        if a.cmd == "budget":
            return cmd_budget(a)
        if a.cmd == "boot":
            return cmd_boot(a)
        if a.cmd == "isa-audit":
            return isa_audit.main(a.paths + (["--pacman"] if a.pacman else []) +
                                  (["--no-disasm"] if a.no_disasm else []) + (["--json"] if a.json else []))
        if a.cmd == "ai":
            return cmd_ai(a)
        if a.cmd == "pkg":
            return cmd_pkg(a)
    except (ApiError, config.ConfigError, bootfs.BootFsError, ValueError) as exc:
        print(f"orbis: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
