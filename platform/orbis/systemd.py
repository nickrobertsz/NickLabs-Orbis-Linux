"""Thin, testable wrapper around systemctl/loginctl.

`RealSystemd` shells out (with timeouts) to the real tools. `FakeSystemd`
models targets, units, processes and sessions in memory so the mode engine
can be exercised end-to-end in CI without a booted systemd.
"""

from __future__ import annotations

import fnmatch
import json
import subprocess
from dataclasses import dataclass, field


class SystemdError(Exception):
    pass


class Systemd:
    def is_active(self, unit: str) -> str: ...
    def start(self, unit: str, timeout: float = 120) -> None: ...
    def stop(self, unit: str, timeout: float = 120) -> None: ...
    def isolate(self, target: str, timeout: float = 300) -> None: ...
    def list_units(self, patterns: list[str]) -> list[dict]: ...
    def show(self, unit: str, props: list[str]) -> dict: ...
    def cgroup_pids(self, unit: str) -> list[int]: ...
    def sessions(self) -> list[dict]: ...
    def terminate_session(self, sid: str) -> None: ...
    def reboot(self) -> None: ...
    def kexec(self) -> None: ...


def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise SystemdError(f"timed out after {timeout:.0f}s: {' '.join(cmd)}") from exc
    except FileNotFoundError as exc:
        raise SystemdError(f"missing tool: {cmd[0]}") from exc
    return r


