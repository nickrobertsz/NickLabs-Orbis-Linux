"""orbisd — the privileged Orbis control service.

Runs as root, listens ONLY on a Unix socket (/run/orbis/orbisd.sock, mode
0660, group `orbis`). Never on the network. The LAN dashboard (orbis-web)
runs unprivileged and talks to this socket.

Protocol: newline-delimited JSON. Request {"id": n, "method": "...", "params": {...}}
Response {"id": n, "ok": true, "result": ...} or {"id": n, "ok": false, "error": "..."}.
API version: orbis.API_VERSION. Unknown methods are rejected.

Authorisation (SO_PEERCRED):
  read methods   root, or a member of group `orbis`
  write methods  root, or a member of group `orbis-admin`
"""

from __future__ import annotations

import grp
import json
import os
import pwd
import socket
import socketserver
import struct
import sys
import threading
import time
import traceback
import urllib.request
import uuid

from . import (API_VERSION, MODES, __version__, ai, bootfs, capabilities, config, doctor, gpu,
               hardware, memory, paths)
from .engine import Engine
from .systemd import RealSystemd

READ_METHODS = {"hello", "status", "plan", "job", "capabilities", "budget", "ai.status",
                "ai.models", "logs", "gaming.status", "doctor.last"}
WRITE_METHODS = {"switch", "doctor.run", "ai.set", "reboot", "settings.set"}


def _peer(sock: socket.socket) -> tuple[int, int, int]:
    creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    pid, uid, gid = struct.unpack("3i", creds)
    return pid, uid, gid


def _in_group(uid: int, gid: int, group: str) -> bool:
    try:
        g = grp.getgrnam(group)
    except KeyError:
        return False
    if gid == g.gr_gid:
        return True
    try:
        name = pwd.getpwuid(uid).pw_name
    except KeyError:
        return False
    return name in g.gr_mem


class State:
    def __init__(self, sd=None, cfg: dict | None = None, engine_kwargs: dict | None = None,
                 monitor: bool = True):
        self.cfg = cfg if cfg is not None else config.load_config()
        self.sd = sd or RealSystemd()
        self.engine_kwargs = engine_kwargs or {}
        self.monitor = monitor
        self.lock = threading.Lock()
        self.jobs: dict[str, dict] = {}
        self.alerts: list[dict] = []
        self.boot = self._record_boot()
        self.last_doctor: dict | None = None
        self._known_faults = 0

    def _record_boot(self) -> dict:
        b = {"time": time.time(), "kernel": hardware.kernel_info().get("release"),
             "vram_total_bytes": hardware.gpu_memory().get("mem_info_vram_total")}
        try:
            bp = bootfs.locate(self.cfg, mount=True)
            if bp:
                b["boot_partition"] = bp.status()
        except bootfs.BootFsError as exc:
            b["boot_partition_error"] = str(exc)
        try:
            paths.run_dir().mkdir(parents=True, exist_ok=True)
            (paths.run_dir() / "boot.json").write_text(json.dumps(b, indent=2))
        except OSError:
            pass
        return b

    def alert(self, level: str, kind: str, msg: str) -> None:
        a = {"t": time.time(), "level": level, "kind": kind, "message": msg}
        # De-duplicate identical consecutive alerts.
        if self.alerts and self.alerts[-1]["kind"] == kind and self.alerts[-1]["message"] == msg:
            self.alerts[-1]["t"] = a["t"]
            return
        self.alerts.append(a)
        del self.alerts[:-50]
        print(f"orbisd: {level}: {kind}: {msg}", file=sys.stderr, flush=True)

    def monitor_once(self) -> None:
        pr = memory.mem_pressure_level(self.cfg)
        if pr["level"] != "ok":
            self.alert("critical" if pr["level"] == "critical" else "warning", "memory",
                       f"MemAvailable is {pr['mem_available_kib'] // 1024} MiB")
        nets = hardware.network()
        if nets and not any(n["operstate"] == "up" for n in nets):
            self.alert("warning", "network", "no network interface is up")
        ev = gpu.fault_events()
        n = sum(len(v) for k, v in ev.items() if k != "uvd")
        if n > self._known_faults:
            self.alert("critical", "gpu", "new GPU fault/reset messages in the kernel log: " +
                       ", ".join(f"{k}×{len(v)}" for k, v in ev.items()))
            self._known_faults = n

    def monitor_loop(self) -> None:
        interval = float(self.cfg.get("alerts", {}).get("interval_s", 15))
        while True:
            try:
                self.monitor_once()
            except Exception as exc:  # noqa: BLE001 - keep monitoring alive
                print(f"orbisd: monitor error: {exc}", file=sys.stderr)
            time.sleep(interval)

    # ------------------------------------------------------------ methods
    def engine(self, dry_run: bool = False) -> Engine:
        return Engine(self.sd, self.cfg, dry_run=dry_run, **self.engine_kwargs)

    def m_hello(self, p, who):
        return {"api": API_VERSION, "version": __version__, "modes": list(MODES),
                "you": {"uid": who[1], "admin": who[3]}}

    def m_status(self, p, who):
        eng = self.engine()
        inv_cpu = hardware.cpu_info()
        snap = memory.snapshot(include_processes=False)
        bp = None
        try:
            b = bootfs.locate(self.cfg, mount=False)
            bp = b.status() if b else None
        except bootfs.BootFsError:
            pass
        sens = hardware.sensors()
        return {
            "current_mode": eng.current_mode(), "desired_mode": eng.desired_mode(),
            "platform": hardware.detect_platform(), "cpu": inv_cpu,
            "memory": snap, "pressure": memory.mem_pressure_level(self.cfg, snap),
            "sensors": sens, "network": hardware.network(), "boot_partition": bp,
            "cpu_load": hardware.cpu_load(), "root_storage": hardware.root_storage(),
            "boot_record": self.boot, "alerts": self.alerts[-20:],
            "jobs_active": [j for j in self.jobs.values() if j["state"] == "running"],
            "kernel": {k: v for k, v in hardware.kernel_info().items() if k != "config"},
            "profiles": {m: pr.to_dict() for m, pr in eng.profiles.items()},
        }

    def m_plan(self, p, who):
        return self.engine().plan(p["mode"], p.get("policy"), p.get("backend"), bool(p.get("force"))).to_dict()

    def _start_job(self, kind: str, fn) -> str:
        jid = uuid.uuid4().hex[:12]
        job = {"id": jid, "kind": kind, "state": "running", "progress": [], "result": None,
               "started": time.time()}
        self.jobs[jid] = job

        def runner():
            try:
                job["result"] = fn(lambda s: job["progress"].append({"t": time.time(), "msg": s}))
                job["state"] = "done"
            except Exception as exc:  # noqa: BLE001
                job["state"] = "failed"
                job["result"] = {"error": str(exc), "trace": traceback.format_exc()[-2000:]}
            job["finished"] = time.time()
        threading.Thread(target=runner, daemon=True).start()
        return jid

    def m_switch(self, p, who):
        mode = p["mode"]
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("another transition is in progress")
        dry = bool(p.get("dry_run"))

        def work(progress):
            try:
                eng = self.engine(dry_run=dry)
                plan = eng.plan(mode, p.get("policy"), p.get("backend"), bool(p.get("force")))
                if plan.method == "reboot" and not p.get("confirm_reboot") and not dry:
                    return {"ok": False, "needs_confirmation": True, "plan": plan.to_dict(),
                            "message": "this transition needs a reboot through the PS4 system software; "
                                       "confirm to proceed"}
                if plan.sessions_to_end and not p.get("confirm_sessions") and not dry:
                    return {"ok": False, "needs_confirmation": True, "plan": plan.to_dict(),
                            "message": "graphical sessions will be closed (unsaved work is lost); confirm to proceed"}
                return eng.execute(plan, progress).to_dict()
            finally:
                self.lock.release()
        return {"job": self._start_job("switch", work)}

    def m_job(self, p, who):
        j = self.jobs.get(p["id"])
        if not j:
            raise KeyError("no such job")
        return j

    def m_capabilities(self, p, who):
        inv = hardware.inventory()
        # GPU tiers come from the last doctor run (the probe is not re-run on every page view).
        tiers = (self.last_doctor or {}).get("gpu_tiers") or gpu.summarize(None, inv, gpu.load_gate_receipt())
        return capabilities.evaluate(inv["platform"], self.cfg.get("hardware", {}).get("model"),
                                     capabilities.runtime_overlays(inv, tiers))

    def m_budget(self, p, who):
        eng = self.engine()
        snap = memory.snapshot(include_processes=True)
        return {"snapshot": snap, "current_mode": eng.current_mode(),
                "targets": {m: pr.budget for m, pr in eng.profiles.items()}}

    def m_doctor_run(self, p, who):
        deep = bool(p.get("deep"))

        def work(progress):
            progress("running diagnostics")
            rep = doctor.run(self.cfg, deep=deep)
            d = rep.to_dict()
            self.last_doctor = d
            return d
        return {"job": self._start_job("doctor", work)}

    def m_doctor_last(self, p, who):
        return self.last_doctor

    def m_ai_models(self, p, who):
        return ai.list_models(self.cfg)

    def m_ai_status(self, p, who):
        a = self.cfg.get("ai", {})
        url = f"http://127.0.0.1:{a.get('port', 8081)}"
        out = {"service": self.sd.is_active("orbis-llama.service"), "endpoint": url,
               "lan_api": bool(a.get("lan_api", False)), "requested_backend": a.get("backend", "cpu")}
        try:
            with urllib.request.urlopen(url + "/health", timeout=2) as r:
                out["health"] = json.loads(r.read() or b"{}")
        except (OSError, ValueError):
            out["health"] = None
        lp = paths.p("run/orbis-ai/llama-launch.json")
        if lp.exists():
            try:
                out["launch"] = json.loads(lp.read_text())
            except (OSError, json.JSONDecodeError):
                pass
        out["gate"] = gpu.load_gate_receipt() or None
        return out

    def m_ai_set(self, p, who):
        return config.set_setting(f"ai.{p['key']}", p["value"])

    def m_settings_set(self, p, who):
        data = config.set_setting(p["key"], p["value"])
        self.cfg = config.load_config()
        return data

    def m_logs(self, p, who):
        n = int(p.get("n", 50))
        f = paths.transitions_log()
        lines = f.read_text().splitlines()[-n:] if f.exists() else []
        return {"transitions": [json.loads(x) for x in lines if x.strip()], "alerts": self.alerts}

    def m_gaming_status(self, p, who):
        ctrl = []
        try:
            text = paths.procfs("bus/input/devices").read_text()
            for block in text.split("\n\n"):
                name = next((l.split("=", 1)[1].strip('"') for l in block.splitlines() if l.startswith("N: Name=")), "")
                if any(k in name for k in ("Wireless Controller", "DualShock", "DualSense", "Sony",
                                           "Xbox", "X-Box", "Gamepad", "Controller")) and "Motion" not in name \
                        and "Touchpad" not in name:
                    ctrl.append(name)
        except OSError:
            pass
        compat = []
        f = paths.share_dir() / "gaming-compat.json"
        if f.exists():
            try:
                compat = json.loads(f.read_text()).get("titles", [])
            except (OSError, json.JSONDecodeError):
                pass
        return {"controllers": sorted(set(ctrl)), "compat_list": compat,
                "session": self.sd.is_active("orbis-gaming-session.service"),
                "frontend": self.cfg.get("gaming", {}).get("frontend", "steam-gamepadui")}

    def m_reboot(self, p, who):
        if not p.get("confirm"):
            raise PermissionError("reboot requires confirm=true")
        threading.Timer(1.0, self.sd.reboot).start()
        return {"message": "rebooting; the console returns to the PS4 system software. Relaunch the payload."}


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        st: State = self.server.state  # type: ignore[attr-defined]
        try:
            pid, uid, gid = _peer(self.request)
        except OSError:
            return
        is_admin = uid == 0 or _in_group(uid, gid, "orbis-admin")
        can_read = is_admin or _in_group(uid, gid, "orbis")
        for raw in self.rfile:
            if len(raw) > 1 << 20:
                break
            try:
                req = json.loads(raw)
                rid, method, params = req.get("id"), req["method"], req.get("params") or {}
            except (json.JSONDecodeError, KeyError, TypeError):
                self._send({"id": None, "ok": False, "error": "bad request"})
                continue
            if method not in READ_METHODS | WRITE_METHODS:
                self._send({"id": rid, "ok": False, "error": f"unknown method {method}"})
                continue
            if (method in WRITE_METHODS and not is_admin) or not can_read:
                self._send({"id": rid, "ok": False, "error": "permission denied"})
                continue
            fn = getattr(st, "m_" + method.replace(".", "_"))
            try:
                res = fn(params, (pid, uid, gid, is_admin))
                self._send({"id": rid, "ok": True, "result": res})
            except Exception as exc:  # noqa: BLE001
                self._send({"id": rid, "ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def _send(self, obj):
        self.wfile.write((json.dumps(obj, default=str) + "\n").encode())
        self.wfile.flush()


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(sock_path=None, state: State | None = None) -> "Server":
    sock_path = str(sock_path or paths.daemon_socket())
    os.makedirs(os.path.dirname(sock_path), exist_ok=True)
    try:
        os.unlink(sock_path)
    except FileNotFoundError:
        pass
    srv = Server(sock_path, Handler)
    srv.state = state or State()  # type: ignore[attr-defined]
    os.chmod(sock_path, 0o660)
    return srv


def serve(sock_path=None, state: State | None = None) -> None:
    sock_path = str(sock_path or paths.daemon_socket())
    os.makedirs(os.path.dirname(sock_path), exist_ok=True)
    try:
        os.unlink(sock_path)
    except FileNotFoundError:
        pass
    srv = Server(sock_path, Handler)
    srv.state = state or State()  # type: ignore[attr-defined]
    try:
        gid = grp.getgrnam("orbis").gr_gid
        os.chown(sock_path, 0, gid)
    except (KeyError, PermissionError):
        pass
    os.chmod(sock_path, 0o660)
    if srv.state.monitor:  # type: ignore[attr-defined]
        threading.Thread(target=srv.state.monitor_loop, daemon=True).start()  # type: ignore[attr-defined]
    print(f"orbisd {__version__} (api {API_VERSION}) listening on {sock_path}", file=sys.stderr, flush=True)
    srv.serve_forever()


def main(argv=None) -> int:
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