class RealSystemd(Systemd):
    def __init__(self, systemctl: str = "systemctl", loginctl: str = "loginctl"):
        self.systemctl = systemctl
        self.loginctl = loginctl

    def _ctl(self, *args: str, timeout: float = 60, check: bool = True) -> str:
        r = _run([self.systemctl, "--no-pager", "--no-ask-password", *args], timeout)
        if check and r.returncode != 0:
            raise SystemdError(f"systemctl {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
        return r.stdout

    def is_active(self, unit: str) -> str:
        r = _run([self.systemctl, "is-active", unit], 15)
        return (r.stdout.strip() or "unknown").splitlines()[0]

    def start(self, unit: str, timeout: float = 120) -> None:
        self._ctl("start", unit, timeout=timeout)

    def stop(self, unit: str, timeout: float = 120) -> None:
        self._ctl("stop", unit, timeout=timeout)

    def isolate(self, target: str, timeout: float = 300) -> None:
        self._ctl("isolate", target, timeout=timeout)

    def list_units(self, patterns: list[str]) -> list[dict]:
        if not patterns:
            return []
        out = self._ctl("list-units", "--all", "--plain", "--no-legend", "--output=json",
                        *patterns, check=False)
        try:
            data = json.loads(out or "[]")
            return [{"unit": u["unit"], "active": u["active"], "sub": u["sub"]} for u in data]
        except (json.JSONDecodeError, KeyError, TypeError):
            # Older systemd: no JSON output; parse plain columns.
            res = []
            for line in out.splitlines():
                cols = line.split(None, 4)
                if len(cols) >= 4:
                    res.append({"unit": cols[0], "active": cols[2], "sub": cols[3]})
            return res

    def show(self, unit: str, props: list[str]) -> dict:
        out = self._ctl("show", unit, *[f"--property={p}" for p in props], check=False)
        res = {}
        for line in out.splitlines():
            k, _, v = line.partition("=")
            res[k] = v
        return res

    def cgroup_pids(self, unit: str) -> list[int]:
        cg = self.show(unit, ["ControlGroup"]).get("ControlGroup", "")
        if not cg:
            return []
        try:
            with open(f"/sys/fs/cgroup{cg}/cgroup.procs") as fh:
                return [int(x) for x in fh.read().split()]
        except OSError:
            return []

    def sessions(self) -> list[dict]:
        r = _run([self.loginctl, "list-sessions", "--no-legend", "--json=short"], 15)
        try:
            raw = json.loads(r.stdout or "[]")
        except json.JSONDecodeError:
            raw = [{"session": l.split()[0]} for l in r.stdout.splitlines() if l.strip()]
        out = []
        for s in raw:
            sid = str(s.get("session"))
            props = {}
            rr = _run([self.loginctl, "show-session", sid, "-p", "Type", "-p", "Class",
                       "-p", "Seat", "-p", "Name", "-p", "Service", "-p", "Leader"], 15)
            for line in rr.stdout.splitlines():
                k, _, v = line.partition("=")
                props[k] = v
            out.append({"id": sid, "user": props.get("Name"), "type": props.get("Type"),
                        "class": props.get("Class"), "seat": props.get("Seat"),
                        "service": props.get("Service")})
        return out

    def terminate_session(self, sid: str) -> None:
        r = _run([self.loginctl, "terminate-session", sid], 30)
        if r.returncode != 0:
            raise SystemdError(f"terminate-session {sid}: {r.stderr.strip()}")

    def reboot(self) -> None:
        self._ctl("reboot", timeout=30)

    def kexec(self) -> None:
        self._ctl("kexec", timeout=30)


# --------------------------------------------------------------------------
@dataclass
class FakeUnit:
    name: str
    wanted_by: set[str] = field(default_factory=set)
    active: bool = False
    pids: list[int] = field(default_factory=list)
    stop_refuses: bool = False      # simulate a unit that will not stop
    session: dict | None = None     # unit that spawns a logind session


class FakeSystemd(Systemd):
    """Deterministic in-memory model used by tests and `orbis --simulate`."""

    def __init__(self, units: dict[str, FakeUnit], base_targets: set[str] | None = None):
        self.units = units
        self.base = base_targets or {"multi-user.target"}
        self.log: list[str] = []
        self._sessions: dict[str, dict] = {}
        self._next_pid = 1000
        self.rebooted = False
        self.kexeced = False

    def _alloc(self) -> int:
        self._next_pid += 1
        return self._next_pid

    def _closure(self, target: str) -> set[str]:
        want = {target} | self.base
        changed = True
        while changed:
            changed = False
            for u in self.units.values():
                if u.name not in want and u.wanted_by & want:
                    want.add(u.name)
                    changed = True
        return want

    def _activate(self, name: str) -> None:
        u = self.units.setdefault(name, FakeUnit(name))
        if not u.active:
            u.active = True
            if not name.endswith(".target"):
                u.pids = [self._alloc()]
            if u.session is not None:
                sid = str(len(self._sessions) + 1)
                self._sessions[sid] = dict(u.session, id=sid, unit=name)
            self.log.append(f"start {name}")

    def _deactivate(self, name: str) -> None:
        u = self.units.get(name)
        if u and u.active:
            if u.stop_refuses:
                self.log.append(f"stop {name} (refused, pids remain)")
                u.active = False
                return
            u.active = False
            u.pids = []
            for sid in [s for s, v in self._sessions.items() if v.get("unit") == name]:
                del self._sessions[sid]
            self.log.append(f"stop {name}")

    def is_active(self, unit: str) -> str:
        u = self.units.get(unit)
        return "active" if (u and u.active) else "inactive"

    def start(self, unit: str, timeout: float = 120) -> None:
        for n in sorted(self._closure(unit)):
            self._activate(n)

    def stop(self, unit: str, timeout: float = 120) -> None:
        self._deactivate(unit)

    def isolate(self, target: str, timeout: float = 300) -> None:
        keep = self._closure(target)
        for n in sorted(self.units):
            if n not in keep:
                self._deactivate(n)
        for n in sorted(keep):
            self._activate(n)
        self.log.append(f"isolate {target}")

    def list_units(self, patterns: list[str]) -> list[dict]:
        out = []
        for n, u in sorted(self.units.items()):
            if any(fnmatch.fnmatch(n, p) for p in patterns):
                out.append({"unit": n, "active": "active" if u.active else "inactive",
                            "sub": "running" if u.active else "dead"})
        return out

    def show(self, unit: str, props: list[str]) -> dict:
        u = self.units.get(unit)
        return {"ActiveState": "active" if (u and u.active) else "inactive",
                "MainPID": str(u.pids[0]) if (u and u.pids) else "0"}

    def cgroup_pids(self, unit: str) -> list[int]:
        u = self.units.get(unit)
        return list(u.pids) if u else []

    def sessions(self) -> list[dict]:
        return [dict(v) for v in self._sessions.values()]

    def terminate_session(self, sid: str) -> None:
        s = self._sessions.pop(sid, None)
        self.log.append(f"terminate-session {sid}")
        if s and s.get("unit") in self.units:
            self._deactivate(s["unit"])

    def reboot(self) -> None:
        self.rebooted = True
        self.log.append("reboot")

    def kexec(self) -> None:
        self.kexeced = True
        self.log.append("kexec")


def default_fake() -> FakeSystemd:
    """Unit graph mirroring platform/systemd/*.target in this repository."""
    U = FakeUnit
    units = {
        "multi-user.target": U("multi-user.target"),
        "sshd.service": U("sshd.service", {"multi-user.target"}),
        "NetworkManager.service": U("NetworkManager.service", {"multi-user.target"}),
        "orbisd.service": U("orbisd.service", {"multi-user.target"}),
        "orbis-web.socket": U("orbis-web.socket", {"multi-user.target"}),
        "orbis-pc.target": U("orbis-pc.target"),
        "orbis-gaming.target": U("orbis-gaming.target"),
        "orbis-ai.target": U("orbis-ai.target"),
        "graphical.target": U("graphical.target", {"orbis-pc.target"}),
        "display-manager.service": U("display-manager.service", {"graphical.target"},
                                     session={"user": "nick", "type": "x11", "class": "user",
                                              "seat": "seat0", "service": "lightdm"}),
        "orbis-gaming-session.service": U("orbis-gaming-session.service", {"orbis-gaming.target"},
                                          session={"user": "nick", "type": "x11",
                                                   "class": "user", "seat": "seat0",
                                                   "service": "orbis-gaming"}),
        "orbis-llama.service": U("orbis-llama.service", {"orbis-ai.target"}),
        "orbis-ai-telemetry.service": U("orbis-ai-telemetry.service", {"orbis-ai.target"}),
    }
    return FakeSystemd(units)
